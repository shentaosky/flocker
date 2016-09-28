# Copyright ClusterHQ Inc.  See LICENSE file for details.

"""
Tests for ``flocker.control._persistence``.
"""
import os
import string

from uuid import uuid4, UUID

import time

import subprocess
from docker import Client
from eliot.testing import (
    validate_logging, assertHasMessage, assertHasAction)

from hypothesis import strategies as st
from hypothesis.extra.datetime import datetimes

from twisted.internet import reactor
from twisted.python.filepath import FilePath

from pyrsistent import pset
from testtools import skipUnless

from flocker.testtools import AsyncTestCase
from flocker.control._persistence import (
    ConfigurationPersistenceService, wire_decode, wire_encode,
    _LOG_SAVE, _LOG_STARTUP, _LOG_UNCHANGED_DEPLOYMENT_NOT_SAVED,
    EtcdConfigurationStore, _CONFIG_VERSION
    )
from flocker.control._model import (
    Deployment, Application, DockerImage, Node, Dataset, Manifestation,
    AttachedVolume, Configuration, Port, Link, Leases, Lease,
    )

DATASET = Dataset(dataset_id=u'4e7e3241-0ec3-4df6-9e7c-3f7e75e08855',
                  metadata={u"name": u"myapp"})
NODE_UUID = UUID(u'ab294ce4-a6c3-40cb-a0a2-484a1f09521c')
MANIFESTATION = Manifestation(dataset=DATASET, primary=True)
TEST_DEPLOYMENT = Deployment(
    leases=Leases(),
    nodes=[Node(uuid=NODE_UUID,
                applications=[
                    Application(
                        name=u'myapp',
                        image=DockerImage.from_string(u'postgresql:7.6'),
                        volumes=[AttachedVolume(
                            manifestation=MANIFESTATION,
                            mountpoint=FilePath(b"/xxx/yyy"))]
                    )],
                manifestations={DATASET.dataset_id: MANIFESTATION})])


def createConfigurationPersistentService(reactor,
        ip=b"0.0.0.0", port=4001, path=None):
    """
    Create a service to persist configuration.

    :param testcase: An instance of the TestCase.
    :param reactor: Reactor to use for thread pool.
    :param ip: Host address.
    :param port: Export port.
    :param FilePath path: Directory where desired deployment will be
            persisted.

    :return: Return an instance of Service.
    """
    if path == None:
        path = FilePath(b"/transwarp.io/storage/flocker")
    store = EtcdConfigurationStore(ip, port, path)
    return ConfigurationPersistenceService(reactor, store=store)


class ImagePullTimeOut(Exception):
    """
    Transwarp_etcd Pull Image '172.16.1.41:5000/etcd:20150703-01' Time Out
    Exception.
    """


@skipUnless(os.path.exists(b"/var/run/docker.sock"), "Please install docker "
            "first")
class EtcdConfigurationPersistenceServiceTests(AsyncTestCase):
    """
    Tests for ``ConfigurationPersistenceService``.
    """
    _client = None

    def turnOffContainer(self):
        """
        Close docker container.
        """
        if self._client != None:
            self._client.kill(self.container)
            self._client = None

    def startUpContainer(self):
        """
        Run a etcd docker container.
        """
        if self._client == None:
            self._client = Client(base_url='unix://var/run/docker.sock')
            response = self._client.images(
                '172.16.1.45/jenkins/etcd:live')
            if response == []:
                arguments = ["docker", "pull", "172.16.1.45/jenkins/etcd:live"]
                process = subprocess.Popen(arguments)
                process.wait()
                time.sleep(2)
            self.container = self._client.create_container(
                image='172.16.1.45/jenkins/etcd:live',
                command='etcd --listen-client-urls http://0.0.0.0:14001 '
                        '--advertise-client-urls http://0.0.0.0:14001'
            )
            self._client.start(container=self.container)
            result_container = self._client.inspect_container(self.container)
            self._ipaddress = result_container['NetworkSettings']['IPAddress']

    def service(self, path=None, logger=None):
        """
        Start a service, schedule its stop.

        :param FilePath path: Where to store data.
        :param logger: Optional eliot ``Logger`` to set before startup.

        :return: Started ``ConfigurationPersistenceService``.
        """
        self.startUpContainer()
        self.addCleanup(self.turnOffContainer)
        service = createConfigurationPersistentService(reactor,
                                                       ip=self._ipaddress,
                                                       path=path)
        if logger is not None:
            self.patch(service, "logger", logger)
        service.startService()
        self.addCleanup(service.stopService)
        return service

    def test_empty_on_start(self):
        """
        If no configuration was previously saved, starting a service results
        in an empty ``Deployment``.
        """
        service = self.service()
        self.assertEqual(service.get(), Deployment(nodes=frozenset()))

    def test_file_is_created(self):
        """
        If no configuration file exists in the given path, it is created.
        """
        service = self.service()
        self.assertTrue(service.getStore().exists(service.getStore()._path))

    def test_current_configuration_unchanged(self):
        """
        A persisted configuration saved in the latest configuration
        version is not upgraded and therefore remains unchanged on
        service startup.
        """
        service = self.service()
        persisted_configuration = Configuration(
            version=_CONFIG_VERSION, deployment=TEST_DEPLOYMENT)
        service.getStore().set(wire_encode(persisted_configuration))
        loaded_configuration = service.getStore().get()
        self.assertEqual(wire_decode(loaded_configuration),
                         persisted_configuration)

    @validate_logging(assertHasAction, _LOG_SAVE, succeeded=True,
                      startFields=dict(configuration=TEST_DEPLOYMENT))
    def test_save_then_get(self, logger):
        """
        A configuration that was saved can subsequently retrieved.
        """
        service = self.service(logger=logger)
        logger.reset()
        d = service.save(TEST_DEPLOYMENT)
        d.addCallback(lambda _: service.get())
        d.addCallback(self.assertEqual, TEST_DEPLOYMENT)
        return d

    @validate_logging(assertHasMessage, _LOG_STARTUP,
                      fields=dict(configuration=TEST_DEPLOYMENT))
    def test_persist_across_restarts(self, logger):
        """
        A configuration that was saved can be loaded from a new service.
        """
        path = FilePath(self.mktemp())
        self.startUpContainer()
        self.addCleanup(self.turnOffContainer)
        service = createConfigurationPersistentService(reactor,
                                                       ip=self._ipaddress,
                                                       path=path)
        service.startService()
        d = service.save(TEST_DEPLOYMENT)
        self.addCleanup(service.stopService)

        def retrieve_in_new_service(_):
            new_service = self.service(path, logger)
            self.assertEqual(new_service.get(), TEST_DEPLOYMENT)
        d.addCallback(retrieve_in_new_service)
        return d

    def test_register_for_callback(self):
        """
        Callbacks can be registered that are called every time there is a
        change saved.
        """
        service = self.service()
        callbacks = []
        callbacks2 = []
        service.register(lambda: callbacks.append(1))
        d = service.save(TEST_DEPLOYMENT)

        def saved(_):
            service.register(lambda: callbacks2.append(1))
            changed = TEST_DEPLOYMENT.transform(
                ("nodes",), lambda nodes: nodes.add(Node(uuid=uuid4())),
            )
            return service.save(changed)
        d.addCallback(saved)

        def saved_again(_):
            self.assertEqual((callbacks, callbacks2), ([1, 1], [1]))
        d.addCallback(saved_again)
        return d

    @validate_logging(
        lambda test, logger:
        test.assertEqual(len(logger.flush_tracebacks(ZeroDivisionError)), 1))
    def test_register_for_callback_failure(self, logger):
        """
        Failed callbacks don't prevent later callbacks from being called.
        """
        service = self.service(logger=logger)
        callbacks = []
        service.register(lambda: 1/0)
        service.register(lambda: callbacks.append(1))
        d = service.save(TEST_DEPLOYMENT)

        def saved(_):
            self.assertEqual(callbacks, [1])
        d.addCallback(saved)
        return d

    @validate_logging(assertHasMessage, _LOG_UNCHANGED_DEPLOYMENT_NOT_SAVED)
    def test_callback_not_called_for_unchanged_deployment(self, logger):
        """
        If the old deployment and the new deployment are equivalent, registered
        callbacks are not called.
        """
        service = self.service(logger=logger)

        state = []

        def callback():
            state.append(None)

        saving = service.save(TEST_DEPLOYMENT)

        def saved_old(ignored):
            service.register(callback)
            return service.save(TEST_DEPLOYMENT)
        saving.addCallback(saved_old)

        def saved_new(ignored):
            self.assertEqual(
                [], state,
                "Registered callback was called; should not have been."
            )

        saving.addCallback(saved_new)
        return saving

    def test_success_returned_for_unchanged_deployment(self):
        """
        ``save`` returns a ``Deferred`` that fires with ``None`` when called
        with a deployment that is the same as the already-saved deployment.
        """
        service = self.service()

        old_saving = service.save(TEST_DEPLOYMENT)

        def saved_old(ignored):
            new_saving = service.save(TEST_DEPLOYMENT)
            new_saving.addCallback(self.assertEqual, None)
            return new_saving

        old_saving.addCallback(saved_old)
        return old_saving

    def get_hash(self, service):
        """
        Get the configuration, doing some sanity checks along the way.

        :param service: A ``ConfigurationPersistenceService``.
        :return: Result of ``service.configuration_hash()``.
        """
        # Repeatable:
        result1 = service.configuration_hash()
        result2 = service.configuration_hash()
        self.assertEqual(result1, result2)
        # Bytes:
        self.assertIsInstance(result1, bytes)
        return result1

    def test_hash_on_startup(self):
        """
        An empty configuration can be hashed.
        """
        service = self.service()
        # Hash can be retrieved and passes sanity check:
        self.get_hash(service)

    def test_hash_on_save(self):
        """
        The configuration hash changes when a new version is saved.
        """
        service = self.service()
        original = self.get_hash(service)
        d = service.save(TEST_DEPLOYMENT)

        def saved(_):
            updated = self.get_hash(service)
            self.assertNotEqual(updated, original)
        d.addCallback(saved)
        return d

    def test_hash_persists_across_restarts(self):
        """
        A configuration that was saved can be loaded from a new service.
        """
        service = self.service()
        d = service.save(TEST_DEPLOYMENT)

        def saved(_):
            original = self.get_hash(service)
            service.stopService()
            service.startService()
            self.assertEqual(self.get_hash(service), original)
        d.addCallback(saved)
        return d


DATASETS = st.builds(
    Dataset,
    dataset_id=st.uuids(),
    maximum_size=st.integers(),
)

# `datetime`s accurate to seconds
DATETIMES_TO_SECONDS = datetimes().map(lambda d: d.replace(microsecond=0))

LEASES = st.builds(
    Lease, dataset_id=st.uuids(), node_id=st.uuids(),
    expiration=st.one_of(
        st.none(),
        DATETIMES_TO_SECONDS
    )
)

# Constrain primary to be True so that we don't get invariant errors from Node
# due to having two differing manifestations of the same dataset id.
MANIFESTATIONS = st.builds(
    Manifestation, primary=st.just(True), dataset=DATASETS)
IMAGES = st.builds(DockerImage, tag=st.text(), repository=st.text())
NONE_OR_INT = st.one_of(
    st.none(),
    st.integers()
)
ST_PORTS = st.integers(min_value=1, max_value=65535)
PORTS = st.builds(
    Port,
    internal_port=ST_PORTS,
    external_port=ST_PORTS
)
LINKS = st.builds(
    Link,
    local_port=ST_PORTS,
    remote_port=ST_PORTS,
    alias=st.text(alphabet=string.letters, min_size=1)
)
FILEPATHS = st.text(alphabet=string.printable).map(FilePath)
VOLUMES = st.builds(
    AttachedVolume, manifestation=MANIFESTATIONS, mountpoint=FILEPATHS)
APPLICATIONS = st.builds(
    Application, name=st.text(), image=IMAGES,
    # A MemoryError will likely occur without the max_size limits on
    # Ports and Links. The max_size value that will trigger memory errors
    # will vary system to system. 10 is a reasonable test range for realistic
    # container usage that should also not run out of memory on most modern
    # systems.
    ports=st.sets(PORTS, max_size=10),
    links=st.sets(LINKS, max_size=10),
    volumes=st.sets(VOLUMES, max_size=10),
    environment=st.dictionaries(keys=st.text(), values=st.text()),
    memory_limit=NONE_OR_INT,
    cpu_shares=NONE_OR_INT,
    running=st.booleans()
)


def _build_node(applications):
    # All the manifestations in `applications`.
    app_manifestations = set(
        volume.manifestation for app in applications
            for volume in app.volumes
    )
    # A set that contains all of those, plus an arbitrary set of
    # manifestations.
    dataset_ids = frozenset(
        volume.manifestation.dataset_id
        for app in applications
            for volume in app.volumes
    )
    manifestations = (
        st.sets(MANIFESTATIONS.filter(
            lambda m: m.dataset_id not in dataset_ids))
        .map(pset)
        .map(lambda ms: ms.union(app_manifestations))
        .map(lambda ms: dict((m.dataset.dataset_id, m) for m in ms)))
    return st.builds(
        Node, uuid=st.uuids(),
        applications=st.just(applications),
        manifestations=manifestations)


def validate_app(app):
    if not app.volumes:
        return app
    else:
        for volume in app.volumes:
            if volume.manifestation.dataset_id:
                return app
    return None
