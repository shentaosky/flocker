"""
Test create flocker pvc set
"""
import json
import random
import string
from uuid import UUID, uuid4

from .testtools import require_k8s_cluster
from ..testtools import create_dataset
from ...testtools import AsyncTestCase


def create_test_pvcset(name, replicas):
    """
    create request body of pvcset

    :param name: name of PersistentVolumeClaim
    :param replicas: replicas of PersistentVolumeClaim

    :return unicode request body, request body send to apiserver
    """
    return json.dumps({
        "kind": "PersistentVolumeClaimSet",
        "apiVersion": "v1",
        "metadata": {
            "name": name,
            "labels": {
                "name": name
            }
        },
        "spec": {
            "replicas": replicas,
            "selector": {
                "name": name
            },
            "template": {
                "metadata": {
                    "labels": {
                        "name": name
                    },
                    "annotations": {
                        "volume.alpha.kubernetes.io/storage-class": "silver"
                    }
                },
                "spec": {
                    "accessModes": [
                        "ReadWriteOnce"
                    ],
                    "resources": {
                        "requests": {
                            "storage": "3Gi"
                        }
                    }
                }
            }
        },
    })


def create_test_rc(name, flocker_name, replicas):
    """
    Create test rc

    :param name: name of PersistentVolumeClaim
    :param flocker_name: name of flocker pvcset
    :param replicas: replicas of replication controller

    :return unicode request body, request body send to apiserver
    """
    return json.dumps({
        "kind": "ReplicationController",
        "apiVersion": "v1",
        "metadata": {
            "name": name,
            "labels": {
                "name": name
            }
        },
        "spec": {
            "replicas": replicas,
            "selector": {
                "name": name
            },
            "template": {
                "metadata": {
                    "labels": {
                        "name": name
                    }
                },
                "spec": {
                    "volumes": [
                        {
                            "name": "flockerdir",
                            "persistentVolumeClaim": {
                                "selector": {
                                    "name": flocker_name
                                }
                            }
                        }
                    ],
                    "containers": [
                        {
                            "name": name,
                            "image": "172.16.1.41:5000/jenkins/transwarp-docker:testing-latest",
                            "args": [
                                "/bin/bash",
                                "-c",
                                ("echo -e 'cat /var/transwarp/test.txt:';" +
                                 "cat /var/transwarp/test.txt; " +
                                 "echo -e '\n'cat `hostname` to /var/transwarp/test.txt;" +
                                 "echo `hostname` >> /var/transwarp/test.txt; " +
                                 "while true; do sleep 10;done")
                            ],
                            "resources": {},
                            "volumeMounts": [
                                {
                                    "name": "flockerdir",
                                    "mountPath": "/var/transwarp"
                                }
                            ],
                            "imagePullPolicy": "IfNotPresent"
                        }
                    ],
                    "restartPolicy": "OnFailure",
                    "terminationGracePeriodSeconds": 30,
                    "dnsPolicy": "ClusterFirst",
                }
            }
        },
    })


def id_generator(prefix, size=4, chars=string.ascii_lowercase + string.digits):
    """
    Generate id
    :param prefix: prefix used to generate string
    :param size: random suffix size
    :param chars: random suffix char candidates

    :return generate name

    """
    suffix = "".join([random.choice(chars) for _ in range(size)])
    return "{}-{}".format(prefix, suffix)


class FlockerPVCCreateTest(AsyncTestCase):
    """
    Test create flocker pvc set
    requirements:
    1. k8s cluster with flocker
    """

    @require_k8s_cluster(1)
    def test_create_pvcset(self, k8s_cluster):
        """
        test creation of pvcset
        1. submit pvcset yml
        2. verify pv and pvc successfully created
        3. flocker pv is lazycreate pending
        """
        name = id_generator("flockercreate")
        pvcset = create_test_pvcset(name, 1)
        k8s_cluster.create_pvcset(pvcset)
        self.addCleanup(k8s_cluster.delete_pvcset, pvcset)
        return k8s_cluster.check_pvcset_provision(1, {"name": name})

    @require_k8s_cluster(1)
    def test_bound_pvcset(self, k8s_cluster):
        """
        test bound of pvcset
        1. submit pvcset yml
        2. verify pv and pvc successfully created
        3. flocker pv is lazycreate pending
        4. bind pvc to pod
        5. check provision of pvc
        """
        name = id_generator("boundpvcset")
        pvcset = create_test_pvcset(name, 1)
        rc = create_test_rc(name, name, 1)
        k8s_cluster.create_pvcset(pvcset)
        self.addCleanup(k8s_cluster.delete_pvcset, pvcset)
        k8s_cluster.create_replication_controllers(rc)
        self.addCleanup(k8s_cluster.delete_rc, rc)
        return k8s_cluster.check_rc_provision(1, {"name": name})

    @require_k8s_cluster(1)
    def test_rebind_pvcset(self, k8s_cluster):
        """
        test move of dataset
        1. create pvc
        2. create rc and bind pvc
        3. delete rc
        4. create new rc and bind pvc
        5. check
        """
        flocker_name = id_generator("rebindpvcset")
        name1 = id_generator("rebindpvcset")
        name2 = id_generator("rebindpvcset")
        pvcset = create_test_pvcset(flocker_name, 1)
        rc1 = create_test_rc(name1, flocker_name, 1)
        rc2 = create_test_rc(name2, flocker_name, 1)
        k8s_cluster.create_pvcset(pvcset)
        self.addCleanup(k8s_cluster.delete_pvcset, pvcset)
        k8s_cluster.create_replication_controllers(rc1)
        d = k8s_cluster.check_rc_provision(1, {"name": name1})
        d.addCallback(lambda ignore: k8s_cluster.delete_rc(rc1))
        d.addCallback(lambda ignore: k8s_cluster.create_replication_controllers(rc2))
        d.addCallback(lambda ignore: self.addCleanup(k8s_cluster.delete_rc, rc2))
        return d.addCallback(lambda ignore: k8s_cluster.check_rc_provision(1, {"name": name2}))

    @require_k8s_cluster(3)
    def test_bound_multi_pvcsets(self, k8s_cluster):
        """
        test bound of pvcset
        1. submit pvcset yml
        2. verify pv and pvc successfully created
        3. flocker pv is lazycreate pending
        4. bind pvc to pod
        5. check provision of pvc
        """
        name = id_generator("boundmultipvcsets")
        pvcset = create_test_pvcset(name, 10)
        rc = create_test_rc(name, name, 10)
        k8s_cluster.create_pvcset(pvcset)
        self.addCleanup(k8s_cluster.delete_pvcset, pvcset)
        k8s_cluster.create_replication_controllers(rc)
        self.addCleanup(k8s_cluster.delete_rc, rc)
        return k8s_cluster.check_rc_provision(10, {"name": name})

    @require_k8s_cluster(3)
    def test_dataset_move(self, k8s_cluster):
        """
        A dataset can be moved from one node to another.

        All attributes, including the maximum size, are preserved.
        """
        flocker_cluster = k8s_cluster.flocker_cluster
        dataset_id = unicode(uuid4())
        node_nums = len(flocker_cluster.nodes)
        waiting_for_create = create_dataset(self, flocker_cluster,
                                            metadata={u'storagetype': u'silver'},
                                            dataset_id=dataset_id)
        self.addCleanup(flocker_cluster.client.delete_dataset, bytes(dataset_id))

        # Once created, request to move the dataset to node2
        def move_dataset(dataset, i):
            dataset_moving = flocker_cluster.client.move_dataset(
                UUID(flocker_cluster.nodes[i].uuid), dataset.dataset_id)

            # Wait for the dataset to be moved; we expect the state to
            # match that of the originally created dataset in all ways
            # other than the location.
            moved_dataset = dataset.set(
                primary=UUID(flocker_cluster.nodes[i].uuid))
            dataset_moving.addCallback(
                lambda dataset: flocker_cluster.wait_for_dataset(moved_dataset))
            return dataset_moving

        for i in range(0, 2 * node_nums):
            waiting_for_create.addCallback(move_dataset, i % node_nums)

        return waiting_for_create
