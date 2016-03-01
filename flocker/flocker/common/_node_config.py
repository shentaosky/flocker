"""
Config options for flocker node

"""
from twisted.python.filepath import FilePath

DEFAULT_CONFIG_PATH = FilePath(b"/etc/flocker/volume.json")
FLOCKER_MOUNTPOINT = FilePath(b"/flocker")
FLOCKER_POOL = b"flocker"

DEFAULT_AGENT_CONFIGFILE = b"/etc/flocker/agent.yml"
AGENT_CONFIG = FilePath(DEFAULT_AGENT_CONFIGFILE)

SSH_PRIVATE_KEY_PATH = FilePath(b"/etc/flocker/id_rsa_flocker")
