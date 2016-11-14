import json
import shutil
import subprocess
import logging
import time
import hashlib
import uuid

from OpenSSL import crypto
from flocker.control.functional.test_persistence_etcd import ETCD_IMAGE

class EtcdProcessHelper(object):
    def __init__(
            self,
            directory=None,
            proc_name='docker-compose',
            port_range_start=4001,
            internal_port_range_start=7001,
            cluster=False,
            tls=False
    ):

        self.directory = directory
        self.proc_name = proc_name
        self.port_range_start = port_range_start
        self.internal_port_range_start = internal_port_range_start
        self.cluster = cluster
        self.schema = 'http://'
        self.compose_args = 'etcd_network:\n \
                  container_name: etcd_test_network\n \
                  image: %s\n \
                  privileged: true\n \
                  hostname: etcd_network\n \
                  command:\n \
                    - /bin/bash\n \
                    - -c\n \
                    - "while true; do sleep 1; echo `date`;done"\n' % ETCD_IMAGE
        if tls:
            self.schema = 'https://'

    def command_run(self, arguments, logger):
        process = subprocess.Popen(arguments, stderr=subprocess.PIPE,
                                   stdin=subprocess.PIPE)
        error = process.communicate(input=self.compose_args)
        logger.debug('Started %d' % process.pid)
        logger.debug('Params: %s' % self.compose_args)
        time.sleep(2)
        arguments = ["docker", "inspect", "etcd_test_network"]
        process_getip = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         stdin=subprocess.PIPE)
        logger.debug('Started %d' % process_getip.pid)
        logger.debug('Params: %s' % arguments)
        output = process_getip.stdout.read()
        status = process_getip.wait()
        time.sleep(2)
        res = json.loads(output)
        self.ipaddr = res[0]['NetworkSettings']['Networks']['bridge']['IPAddress']

    def run(self, number=1, proc_args={}):
        cluster_members = ''
        for i in range(0, number):
            member = 'node%d=http://etcd_network:%d,' % (i, self.internal_port_range_start + i)
            cluster_members += member
        cluster_members = cluster_members[:-1]
        for i in range(0, number):
            self.add_one(i, cluster_members, proc_args)

        log = logging.getLogger()
        arguments = ["%s" % self.proc_name, "-f", "-", "up", "-d"]
        self.command_run(arguments=arguments, logger=log)
        log.debug('Created directory %s' % self.directory)

    def stop(self):
        log = logging.getLogger()
        arguments = ["%s" % self.proc_name, "-f", "-", "kill"]
        kill_process = subprocess.Popen(arguments, stderr=subprocess.PIPE,
                                        stdin=subprocess.PIPE)
        output, error = kill_process.communicate(input=self.compose_args)
        if self.directory != None:
            shutil.rmtree(self.directory)
        time.sleep(2)
        log.debug('Kill etcd cluster pid:%d' % kill_process.pid)
        log.debug('Delete directorty %s' % self.directory)

    def add_one(self, slot, cluster_members, proc_args=None):
        client = '%setcd_network:%d' % (self.schema, self.port_range_start + slot)
        peer = '%setcd_network:%d' % ('http://', self.internal_port_range_start
                                      + slot)
        token = 'etcd-cluster-test'

        self.compose_args += '\netcd%d:\n \
              container_name: etcd_test_%d\n \
              image: %s\n \
              net: "container:etcd_network"\n \
              volumes:\n \
                - %s:%s\n \
              command:\n \
                - etcd\n \
                - --name\n \
                - node%d\n \
                - --data-dir\n \
                - /var/tmp/etcd/test\n \
                - --listen-client-urls\n \
                - %s\n \
                - --advertise-client-urls\n \
                - %s\n \
                - --listen-peer-urls\n \
                - %s\n \
                - --initial-advertise-peer-urls\n \
                - %s\n \
                - --initial-cluster\n \
                - %s\n \
                - --initial-cluster-token\n \
                - %s\n \
                - --initial-cluster-state\n \
                - new\n' % (slot, slot, ETCD_IMAGE, self.directory, self.directory, slot,
                            client, client, peer, peer, cluster_members, token)

        for key in proc_args.keys():
            self.compose_args += '                 - -%s\n \
                - %s\n' % (key, proc_args[key])

    def kill_one(self, slot):
        log = logging.getLogger()
        arguments = ["%s" % self.proc_name, "-f", "-", "kill", "etcd%d" % slot]
        kill_process = subprocess.Popen(arguments, stderr=subprocess.PIPE,
                                        stdin=subprocess.PIPE)
        output, error = kill_process.communicate(input=self.compose_args)
        time.sleep(1)
        log.debug('Killed etcd pid:%d', kill_process.pid)


class TestingCA(object):

    @classmethod
    def create_test_ca_certificate(self, cert_path, key_path, cn=None):
        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 4096)
        cert = crypto.X509()

        if not cn:
            serial = uuid.uuid4().int
        else:
            md5_hash = hashlib.md5()
            md5_hash.update(cn.encode('utf-8'))
            serial = int(md5_hash.hexdigest(), 36)
            cert.get_subject().CN = cn

        cert.get_subject().C = "ES"
        cert.get_subject().ST = "State"
        cert.get_subject().L = "City"
        cert.get_subject().O = "Organization"
        cert.get_subject().OU = "Organizational Unit"
        cert.set_serial_number(serial)
        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(315360000)
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(k)
        cert.add_extensions([
            crypto.X509Extension("basicConstraints".encode('ascii'), False,
                                 "CA:TRUE".encode('ascii')),
            crypto.X509Extension("keyUsage".encode('ascii'), False,
                                 "keyCertSign, cRLSign".encode('ascii')),
            crypto.X509Extension("subjectKeyIdentifier".encode('ascii'), False,
                                 "hash".encode('ascii'),
                                 subject=cert),
        ])

        cert.add_extensions([
            crypto.X509Extension(
                "authorityKeyIdentifier".encode('ascii'), False,
                "keyid:always".encode('ascii'), issuer=cert)
        ])

        cert.sign(k, 'sha1')

        with open(cert_path, 'w') as f:
            f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert)
                    .decode('utf-8'))

        with open(key_path, 'w') as f:
            f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k)
                    .decode('utf-8'))

        return cert, k

    @classmethod
    def create_test_certificate(self, ca, ca_key, cert_path, key_path, cn=None):
        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 4096)
        cert = crypto.X509()

        if not cn:
            serial = uuid.uuid4().int
        else:
            md5_hash = hashlib.md5()
            md5_hash.update(cn.encode('utf-8'))
            serial = int(md5_hash.hexdigest(), 36)
            cert.get_subject().CN = cn

        cert.get_subject().C = "ES"
        cert.get_subject().ST = "State"
        cert.get_subject().L = "City"
        cert.get_subject().O = "Organization"
        cert.get_subject().OU = "Organizational Unit"

        cert.add_extensions([
            crypto.X509Extension(
                "keyUsage".encode('ascii'),
                False,
                "nonRepudiation,digitalSignature,keyEncipherment".encode('ascii')),
            crypto.X509Extension(
                "extendedKeyUsage".encode('ascii'),
                False,
                "clientAuth,serverAuth".encode('ascii')),
            crypto.X509Extension(
                "subjectAltName".encode('ascii'),
                False,
                "IP: 127.0.0.1".encode('ascii')),
        ])

        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(315360000)
        cert.set_issuer(ca.get_subject())
        cert.set_pubkey(k)
        cert.set_serial_number(serial)
        cert.sign(ca_key, 'sha1')

        with open(cert_path, 'w') as f:
            f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert)
                    .decode('utf-8'))

        with open(key_path, 'w') as f:
            f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k)
                    .decode('utf-8'))
