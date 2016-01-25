# Copyright ClusterHQ Inc.  See LICENSE file for details.

"""
Unit tests for the implementation of ``flocker-deploy``.
"""

from twisted.python.filepath import FilePath
from twisted.python.usage import UsageError

from ...testtools import (
    make_flocker_script_test, make_standard_options_test, TestCase
)
from ..script import DeployScript, DeployOptions
from ...control.httpapi import REST_API_PORT


class FlockerDeployTests(make_flocker_script_test(
        DeployScript, DeployOptions, u'flocker-deploy'
)):
    """Tests for ``flocker-deploy``."""


CONTROL_HOST = u"192.168.1.1"


class StandardDeployOptionsTests(make_standard_options_test(DeployOptions)):
    """Standard options tests for :class:`DeployOptions`."""


class DeployOptionsTests(TestCase):
    """Tests for :class:`DeployOptions`."""

    def setUp(self):
        self.options = DeployOptions()
        super(DeployOptionsTests, self).setUp()

    def default_port(self):
        """
        The default port to connect to is the REST API port.
        """
        deploy = FilePath(self.mktemp())
        app = FilePath(self.mktemp())

        deploy.setContent(b"{}")
        app.setContent(b"{}")

        self.assertEqual(self.options.parseOptions(
            [CONTROL_HOST, deploy.path, app.path])["port"], REST_API_PORT)

    def test_cannot_mix_ca_path_options(self):
        """
        A ``UsageError`` is raised if the ``certificates-directory`` and any
        of ``cacert``, ``cert`` or ``key`` options are mixed together.
        """
        app = self.mktemp()
        FilePath(app).touch()
        deploy = self.mktemp()
        FilePath(deploy).touch()
        cacert_path = b"/path/to/cluster.crt"
        certificates_directory = b"/path/to/certificates"
        self.options["cacert"] = cacert_path
        self.options["certificates-directory"] = certificates_directory
        exception = self.assertRaises(UsageError, self.options.parseOptions,
                                      [CONTROL_HOST, deploy, app])
        self.assertEqual(
            ("Cannot use --certificates-directory and --cacert options "
             "together. Please specify either certificates directory or "
             "full paths to each file via the --cacert, --cert and --key "
             "options."),
            str(exception)
        )

    def test_ca_must_exist(self):
        """
        A ``UsageError`` is raised if the specified ``cacert`` cluster
        certificate does not exist.
        """
        app = self.mktemp()
        FilePath(app).touch()
        deploy = self.mktemp()
        FilePath(deploy).touch()
        credential_path = b"/path/to/non-existent-cluster.crt"
        self.options["cacert"] = credential_path
        exception = self.assertRaises(UsageError, self.options.parseOptions,
                                      [CONTROL_HOST, deploy, app])
        expected_message = (
            "File /path/to/non-existent-cluster.crt does not exist. "
            "Use the flocker-ca command to create the credential, "
            "or use the --cacert flag to specify the credential location."
        )

        self.assertEqual(
            expected_message,
            str(exception)
        )

    def test_user_cert_must_exist(self):
        """
        A ``UsageError`` is raised if the specified ``cert`` user
        certificate does not exist.
        """
        app = self.mktemp()
        FilePath(app).touch()
        deploy = self.mktemp()
        FilePath(deploy).touch()
        ca = self.mktemp()
        FilePath(ca).touch()
        credential_path = b"/path/to/non-existent-user.crt"
        self.options["cacert"] = ca
        self.options["cert"] = credential_path
        exception = self.assertRaises(UsageError, self.options.parseOptions,
                                      [CONTROL_HOST, deploy, app])
        expected_message = (
            "File /path/to/non-existent-user.crt does not exist. "
            "Use the flocker-ca command to create the credential, "
            "or use the --cert flag to specify the credential location."
        )

        self.assertEqual(
            expected_message,
            str(exception)
        )

    def test_user_key_must_exist(self):
        """
        A ``UsageError`` is raised if the specified ``key`` user
        private key does not exist.
        """
        app = self.mktemp()
        FilePath(app).touch()
        deploy = self.mktemp()
        FilePath(deploy).touch()
        ca = self.mktemp()
        FilePath(ca).touch()
        user_cert = self.mktemp()
        FilePath(user_cert).touch()
        credential_path = b"/path/to/non-existent-user.key"
        self.options["cacert"] = ca
        self.options["cert"] = user_cert
        self.options["key"] = credential_path
        exception = self.assertRaises(UsageError, self.options.parseOptions,
                                      [CONTROL_HOST, deploy, app])
        expected_message = (
            "File /path/to/non-existent-user.key does not exist. "
            "Use the flocker-ca command to create the credential, "
            "or use the --key flag to specify the credential location."
        )

        self.assertEqual(
            expected_message,
            str(exception)
        )

    def test_deploy_must_exist(self):
        """
        A ``UsageError`` is raised if the ``deployment_config`` file does not
        exist.
        """
        app = self.mktemp()
        FilePath(app).touch()
        deploy = b"/path/to/non-existent-file.cfg"
        exception = self.assertRaises(UsageError, self.options.parseOptions,
                                      [CONTROL_HOST, deploy, app])
        self.assertEqual('No file exists at {deploy}'.format(deploy=deploy),
                         str(exception))

    def test_app_must_exist(self):
        """
        A ``UsageError`` is raised if the ``app_config`` file does not
        exist.
        """
        deploy = self.mktemp()
        FilePath(deploy).touch()
        app = b"/path/to/non-existent-file.cfg"
        exception = self.assertRaises(UsageError, self.options.parseOptions,
                                      [CONTROL_HOST, deploy, app])
        self.assertEqual('No file exists at {app}'.format(app=app),
                         str(exception))

    def test_deployment_config_must_be_yaml(self):
        """
        A ``UsageError`` is raised if the supplied deployment
        configuration cannot be parsed as YAML.
        """
        deploy = FilePath(self.mktemp())
        app = FilePath(self.mktemp())

        deploy.setContent(b"{'foo':'bar', 'x':y, '':'")
        app.setContent(b"{}")

        e = self.assertRaises(
            UsageError, self.options.parseOptions,
            [CONTROL_HOST, deploy.path, app.path])

        expected = (
            "Deployment configuration at {path} could not be parsed "
            "as YAML"
        ).format(path=deploy.path)
        self.assertTrue(str(e).startswith(expected))

    def test_application_config_must_be_yaml(self):
        """
        A ``UsageError`` is raised if the supplied application
        configuration cannot be parsed as YAML.
        """
        deploy = FilePath(self.mktemp())
        app = FilePath(self.mktemp())

        deploy.setContent(b"{}")
        app.setContent(b"{'foo':'bar', 'x':y, '':'")

        e = self.assertRaises(
            UsageError, self.options.parseOptions,
            [CONTROL_HOST, deploy.path, app.path])

        expected = (
            "Application configuration at {path} could not be parsed "
            "as YAML"
        ).format(path=app.path)
        self.assertTrue(str(e).startswith(expected))
