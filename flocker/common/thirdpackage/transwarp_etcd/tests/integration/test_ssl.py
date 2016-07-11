import logging
import os
import tempfile
from twisted.python.runtime import platform
from unittest import skipUnless

from executor import which

from ...common import EtcdConnectionFailed, EtcdException
from ...client import Client

from . import helpers
from . import test_simple

log = logging.getLogger()

class TestEncryptedAccess(test_simple.EtcdIntegrationTest):
    @skipUnless(which(b"docker-compose"), b"docker-compose" + " not installed")
    @skipUnless(platform.isLinux(), "docker-compose only works on Linux")
    def setUp(self):
        program = self._get_exe()
        self.directory = tempfile.mkdtemp(prefix='python-etcd')

        self.ca_cert_path = os.path.join(self.directory, 'ca.crt')
        ca_key_path = os.path.join(self.directory, 'ca.key')

        self.ca2_cert_path = os.path.join(self.directory, 'ca2.crt')
        ca2_key_path = os.path.join(self.directory, 'ca2.key')

        server_cert_path = os.path.join(self.directory, 'server.crt')
        server_key_path = os.path.join(self.directory, 'server.key')

        ca, ca_key = helpers.TestingCA.create_test_ca_certificate(
            self.ca_cert_path, ca_key_path, 'TESTCA')

        ca2, ca2_key = helpers.TestingCA.create_test_ca_certificate(
            self.ca2_cert_path, ca2_key_path, 'TESTCA2')

        self.processHelper = helpers.EtcdProcessHelper(
            self.directory,
            proc_name=program,
            port_range_start=6001,
            internal_port_range_start=8001,
            tls=True
        )
        self.processHelper.run(number=0)
        ip = self.processHelper.ipaddr
        helpers.TestingCA.create_test_certificate(
            ca, ca_key, server_cert_path, server_key_path, ip)

        self.processHelper.run(number=3,
                               proc_args={
                                   '-cert-file': server_cert_path,
                                   '-key-file': server_key_path
                               })
        self.ipaddr = self.processHelper.schema + self.processHelper.ipaddr
        self.ip = self.processHelper.ipaddr

    def test_get_set_unauthenticated_with_ca(self):
        """
        INTEGRATION: try unauthenticated with validation (https->https)
        """
        client = Client(host=self.ip,
                        protocol='https', port=6001, ca_cert=self.ca2_cert_path)

        self.assertRaises(EtcdConnectionFailed, client.set, '/test-set', 'test-key')
        self.assertRaises(EtcdConnectionFailed, client.get, '/test-set')

    def test_get_set_authenticated(self):
        """
        INTEGRATION: set/get a new value authenticated
        """
        client = Client(host=self.ip,
                        port=6001, protocol='https', ca_cert=self.ca_cert_path)

        set_result = client.set('/test_set', 'test-key')
        get_result = client.get('/test_set')


class TestClientAuthenticatedAccess(test_simple.EtcdIntegrationTest):
    @skipUnless(which(b"docker-compose"), b"docker-compose" + " not installed")
    @skipUnless(platform.isLinux(), "docker-compose only works on Linux")
    def setUp(self):
        program = self._get_exe()
        self.directory = tempfile.mkdtemp(prefix='python-etcd')

        self.ca_cert_path = os.path.join(self.directory, 'ca.crt')
        ca_key_path = os.path.join(self.directory, 'ca.key')

        server_cert_path = os.path.join(self.directory, 'server.crt')
        server_key_path = os.path.join(self.directory, 'server.key')

        self.client_cert_path = os.path.join(self.directory, 'client.crt')
        self.client_key_path = os.path.join(self.directory, 'client.key')

        self.client_all_cert = os.path.join(self.directory, 'client-all.crt')

        ca, ca_key = helpers.TestingCA.create_test_ca_certificate(
            self.ca_cert_path, ca_key_path, 'etcd_network')

        helpers.TestingCA.create_test_certificate(
            ca,
            ca_key,
            self.client_cert_path,
            self.client_key_path)

        self.processHelper = helpers.EtcdProcessHelper(
            self.directory,
            proc_name=program,
            port_range_start=6001,
            internal_port_range_start=8001,
            tls=True
        )

        with open(self.client_all_cert, 'w') as f:
            with open(self.client_key_path, 'r') as g:
                f.write(g.read())
            with open(self.client_cert_path, 'r') as g:
                f.write(g.read())

        self.processHelper.run(number=3,
                              proc_args={
                                  '-cert-file': server_cert_path,
                                  '-key-file': server_key_path,
                                  '-ca-file': self.ca_cert_path
                              })
        self.ipaddr = self.processHelper.schema + self.processHelper.ipaddr
        self.ip = self.processHelper.ipaddr

    def test_get_set_unauthenticated(self):
        """
        INTEGRATION: set/get a new value unauthenticated (http->https)
        """
        client = Client(host=self.ip, port=6001)

        # See above for the reason of this change
        self.assertRaises(
            EtcdException, client.set, '/test_set', 'test-key')
        self.assertRaises(EtcdException, client.get, '/test_set')
