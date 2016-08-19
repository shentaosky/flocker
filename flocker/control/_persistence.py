# Copyright ClusterHQ Inc.  See LICENSE file for details.

"""
Persistence of cluster configuration.
"""

from json import dumps, loads, JSONEncoder
from uuid import UUID
from calendar import timegm
from datetime import datetime
from hashlib import sha256
from zope.interface import Interface

from ..common.thirdpackage.transwarp_etcd.common import EtcdConnectionFailed \
    ,EtcdKeyNotFound

from ..common.thirdpackage.transwarp_etcd.client import Client
import time

from eliot import Logger, write_traceback, MessageType, Field, ActionType

from pyrsistent import PRecord, PVector, PMap, PSet, pmap, PClass

from pytz import UTC

from twisted.python.filepath import FilePath
from twisted.application.service import Service, MultiService
from twisted.internet.defer import succeed
from twisted.internet.task import LoopingCall

from zope.interface.declarations import implementer
from ._model import SERIALIZABLE_CLASSES, Deployment, Configuration

# The class at the root of the configuration tree.
ROOT_CLASS = Deployment


# Serialization marker storing the class name:
_CLASS_MARKER = u"$__class__$"

# The latest configuration version. Configuration versions are
# always integers.
_CONFIG_VERSION = 3

# Map of serializable class names to classes
_CONFIG_CLASS_MAP = {cls.__name__: cls for cls in SERIALIZABLE_CLASSES}


class ConfigurationMigrationError(Exception):
    """
    Error raised when a configuration migration is unable to
    complete successfully.
    """


class MissingMigrationError(Exception):
    """
    Error raised when a configuration migration method cannot be found.
    """
    def __init__(self, source_version, target_version):
        """
        Initialize a missing migration exception.

        :param int source_version: The version to migrate from.
        :param int target_version: The version to migrate to.
        """
        self.source_version = source_version
        self.target_version = target_version
        self.message = (
            u"Unable to find a migration path for a version {source} "
            u"to version {target} configuration. No migration method "
            u"upgrade_from_v{source} could be found.".format(
                source=self.source_version, target=self.target_version
            )
        )
        super(MissingMigrationError, self).__init__(self.message)


def migrate_configuration(source_version, target_version,
                          config, migration_class):
    """
    Migrate a persisted configuration from one version to another
    in sequential upgrades, e.g. a source version of 1 and target
    version of 3 will perform two upgrades, from version 1 to 2,
    followed by 2 to 3.

    Calls the correct ``migration_class`` class methods for
    sequential upgrades between the suppled source and target versions.

    :param int source_version: The version to migrate from.
    :param int target_version: The version to migrate to.
    :param bytes config: The source configuration blob.
    :param class migration_class: The class containing the methods
        that will be used for migration.

    :return bytes: The updated configuration blob after migration.
    :raises MissingMigrationError: Raises this exception if any of the
        required upgrade methods cannot be found in the supplied migration
        class, before attempting to execute any upgrade paths.
    """
    upgraded_config = config
    current_version = source_version
    migrations_sequence = []
    for upgrade_version in range(source_version + 1, target_version + 1):
        with _LOG_UPGRADE(configuration=upgraded_config,
                          source_version=current_version,
                          target_version=upgrade_version):
            migration_method = u"upgrade_from_v%d" % current_version
            migration = getattr(migration_class, migration_method, None)
            if migration is None:
                raise MissingMigrationError(current_version, upgrade_version)
            migrations_sequence.append(migration)
            current_version += 1
    for migration in migrations_sequence:
        upgraded_config = migration(upgraded_config)
    return upgraded_config


class ConfigurationMigration(object):
    """
    Migrate a JSON configuration from one version to another.
    """
    @classmethod
    def upgrade_from_v1(cls, config):
        """
        Migrate a v1 JSON configuration to v2.

        :param bytes config: The v1 JSON data.
        :return bytes: The v2 JSON data.
        """
        v1_config = loads(config)
        v2_config = {
            _CLASS_MARKER: u"Configuration",
            u"version": 2,
            u"deployment": v1_config
        }
        return dumps(v2_config)

    @classmethod
    def upgrade_from_v2(cls, config):
        """
        Migrate a v2 JSON configuration to v3.

        :param bytes config: The v3 JSON data.
        :return bytes: The v3 JSON data.
        """
        decoded_config = loads(config)
        decoded_config[u"version"] = 3
        decoded_config[u"deployment"][u"leases"] = {
            u"values": [], _CLASS_MARKER: u"PMap",
        }
        return dumps(decoded_config)


class _ConfigurationEncoder(JSONEncoder):
    """
    JSON encoder that can encode the configuration model.
    Base encoder for version 1 configurations.
    """
    def default(self, obj):
        if isinstance(obj, PRecord):
            result = dict(obj)
            result[_CLASS_MARKER] = obj.__class__.__name__
            return result
        elif isinstance(obj, PClass):
            result = obj.evolver().data
            result[_CLASS_MARKER] = obj.__class__.__name__
            return result
        elif isinstance(obj, PMap):
            return {_CLASS_MARKER: u"PMap", u"values": dict(obj).items()}
        elif isinstance(obj, (PSet, PVector, set)):
            return list(obj)
        elif isinstance(obj, FilePath):
            return {_CLASS_MARKER: u"FilePath",
                    u"path": obj.path.decode("utf-8")}
        elif isinstance(obj, UUID):
            return {_CLASS_MARKER: u"UUID",
                    "hex": unicode(obj)}
        elif isinstance(obj, datetime):
            if obj.tzinfo is None:
                raise ValueError(
                    "Datetime without a timezone: {}".format(obj))
            return {_CLASS_MARKER: u"datetime",
                    "seconds": timegm(obj.utctimetuple())}
        return JSONEncoder.default(self, obj)


def wire_encode(obj):
    """
    Encode the given model object into bytes.

    :param obj: An object from the configuration model, e.g. ``Deployment``.
    :return bytes: Encoded object.
    """
    return dumps(obj, cls=_ConfigurationEncoder)


def wire_decode(data):
    """
    Decode the given model object from bytes.

    :param bytes data: Encoded object.
    """
    def decode(dictionary):
        class_name = dictionary.get(_CLASS_MARKER, None)
        if class_name == u"FilePath":
            return FilePath(dictionary.get(u"path").encode("utf-8"))
        elif class_name == u"PMap":
            return pmap(dictionary[u"values"])
        elif class_name == u"UUID":
            return UUID(dictionary[u"hex"])
        elif class_name == u"datetime":
            return datetime.fromtimestamp(dictionary[u"seconds"], UTC)
        elif class_name in _CONFIG_CLASS_MAP:
            dictionary = dictionary.copy()
            dictionary.pop(_CLASS_MARKER)
            return _CONFIG_CLASS_MAP[class_name].create(dictionary)
        else:
            return dictionary

    return loads(data, object_hook=decode)


def to_unserialized_json(obj):
    """
    Convert a wire encodeable object into structured Python objects that
    are JSON serializable.

    :param obj: An object that can be passed to ``wire_encode``.
    :return: Python object that can be JSON serialized.
    """
    # Worst implementation everrrr:
    return loads(wire_encode(obj))

_DEPLOYMENT_FIELD = Field(u"configuration", to_unserialized_json)
_LOG_STARTUP = MessageType(u"flocker-control:persistence:startup",
                           [_DEPLOYMENT_FIELD])
_LOG_SAVE = ActionType(u"flocker-control:persistence:save",
                       [_DEPLOYMENT_FIELD], [])

_UPGRADE_SOURCE_FIELD = Field.for_types(
    u"source_version", [int], u"Configuration version to upgrade from.")
_UPGRADE_TARGET_FIELD = Field.for_types(
    u"target_version", [int], u"Configuration version to upgrade to.")
_LOG_UPGRADE = ActionType(u"flocker-control:persistence:migrate_configuration",
                          [_DEPLOYMENT_FIELD, _UPGRADE_SOURCE_FIELD,
                           _UPGRADE_TARGET_FIELD, ], [])
_LOG_EXPIRE = MessageType(
    u"flocker-control:persistence:lease-expired",
    [Field(u"dataset_id", unicode), Field(u"node_id", unicode)],
    u"A lease for a dataset has expired.")

_LOG_UNCHANGED_DEPLOYMENT_NOT_SAVED = MessageType(
    u"flocker-control:persistence:unchanged-deployment-not-saved",
    [],
    u"The persistence service was told to save a deployment which is the same "
    u"as the already-saved deployment.  It has optimized this away."
)


class LeaseService(Service):
    """
    Manage leases.
    In particular, clear out expired leases once a second.

    :ivar _reactor: A ``IReactorTime`` provider.
    :ivar _persistence_service: The persistence service to act with.
    :ivar _lc: A ``twisted.internet.task.LoopingCall`` run every second
        to update the configured leases by releasing leases that have
        expired.
    """
    def __init__(self, reactor, persistence_service):
        self._reactor = reactor
        self._persistence_service = persistence_service

    def startService(self):
        self._lc = LoopingCall(self._expire)
        self._lc.clock = self._reactor
        self._lc.start(1)

    def stopService(self):
        self._lc.stop()

    def _expire(self):
        now = datetime.fromtimestamp(self._reactor.seconds(), tz=UTC)

        def expire(leases):
            updated_leases = leases.expire(now)
            for dataset_id in set(leases) - set(updated_leases):
                _LOG_EXPIRE(dataset_id=dataset_id,
                            node_id=leases[dataset_id].node_id).write()
            return updated_leases
        return update_leases(expire, self._persistence_service)


def update_leases(transform, persistence_service):
    """
    Update the leases configuration in the persistence service.

    :param transform: A function to execute on the currently configured
        leases to manipulate their state.
    :param persistence_service: The persistence service to which the
        updated configuration will be saved.

    :return Deferred: Fires with the new ``Leases`` instance when the
        persistence service has saved.
    """
    config = persistence_service.get()
    new_config = config.set("leases", transform(config.leases))
    d = persistence_service.save(new_config)
    d.addCallback(lambda _: new_config.leases)
    return d


class IConfigurationStore(Interface):
    """
    Provide the interface to save and obtain the configuration.
    Initiate configuration.
    """
    def get(self):
        """
        :return: reuturn the configuration.
        """

    def set(self, value):
        """
        Save the configuration with value.

        :param value: the content of configuration.
        """

    def start(self):
        """
        Check out the path exists and recreate a directory.
        """

    def setLogger(self, logger):
        """
        Set up a logger.
        """


@implementer(IConfigurationStore)
class FileConfigurationStore(object):
    """
    Persistent configuration to local disk and obtain the configuration from
    json file.

    :ivar FilePath _path: the path of configuration saved.
    :ivar FilePath _config_path: a full path and filename of configuration.
    """
    def __init__(self, path):
        self._path = path
        self._config_path = path.child(b"current_configuration.json")

    def get(self):
        return self._config_path.getContent()

    def set(self, value):
        self._config_path.setContent(value)

    def exists(self, path):
        """
        Test the path is exists or not.

        :param path: Path.
        :return: True or false.
        """
        return path.exists()

    def start(self):
        if not self.exists(self._path):
            self._path.makedirs()
        # Version 1 configurations are a special case. They do not store
        # any version information in the configuration data itself, rather they
        # can only be identified by the use of the file name
        # current_configuration.v1.json
        # Therefore we check for a version 1 configuration file and if it is
        # found, the config is upgraded, written to current_configuration.json
        # and the old file archived as current_configuration.v1.old.json
        self._process_v1_config()

    def setLogger(self, logger):
        self._logger=logger

    def _process_v1_config(self):
        """
        Check if a v1 configuration file exists and upgrade it if necessary.
        After upgrade, the v1 configuration file is retained with an archived
        file name, which ensures the data is not lost but we do not override
        a newer configuration version next time the service starts.
        """
        file_name = b"current_configuration.v1.json"
        archive_name = b"current_configuration.v1.old.json"
        v1_config_path = self._path.child(file_name)
        v1_archived_path = self._path.child(archive_name)
        # Check for a v1 config and upgrade to latest if found.
        if v1_config_path.exists():
            v1_json = v1_config_path.getContent()
            with _LOG_UPGRADE(self._logger,
                              configuration=v1_json,
                              source_version=1,
                              target_version=_CONFIG_VERSION):
                updated_json = migrate_configuration(
                    1, _CONFIG_VERSION, v1_json,
                    ConfigurationMigration
                )
                self._config_path.setContent(updated_json)
                v1_config_path.moveTo(v1_archived_path)


class EtcdUnreachable(Exception):
    """
    Transwarp_etcd Unreachable Exception.
    """

@implementer(IConfigurationStore)
class EtcdConfigurationStore(object):
    """
    Persistent configuration to transwarp_etcd and obtain the configuration
    from etcd.

    :ivar _path: the node of configuration saved.
    :ivar _config_path: a full node and filename of configuration.
    """
    def __init__(self, ip=b"0.0.0.0", port=None, path=FilePath(
        b"/transwarp.io/storage/flocker")):
        if isinstance(path, str):
            path = FilePath(path)
        if port == None:
            self._client = Client(host=self.parseHosts(ip),
                                  allow_reconnect=True)
        else:
            self._client = Client(host=ip, port=int(port))
        self._path = path
        self._config_path = path.child(b"current_configuration")

    def get(self):
        return self._client.read(self._config_path.path).value

    def set(self, value):
        self._client.write(self._config_path.path, value)

    def start(self):
        steps = 100
        while True:
            try:
                self._client.stats['id']
            except EtcdConnectionFailed:
                time.sleep(0.1)
                steps -= 1
                if steps < 0:
                    raise EtcdUnreachable
                continue
            break
        if not self.exists(self._path):
            self._client.write(self._path.path, 0, None, 1)

    def exists(self, path):
        """
        Test the path is exists or not.

        :param path: Path.
        :return: True or false.
        """
        try:
            self._client.read(path.path)
        except EtcdKeyNotFound:
            return 0
        return 1

    def setLogger(self, logger):
        self._logger = logger

    def parseHosts(self, hostname):
        """
        Parse multi-host connection address and demand of the format of
        "etcd-store-ip" is "host1:port1,host2:port2,...".

        :param hostname: A string of "etcd-store-ip" in options.
        :return: The tuple of hosts.
        """
        res = []
        hosts = hostname.split(',')
        for host in hosts:
            [addr, port] = host.split(':')
            res.append([addr, int(port)])
        return tuple(res)


class ConfigurationPersistenceService(MultiService):
    """
    Persist configuration to disk, and load it back.

    :ivar Deployment _deployment: The current desired deployment configuration.
    :ivar bytes _hash: A SHA256 hash of the configuration.
    """
    logger = Logger()

    def __init__(self, reactor,
                 store=FileConfigurationStore(FilePath(b"/var/lib/flocker"))):
        """
        :param reactor: Reactor to use for thread pool.
        :param store: Object to act persisting the configuration.
        """
        self._store = store
        MultiService.__init__(self)
        self._change_callbacks = []
        LeaseService(reactor, self).setServiceParent(self)

    def startService(self):
        self._store.setLogger(self.logger)
        self._store.start()
        self.load_configuration()
        MultiService.startService(self)
        _LOG_STARTUP(configuration=self.get()).write(self.logger)

    def load_configuration(self):
        """
        Load the persisted configuration, upgrading the configuration format
        if an older version is detected.
        """
        # We can now safely attempt to detect and process a >v1 configuration
        # file as normal.
        if self._store.exists(self._store._config_path):
            config_json = self._store.get()
            if config_json != "" and config_json != None:
                config_dict = loads(config_json)
                config_version = config_dict['version']
                if config_version < _CONFIG_VERSION:
                    with _LOG_UPGRADE(self.logger,
                                      configuration=config_json,
                                      source_version=config_version,
                                      target_version=_CONFIG_VERSION):
                        config_json = migrate_configuration(
                            config_version, _CONFIG_VERSION,
                            config_json, ConfigurationMigration)
                config = wire_decode(config_json)
                self._deployment = config.deployment
                self._sync_save(config.deployment)
                return
        self._deployment = Deployment()
        self._sync_save(self._deployment)

    def _sync_save(self, deployment):
        """
        Save and flush new configuration to disk synchronously.
        """
        config = Configuration(version=_CONFIG_VERSION, deployment=deployment)
        data = wire_encode(config)
        self._hash = sha256(data).hexdigest()
        self._store.set(data)

    def configuration_hash(self):
        """
        :return bytes: A hash of the configuration.
        """
        return self._hash

    def register(self, change_callback):
        """
        Register a function to be called whenever the configuration changes.

        :param change_callback: Callable that takes no arguments, will be
            called when configuration changes.
        """
        self._change_callbacks.append(change_callback)

    def save(self, deployment):
        """
        Save and flush new deployment to disk.

        :return Deferred: Fires when write is finished.
        """
        if deployment == self._deployment:
            _LOG_UNCHANGED_DEPLOYMENT_NOT_SAVED().write(self.logger)
            return succeed(None)

        with _LOG_SAVE(self.logger, configuration=deployment):
            self._sync_save(deployment)
            self._deployment = deployment
            # At some future point this will likely involve talking to a
            # distributed system (e.g. ZooKeeper or transwarp_etcd), so the API doesn't
            # guarantee immediate saving of the data.
            for callback in self._change_callbacks:
                try:
                    callback()
                except:
                    # Second argument will be ignored in next Eliot release, so
                    # not bothering with particular value.
                    write_traceback(self.logger, u"")
            return succeed(None)

    def get(self):
        """
        Retrieve current configuration.

        It should not be mutated.

        :return Deployment: The current desired configuration.
        """
        return self._deployment

    def getStore(self):
        """
        :return: Store: The object of IConfigurationStore.
        """
        return self._store
