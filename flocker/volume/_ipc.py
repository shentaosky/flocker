# Copyright ClusterHQ Inc.  See LICENSE file for details.

"""
Inter-process communication for the volume manager.

Specific volume managers ("nodes") may wish to push data to other
nodes. In the current iteration this is done over SSH using a blocking
API. In some future iteration this will be replaced with an actual
well-specified communication protocol between daemon processes using
Twisted's event loop (https://clusterhq.atlassian.net/browse/FLOC-154).
"""
import json
from contextlib import contextmanager
from io import BytesIO

from characteristic import with_cmp

from zope.interface import Interface, implementer

from twisted.internet.defer import succeed
from twisted.python.filepath import FilePath

from ..common._ipc import ProcessNode
from ..control._model import StorageType
from .filesystems.zfs import Snapshot
from .service import Volume, VolumeName, deserialize_volume, serialize_volume

# Path to SSH private key available on nodes and used to communicate
# across nodes.
# XXX duplicate of same information in flocker.cli:
# https://clusterhq.atlassian.net/browse/FLOC-390
from ..common._node_config import SSH_PRIVATE_KEY_PATH, DEFAULT_CONFIG_PATH


def standard_node(hostname):
    """
    Create the default production ``INode`` for the given hostname.

    That is, a node that SSHes as root to port 22 on the given hostname
    and authenticates using the cluster private key.

    :param bytes hostname: The host to connect to.
    :return: A ``INode`` that can connect to the given hostname using SSH.
    """
    return ProcessNode.using_ssh(hostname, 2022, b"root", SSH_PRIVATE_KEY_PATH)


class RemoteProcessNode(ProcessNode):
    """
    ProcessNode using ssh
    """

    def __init__(self, hostname, port=2022, username=b"root", private_key=SSH_PRIVATE_KEY_PATH):
        """
        :param hostname: remote hostname to be connected
        """
        self.hostname = hostname
        self.port = port
        self.initial_command_arguments = (
            b"ssh",
            b"-q",  # suppress warnings
            b"-i", private_key.path,
            b"-l", username,
            # We're ok with unknown hosts; we'll be switching away from
            # SSH by the time Flocker is production-ready and security is
            # a concern.
            b"-o", b"StrictHostKeyChecking=no",
            # The tests hang if ControlMaster is set, since OpenSSH won't
            # ever close the connection to the test server.
            b"-o", b"ControlMaster=no",
            # Some systems (notably Ubuntu) enable GSSAPI authentication which
            # involves a slow DNS operation before failing and moving on to a
            # working mechanism.  The expectation is that key-based auth will
            # be in use so just jump straight to that.  An alternate solution,
            # explicitly disabling GSSAPI, has cross-version platform and
            # cross-version difficulties (the options aren't always recognized
            # and result in an immediate failure).  As mentioned above, we'll
            # switch away from SSH soon.
            b"-o", b"PreferredAuthentications=publickey",
            b"-p", b"%d" % (port,), hostname)
        ProcessNode.__init__(self, initial_command_arguments=self.initial_command_arguments)

    def run(self, remote_command):
        return ProcessNode.run(self, remote_command)

    def get_output(self, remote_command):
        return ProcessNode.get_output(self, remote_command)


class IRemoteVolumeManager(Interface):
    """
    A remote volume manager with which one can communicate somehow.
    """

    def snapshots(volume):
        """
        Retrieve a list of the snapshots which exist for the given volume.

        :param Volume volume: The volume for which to retrieve snapshots.

        :return: A ``Deferred`` that fires with a ``list`` of ``Snapshot``
            instances giving the snapshot information.  The snapshots are
            ordered from oldest to newest.
        """

    def receive(volume):
        """
        Context manager that returns a file-like object to which a volume's
        contents can be written.

        :param Volume volume: The volume which will be pushed to the
            remote volume manager.

        :return: A file-like object that can be written to, which will
             update the volume on the remote volume manager.
        """

    def acquire(volume):
        """
        Tell the remote volume manager to acquire the given volume.

        :param Volume volume: The volume which will be acquired by the
            remote volume manager.

        :return: The node ID of the remote volume manager (as ``unicode``).
        """

    def clone_to(parent, name):
        """
        Clone a parent volume to a new one with the given name.

        :param Volume parent: The volume to clone.

        :param VolumeName name: The name of the volume to clone to.

        :return: A ``Deferred`` that fires when cloning finished.
        """

    def find_volumes(volume):
        """
        Find matching volumes in target node.

        :param Volume volume: The volume for which to find in target node.

        :return: A ``Deferred`` that fires with a ``list`` of ``Volume``
        """


@implementer(IRemoteVolumeManager)
@with_cmp(["_destination", "_config_path"])
class RemoteVolumeManager(object):
    """
    ``INode``\-based communication with a remote volume manager.
    """

    def __init__(self, destination, config_path=DEFAULT_CONFIG_PATH):
        """
        :param Node destination: The node to push to.
        :param FilePath config_path: Path to configuration file for the
            remote ``flocker-volume``.
        """
        self._destination = destination
        self._config_path = config_path

    def snapshots(self, volume):
        """
        Run ``flocker-volume snapshots`` on the destination and parse the
        output into a ``list`` of ``Snapshot`` instances.
        """
        data = self._destination.get_output(
            [b"flocker-volume",
             b"--config", self._config_path.path,
             b"snapshots",
             volume.node_id.encode("ascii"),
             volume.name.to_bytes(),
             volume.get_storagetype().value]
        )
        return succeed([
                           Snapshot(name=name)
                           for name
                           in data.splitlines()
                           ])

    def receive(self, volume):
        return self._destination.run([b"flocker-volume",
                                      b"--config", self._config_path.path,
                                      b"receive",
                                      volume.node_id.encode(b"ascii"),
                                      volume.name.to_bytes(),
                                      volume.get_storagetype().value])

    def acquire(self, volume):
        return self._destination.get_output(
            [b"flocker-volume",
             b"--config", self._config_path.path,
             b"acquire",
             volume.node_id.encode(b"ascii"),
             volume.name.to_bytes(),
             volume.get_storagetype().value
             ]).decode("ascii")

    def clone_to(self, parent, name):
        return self._destination.get_output(
            [b"flocker-volume",
             b"--config", self._config_path.path,
             b"clone_to",
             parent.node_id.encode(b"ascii"),
             parent.name.to_bytes(),
             name.to_bytes(),
             parent.get_storagetype().value
             ]).decode("ascii")

    def find_volumes(self, volume):
        """
        Run ``flocker-volume find-volumes`` on the destination and parse the
        output into a ``list`` of ``Volumes`` instances.
        """
        data = self._destination.get_output(
            [b"flocker-volume",
             b"--config", self._config_path.path,
             b"find_volumes",
             volume.name.to_bytes(),
             volume.get_storagetype().value]
        )

        volumes = []
        for line in data.splitlines():
            res = line.split("\t", -1)
            volumes.append(Volume(node_id=res[1],
                                  name=VolumeName.from_bytes(res[2]),
                                  storagetype=StorageType.lookupByValue(res[0]),
                                  service=None))
        return succeed(volumes)

    @property
    def destination(self):
        return self._destination


@implementer(IRemoteVolumeManager)
class LocalVolumeManager(object):
    """
    In-memory communication with a ``VolumeService`` instance, for testing.
    """

    def __init__(self, service):
        """
        :param VolumeService service: The service to communicate with.
        """
        self._service = service

    def snapshots(self, volume):
        """
        Interrogate the volume's filesystem for its snapshots.
        """
        return volume.get_filesystem().snapshots()

    @contextmanager
    def receive(self, volume):
        input_file = BytesIO()
        yield input_file
        input_file.seek(0, 0)
        self._service.receive(volume.node_id, volume.name, volume.storagetype, input_file)

    def acquire(self, volume):
        self._service.acquire(volume.node_id, volume.name, volume.storagetype)
        return self._service.node_id

    def clone_to(self, parent, name):
        return self._service.clone_to(parent, name)

    def find_volumes(self, volume):
        return succeed([volume])


class IRemoteVolumeManagerSerde(Interface):
    """
    Serializer and deserializer of RemoteVolumeManager
    """

    def serialize(self, destination, destination_volume):
        """
        Serialize destination and destination_volume into a string
        :param RemoteVolumeManager destination: destination where volume is handoff to
        :param Volume destination_volume: destination volume to be sent

        :return unicode: unicode represent the serialized RemoteVolumeManager and Volume
        """

    def deserialize(self, repr):
        """
        Deserialize unicode representation into RemoteVolumeManager and Volume
        :param unicode repr: unicode representation
        :return return both RemoteVolumeManager and Volume
        """


@implementer(IRemoteVolumeManagerSerde)
class RemoteVolumeManagerSerde(object):
    """
    Implementation of IRemoteVolumeManagerSerde
    """

    def serialize(self, destination, destination_volume):
        return json.dumps({u'dest': destination._destination.hostname, u'volume': serialize_volume(destination_volume)})

    def deserialize(self, repr):
        value = json.loads(repr)
        destination_volume = deserialize_volume(value[u'volume'])
        return RemoteVolumeManager(RemoteProcessNode(value[u'dest'])), destination_volume


@implementer(IRemoteVolumeManagerSerde)
class LocalVolumeManagerSerde(object):
    """
    Implementation of IRemoteVolumeManagerSerde
    """

    def __init__(self):
        self._volume_manager = None
        self._volume = None

    def serialize(self, destination, destination_volume):
        self._volume_manager = destination
        self._volume = destination_volume
        return destination_volume.name.to_bytes()

    def deserialize(self, repr):
        return self._volume_manager, self._volume
