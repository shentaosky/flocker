# Copyright ClusterHQ Inc.  See LICENSE file for details.
# -*- test-case-name: flocker.node.test.test_p2p -*-

"""
Deploy ZFS volumes.
"""

from itertools import chain
from warnings import warn
from datetime import timedelta

from zope.interface import implementer

from characteristic import attributes

from pyrsistent import PClass, field, pmap

from eliot import write_failure, Logger, start_action

from twisted.internet.defer import gatherResults

from . import IStateChange, in_parallel, sequentially

from ..control._model import (
    DatasetChanges, DatasetHandoff, NodeState, Manifestation, Dataset,
    ip_to_uuid, Application, AttachedVolume, DockerImage, DATASET_LAZY_CREATE, DATASET_LAZY_CREATE_PENDING)
from ..volume._ipc import RemoteVolumeManager, RemoteProcessNode, RemoteVolumeManagerSerde
from ..volume._model import VolumeSize
from ..volume.service import VolumeName

from ._deploy import IDeployer, NodeLocalState, NotInUseDatasets
from ._docker import DockerClient, Volume as DockerVolume

_logger = Logger()


def _to_volume_name(dataset_id):
    """
    Convert dataset ID to ``VolumeName`` with ``u"default"`` namespace.

    To be replaced in https://clusterhq.atlassian.net/browse/FLOC-737 with
    real namespace support.

    :param unicode dataset_id: Dataset ID.

    :return: ``VolumeName`` with default namespace.
    """
    return VolumeName(namespace=u"default", dataset_id=dataset_id)


def _eliot_system(part):
    return u"flocker:p2pdeployer:" + part


@implementer(IStateChange)
class CreateDataset(PClass):
    """
    Create a new locally-owned dataset.

    :ivar Dataset dataset: Dataset to create.
    """
    dataset = field(type=Dataset, mandatory=True)

    @property
    def eliot_action(self):
        return start_action(
            _logger, _eliot_system(u"createdataset"),
            dataset_id=self.dataset.dataset_id,
            maximum_size=self.dataset.maximum_size
        )

    def run(self, deployer):
        volume = deployer.volume_service.get(
            name=_to_volume_name(self.dataset.dataset_id),
            size=VolumeSize(maximum_size=self.dataset.maximum_size),
            storagetype=self.dataset.get_storagetype()
        )
        return deployer.volume_service.create(volume)


@implementer(IStateChange)
@attributes(["dataset"])
class ResizeDataset(object):
    """
    Resize an existing locally-owned dataset.

    :ivar Dataset dataset: Dataset to resize.
    """

    @property
    def eliot_action(self):
        return start_action(
            _logger, _eliot_system(u"createdataset"),
            dataset_id=self.dataset.dataset_id,
            maximum_size=self.dataset.maximum_size,
        )

    def run(self, deployer):
        volume = deployer.volume_service.get(
            name=_to_volume_name(self.dataset.dataset_id),
            size=VolumeSize(maximum_size=self.dataset.maximum_size),
            storagetype=self.dataset.get_storagetype()
        )
        return deployer.volume_service.set_maximum_size(volume)


@implementer(IStateChange)
@attributes(["dataset", "hostname"])
class HandoffDataset(object):
    """
    A dataset handoff that needs to be performed from this node to another
    node.

    See :cls:`flocker.volume.VolumeService.handoff` for more details.

    :ivar Dataset dataset: The dataset to hand off.
    :ivar bytes hostname: The hostname of the node to which the dataset is
         meant to be handed off.
    """

    @property
    def eliot_action(self):
        return start_action(
            _logger, _eliot_system(u"handoff"),
            dataset_id=self.dataset.dataset_id,
            hostname=self.hostname,
        )

    def run(self, deployer):
        service = deployer.volume_service
        destination = RemoteProcessNode(hostname=self.hostname)
        return service.handoff(
            service.get(name=_to_volume_name(self.dataset.dataset_id),
                        storagetype=self.dataset.get_storagetype()),
            RemoteVolumeManager(destination),
            RemoteVolumeManagerSerde()
        )


@implementer(IStateChange)
@attributes(["dataset", "hostname"])
class PushDataset(object):
    """
    A dataset push that needs to be performed from this node to another
    node.

    See :cls:`flocker.volume.VolumeService.push` for more details.

    :ivar Dataset: The dataset to push.
    :ivar bytes hostname: The hostname of the node to which the dataset is
         meant to be pushed.
    """

    @property
    def eliot_action(self):
        return start_action(
            _logger, _eliot_system(u"push"),
            dataset_id=self.dataset.dataset_id,
            hostname=self.hostname,
        )

    def run(self, deployer):
        service = deployer.volume_service
        destination = RemoteProcessNode(hostname=self.hostname)
        return service.push(
            service.get(name=_to_volume_name(self.dataset.dataset_id),
                        storagetype=self.dataset.get_storagetype()),
            RemoteVolumeManager(destination))


@implementer(IStateChange)
class DeleteDataset(PClass):
    """
    Delete all local copies of the dataset.

    A better action would be one that deletes a specific manifestation
    ("volume" in flocker.volume legacy terminology). Unfortunately
    currently "remotely owned volumes" (legacy terminology), aka
    non-primary manifestations or replicas, are not exposed to the
    deployer, so we have to enumerate them here.

    :ivar Dataset dataset: The dataset to delete.
    """
    dataset = field(mandatory=True, type=Dataset)

    @property
    def eliot_action(self):
        return start_action(
            _logger, _eliot_system("delete"),
            dataset_id=self.dataset.dataset_id,
        )

    def run(self, deployer):
        service = deployer.volume_service
        d = service.enumerate()

        def got_volumes(volumes):
            deletions = []
            for volume in volumes:
                if volume.name.dataset_id == self.dataset.dataset_id:
                    deletions.append(service.pool.destroy(volume).addErrback(
                        write_failure, _logger, u"flocker:p2pdeployer:delete"))
            return gatherResults(deletions)

        d.addCallback(got_volumes)
        return d


@implementer(IDeployer)
class P2PManifestationDeployer(object):
    """
    Discover and calculate changes for peer-to-peer manifestations (e.g. ZFS)
    on a node.

    :ivar unicode hostname: The hostname of the node that this is running on.
    :ivar VolumeService volume_service: The volume manager for this node.
    """

    def __init__(self, hostname, volume_service, node_uuid=None, docker_client=None):
        if node_uuid is None:
            # To be removed in https://clusterhq.atlassian.net/browse/FLOC-1795
            warn("UUID is required, this is for backwards compat with existing"
                 " tests only. If you see this in production code that's "
                 "a bug.", DeprecationWarning, stacklevel=2)
            node_uuid = ip_to_uuid(hostname)
        self.node_uuid = node_uuid
        self.hostname = hostname
        self.volume_service = volume_service
        # interact with all containers
        if docker_client is None:
            self.docker_client = DockerClient(namespace=b'')
        else:
            self.docker_client = docker_client

    def discover_state(self, cluster_state):
        """
        Discover local ZFS manifestations.
        """
        # Add real namespace support in
        # https://clusterhq.atlassian.net/browse/FLOC-737; for now we just
        # strip the namespace since there will only ever be one.
        volumes = self.volume_service.enumerate()

        def map_volumes_to_size(volumes):
            primary_manifestations = {}
            for volume in volumes:
                path = volume.get_filesystem().get_path()
                primary_manifestations[path] = (
                    volume.name.dataset_id, volume.size.maximum_size, volume.node_id, volume.status)
            return primary_manifestations

        volumes.addCallback(map_volumes_to_size)

        def got_volumes(available_manifestations):
            manifestation_paths = {dataset_id: path for (path, (dataset_id, _, _, _))
                                   in available_manifestations.items()}

            manifestations = {}
            for (dataset_id, maximum_size, node_id, status) in available_manifestations.values():
                primary = False
                if node_id == self.volume_service.node_id:
                    primary = True
                manifestations[dataset_id] = Manifestation(dataset=Dataset(dataset_id=dataset_id,
                                                                           maximum_size=maximum_size,
                                                                           status=status),
                                                           primary=primary)
            containers = self.docker_client.list_sync()
            applications = []

            for container in containers:
                if container.activation_state != "active":
                    continue
                volumes = []
                for volume in container.volumes:
                    if available_manifestations.has_key(volume.node_path):
                        dataset_id, _, _, _ = available_manifestations.get(volume.node_path)
                        volumes.append(
                            AttachedVolume(
                                manifestation=manifestations.get(dataset_id),
                                mountpoint=volume.container_path,
                            )
                        )

                # leave some filed empty, since we not using them
                applications.append(Application(
                    name=container.name,
                    image=DockerImage.from_string(container.container_image),
                    ports=frozenset(),
                    volumes=volumes,
                    environment=None,
                    links=frozenset(),
                    memory_limit=container.mem_limit,
                    cpu_shares=container.cpu_shares,
                    restart_policy=container.restart_policy,
                    running=(container.activation_state == u"active"),
                    command_line=container.command_line)
                )

            d = self.volume_service.enumrate_pool_status()
            def got_status(status):
                return NodeLocalState(
                    node_state=NodeState(
                        uuid=self.node_uuid,
                        hostname=self.hostname,
                        applications=applications,
                        manifestations=manifestations,
                        paths=manifestation_paths,
                        devices={},
                        pool_status=pmap(status),
                    )
                )
            d.addCallback(got_status)

            return d

        volumes.addCallback(got_volumes)
        return volumes

    def calculate_changes(self, configuration, cluster_state, local_state):
        """
        Calculate necessary changes to peer-to-peer manifestations.

        Datasets that are in use by applications cannot be deleted,
        handed-off or resized. See
        https://clusterhq.atlassian.net/browse/FLOC-1425 for leases, a
        better solution.
        """

        local_state_primary = cluster_state.get_node(self.node_uuid)
        # We need to know applications (for now) to see if we should delay
        # deletion or handoffs. Eventually this will rely on leases instead.
        if local_state_primary.applications is None:
            return sequentially(changes=[])

        phases = []

        not_in_use_datasets = NotInUseDatasets(
            node_uuid=self.node_uuid,
            local_applications=local_state_primary.applications,
            leases=configuration.leases,
        )

        # Find any dataset that are moving to or from this node - or
        # that are being newly created by this new configuration.
        dataset_changes = find_dataset_changes(
            self.node_uuid, cluster_state, configuration)

        resizing = not_in_use_datasets(dataset_changes.resizing)
        if resizing:
            phases.append(in_parallel(changes=[
                ResizeDataset(dataset=dataset)
                for dataset in resizing]))

        going = not_in_use_datasets(dataset_changes.going,
                                    lambda d: d.dataset.dataset_id)
        if going:
            phases.append(in_parallel(changes=[
                HandoffDataset(dataset=handoff.dataset,
                               hostname=handoff.hostname)
                for handoff in going]))

        if dataset_changes.creating:
            phases.append(in_parallel(changes=[
                CreateDataset(dataset=dataset)
                for dataset in dataset_changes.creating]))

        deleting = not_in_use_datasets(dataset_changes.deleting)
        if deleting:
            phases.append(in_parallel(changes=[
                DeleteDataset(dataset=dataset)
                for dataset in deleting
                ]))

        obsoleted = not_in_use_datasets(find_obsolete_datasets(desired_state=configuration, local_state=local_state))
        if obsoleted:
            phases.append(in_parallel(changes=[
                DeleteDataset(dataset=dataset)
                for dataset in obsoleted
                ]))

        return sequentially(changes=phases,
                            sleep_when_empty=timedelta(seconds=1))


def find_obsolete_datasets(desired_state, local_state):
    obsoleted_datasets = []

    desired_datasets = {}
    for node in desired_state.nodes:
        for manifestation in node.manifestations.values():
            if manifestation.dataset.deleted:
                desired_datasets[manifestation.dataset_id] = manifestation.dataset

    for manifestation in local_state.node_state.manifestations.values():
        if manifestation.primary is False:
            if manifestation.dataset_id in desired_datasets.keys():
                obsoleted_datasets.append(manifestation.dataset)

    return obsoleted_datasets


def find_dataset_changes(uuid, current_state, desired_state):
    """
    Find what actions need to be taken to deal with changes in dataset
    manifestations between current state and desired state of the cluster.

    XXX The logic here assumes the mountpoints have not changed,
    and will act unexpectedly if that is the case. See
    https://clusterhq.atlassian.net/browse/FLOC-351 for more details.

    XXX The logic here assumes volumes are never added or removed to
    existing applications, merely moved across nodes. As a result test
    coverage for those situations is not implemented. See
    https://clusterhq.atlassian.net/browse/FLOC-352 for more details.

    :param UUID uuid: The uuid of the node for which to find changes.

    :param Deployment current_state: The old state of the cluster on which the
        changes are based.

    :param Deployment desired_state: The new state of the cluster towards which
        the changes are working.

    :return DatasetChanges: Changes to datasets that will be needed in
         order to match desired configuration.
    """
    uuid_to_hostnames = {node.uuid: node.hostname
                         for node in current_state.nodes}
    desired_datasets = {node.uuid:
                            set(manifestation.dataset for manifestation
                                in node.manifestations.values())
                        for node in desired_state.nodes}
    current_datasets = {node.uuid:
                            set(manifestation.dataset for manifestation
                                # We pretend ignorance is equivalent to no
                                # datasets; this is wrong. See FLOC-2060.
                                in (node.manifestations or {}).values())
                        for node in current_state.nodes}
    # lazy creation at controller-node
    local_desired_datasets = set(dataset for dataset in desired_datasets.get(uuid, set())
                                 if dataset.deleted is False)
    local_desired_dataset_ids = set()
    for dataset in local_desired_datasets:
        if DATASET_LAZY_CREATE in dataset.metadata and dataset.metadata[DATASET_LAZY_CREATE] == DATASET_LAZY_CREATE_PENDING:
            continue
        local_desired_dataset_ids.add(dataset.dataset_id)
    # local_desired_dataset_ids = set(dataset.dataset_id for dataset in local_desired_datasets
    #                                 if u"lazycreate" in dataset.metadata)
    local_current_dataset_ids = set(dataset.dataset_id for dataset in
                                    current_datasets.get(uuid, set()))
    remote_current_dataset_ids = set()
    for dataset_node_uuid, current in current_datasets.items():
        if dataset_node_uuid != uuid:
            remote_current_dataset_ids |= set(
                dataset.dataset_id for dataset in current)

    # If a dataset exists locally and is desired anywhere on the cluster, and
    # the desired dataset is a different maximum_size to the existing dataset,
    # the existing local dataset should be resized before any other action
    # is taken on it.
    resizing = set()
    for desired in desired_datasets.values():
        for new_dataset in desired:
            if new_dataset.dataset_id in local_current_dataset_ids:
                for cur_dataset in current_datasets[uuid]:
                    if cur_dataset.dataset_id != new_dataset.dataset_id and new_dataset.deleted is True:
                        continue
                    if cur_dataset.maximum_size != new_dataset.maximum_size:
                        resizing.add(new_dataset)

    # Look at each dataset that is going to be running elsewhere and is
    # currently running here, and add a DatasetHandoff for it to `going`.
    going = set()
    for dataset_node_uuid, desired in desired_datasets.items():
        if dataset_node_uuid != uuid:
            try:
                hostname = uuid_to_hostnames[dataset_node_uuid]
            except KeyError:
                # Apparently we don't know NodeState for this
                # node. Hopefully we'll learn this information eventually
                # but until we do we can't proceed.
                continue
            for dataset in desired:
                if dataset.dataset_id in local_current_dataset_ids:
                    if dataset.deleted is True:
                        continue
                    going.add(DatasetHandoff(
                        dataset=dataset, hostname=hostname))

    # For each dataset that is going to be hosted on this node and did not
    # exist previously, make sure that dataset is in `creating`.
    # Unfortunately the logic for "did not exist previously" is wrong; our
    # knowledge of other nodes' state may be lacking if they are
    # offline. See FLOC-2060.
    creating_dataset_ids = local_desired_dataset_ids.difference(
        local_current_dataset_ids | remote_current_dataset_ids)
    creating = set(dataset for dataset in local_desired_datasets
                   if dataset.dataset_id in creating_dataset_ids)

    deleting = set(dataset for dataset in chain(*desired_datasets.values())
                   if dataset.deleted and dataset.dataset_id in local_current_dataset_ids)
    return DatasetChanges(going=going, deleting=deleting,
                          creating=creating, resizing=resizing)
