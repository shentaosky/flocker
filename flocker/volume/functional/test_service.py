# Copyright ClusterHQ Inc.  See LICENSE file for details.

"""Functional tests for the volume manager service."""

from __future__ import absolute_import

from .._ipc import LocalVolumeManagerSerde, RemoteVolumeManager
from ..service import VolumeName
from ..testtools import create_realistic_servicepair, MutatingProcessNode
from ...testtools import AsyncTestCase, flaky


class RealisticTests(AsyncTestCase):
    """
    Tests for realistic scenarios, used to catch integration issues.
    """

    @flaky(u'FLOC-2767')
    def test_handoff(self):
        """
        Handoff of a previously unpushed volume between two ZFS-based volume
        managers does not fail.
        """
        service_pair = create_realistic_servicepair(self)

        d = service_pair.from_service.create(
            service_pair.from_service.get(
                VolumeName(namespace=u"myns", dataset_id=u"myvolume")
            )
        )

        def created(volume):
            return service_pair.from_service.handoff(
                volume, service_pair.remote, LocalVolumeManagerSerde())
        d.addCallback(created)
        # If the Deferred errbacks the test will fail:
        return d

    def test_handoff_roundtrip(self):
        """
        Handoff of a volume from A to B and then B to A between two ZFS-based
        volume managers does not fail.
        """
        service_pair = create_realistic_servicepair(self)

        d = service_pair.from_service.create(
            service_pair.from_service.get(
                VolumeName(namespace=u"myns", dataset_id=u"myvolume")
            )
        )

        def created(volume):
            return service_pair.from_service.handoff(
                volume, service_pair.remote, LocalVolumeManagerSerde())
        d.addCallback(created)

        def handed_off(volume):
            return service_pair.to_service.handoff(
                service_pair.to_service.get(
                    VolumeName(namespace=u"myns", dataset_id=u"myvolume")),
                RemoteVolumeManager(MutatingProcessNode(service_pair.from_service),
                                    service_pair.from_service._config_path),
                LocalVolumeManagerSerde())
        d.addCallback(handed_off)
        # If the Deferred errbacks the test will fail:
        return d
