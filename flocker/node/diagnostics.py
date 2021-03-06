# Copyright ClusterHQ Inc.  See LICENSE file for details.

"""
A script to export Flocker log files and system information.
"""

from gzip import open as gzip_open
import os
from platform import dist as platform_dist
import re
from shutil import copyfileobj, make_archive, rmtree
from socket import gethostname
from subprocess import check_call, check_output
from uuid import uuid1

from pyrsistent import PClass, field

from flocker import __version__


def gzip_file(source_path, archive_path):
    """
    Create a gzip compressed archive of ``source_path`` at ``archive_path``.
    An empty archive file will be created if the source file does not exist.
    This gives the diagnostic archive a consistent set of files which can
    easily be tested.
    """
    with gzip_open(archive_path, 'wb') as archive:
        if os.path.isfile(source_path):
            with open(source_path, 'rb') as source:
                copyfileobj(source, archive)


def list_hardware():
    """
    List the hardware on the local machine.

    :returns: ``bytes`` JSON encoded hardware inventory of the current host.
    """
    with open(os.devnull, 'w') as devnull:
        return check_output(
            ['lshw', '-quiet', '-json'],
            stderr=devnull
        )


class FlockerDebugArchive(object):
    """
    Create a tar archive containing:
    * Flocker version,
    * logs from all installed Flocker services,
    * some or all of the syslog depending on the logging system,
    * Docker version and configuration information, and
    * a list of all the services installed on the system and their status.
    """
    def __init__(self, service_manager, log_exporter):
        """
        :param service_manager: An API for listing installed services.
        :param log_exporter: An API for exporting logs for services.
        """
        self._service_manager = service_manager
        self._log_exporter = log_exporter

        self._suffix = unicode(uuid1())
        self._archive_name = "clusterhq_flocker_logs_{}".format(
            self._suffix
        )
        self._archive_path = os.path.abspath(self._archive_name)

    def _logfile_path(self, name):
        """
        Generate a path to a file inside the archive directory.

        :param str name: A unique label for the file.
        :returns: An absolute path string for a file inside the archive
            directory.
        """
        return os.path.join(
            self._archive_name,
            name,
        )

    def _open_logfile(self, name):
        """
        :param str name: A unique label for the file.
        :return: An open ``file`` object with a name generated by
            `_logfile_path`.
        """
        return open(self._logfile_path(name), 'w')

    def create(self):
        """
        Create the archive by first creating a uniquely named directory in the
        current working directory, adding the log files and debug information,
        creating a ``tar`` archive from the directory and finally removing the
        directory.
        """
        os.makedirs(self._archive_path)
        try:
            # Flocker version
            with self._open_logfile('flocker-version') as output:
                output.write(__version__.encode('utf-8') + b'\n')

            # Flocker logs.
            services = self._service_manager.flocker_services()
            for service_name, service_status in services:
                self._log_exporter.export_flocker(
                    service_name=service_name,
                    target_path=self._logfile_path(service_name)
                )

            # Syslog.
            self._log_exporter.export_all(self._logfile_path('syslog'))

            # Status of all services.
            with self._open_logfile('service-status') as output:
                services = self._service_manager.all_services()
                for service_name, service_status in services:
                    output.write(service_name + " " + service_status + "\n")

            # Docker version
            check_call(
                ['docker', 'version'],
                stdout=self._open_logfile('docker-version')
            )

            # Docker configuration
            check_call(
                ['docker', 'info'],
                stdout=self._open_logfile('docker-info')
            )

            # Kernel version
            self._open_logfile('uname').write(' '.join(os.uname()))

            # Distribution version
            self._open_logfile('os-release').write(
                open('/etc/os-release').read()
            )

            # Network configuration
            check_call(
                ['ip', 'addr'],
                stdout=self._open_logfile('ip-addr')
            )

            # Hostname
            self._open_logfile('hostname').write(gethostname() + '\n')

            # Partition information
            check_call(
                ['fdisk', '-l'],
                stdout=self._open_logfile('fdisk')
            )

            # Block Device and filesystem information
            check_call(
                ['lsblk', '--all'],
                stdout=self._open_logfile('lsblk')
            )

            # Hardware inventory
            self._open_logfile('lshw').write(list_hardware())

            # Create a single archive file
            archive_path = make_archive(
                base_name=self._archive_name,
                format='tar',
                root_dir=os.path.dirname(self._archive_path),
                base_dir=os.path.basename(self._archive_path),
            )
        finally:
            # Attempt to remove the source directory.
            rmtree(self._archive_path)
        return archive_path


class SystemdServiceManager(object):
    """
    List services managed by Systemd.
    """
    def all_services(self):
        """
        Iterate the name and status of all services known to SystemD.
        """
        output = check_output(['systemctl', 'list-unit-files', '--no-legend'])
        for line in output.splitlines():
            line = line.rstrip()
            service_name, service_status = line.split(None, 1)
            yield service_name, service_status

    def flocker_services(self):
        """
        Iterate the name and status of the Flocker services known to SystemD.
        """
        service_pattern = r'^(?P<service_name>flocker-.+)\.service'
        for service_name, service_status in self.all_services():
            match = re.match(service_pattern, service_name)
            if match:
                service_name = match.group('service_name')
                if service_status == 'enabled':
                    yield service_name, service_status


class UpstartServiceManager(object):
    """
    List services managed by Upstart.
    """
    def all_services(self):
        """
        Iterate the name and status of all services known to Upstart.
        """
        for line in check_output(['initctl', 'list']).splitlines():
            service_name, service_status = line.split(None, 1)
            yield service_name, service_status

    def flocker_services(self):
        """
        Iterate the name and status of the Flocker services known to Upstart.
        """
        for service_name, service_status in self.all_services():
            if service_name.startswith('flocker-'):
                yield service_name, service_status


class JournaldLogExporter(object):
    """
    Export logs managed by JournalD.
    """
    def export_flocker(self, service_name, target_path):
        """
        Export logs for ``service_name`` to ``target_path`` compressed using
        ``gzip``.
        """
        # Centos-7 doesn't have separate startup logs.
        open(target_path + '_startup.gz', 'w').close()
        check_call(
            'journalctl --all --output cat --unit {}.service '
            '| gzip'.format(service_name),
            stdout=open(target_path + '_eliot.gz', 'w'),
            shell=True
        )

    def export_all(self, target_path):
        """
        Export all system logs to ``target_path`` compressed using ``gzip``.
        """
        check_call(
            'journalctl --all --boot | gzip',
            stdout=open(target_path + '.gz', 'w'),
            shell=True
        )


class UpstartLogExporter(object):
    """
    Export logs for services managed by Upstart and written by RSyslog.
    """
    def export_flocker(self, service_name, target_path):
        """
        Export logs for ``service_name`` to ``target_path`` compressed using
        ``gzip``.
        """
        files = [
            ("/var/log/upstart/{}.log".format(service_name),
             target_path + '_startup.gz'),
            ("/var/log/flocker/{}.log".format(service_name),
             target_path + '_eliot.gz'),
        ]
        for source_path, archive_path in files:
            gzip_file(source_path, archive_path)

    def export_all(self, target_path):
        """
        Export all system logs to ``target_path`` compressed using ``gzip``.
        """
        gzip_file('/var/log/syslog', target_path + '.gz')


class Distribution(PClass):
    """
    A record of the service manager and log exported to be used on each
    supported Linux distribution.
    :ivar str name: The name of the operating system.
    :ivar str version: The version of the operating system.
    :ivar service_manager: The service manager API to use for this
        operating system.
    :ivar log_exporter: The log exporter API to use for this operating
        system.
    """
    name = field(type=unicode, mandatory=True)
    version = field(type=unicode, mandatory=True)
    service_manager = field(mandatory=True)
    log_exporter = field(mandatory=True)


DISTRIBUTIONS = (
    Distribution(
        name=u'centos',
        version=u'7',
        service_manager=SystemdServiceManager,
        log_exporter=JournaldLogExporter,
    ),
    Distribution(
        name=u'ubuntu',
        version=u'14.04',
        service_manager=UpstartServiceManager,
        log_exporter=UpstartLogExporter,
    )
)


DISTRIBUTION_BY_LABEL = dict(
    ('{}-{}'.format(p.name, p.version), p)
    for p in DISTRIBUTIONS
)


def current_distribution():
    """
    :returns: A ``str`` label for the operating system distribution running
        this script.
    """
    name, version, _ = platform_dist()
    return name.lower() + '-' + version


def lookup_distribution(distribution_label):
    """
    :param str distribution_label: The label of the distribution to lookup.
    :returns: A ``Distribution`` matching the supplied ``distribution_label``
        of ``None`` if the ``distribution_label`` is not supported.
    """
    for label, distribution in DISTRIBUTION_BY_LABEL.items():
        if distribution_label.startswith(label):
            return distribution
