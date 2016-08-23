import logging

_log = logging.getLogger(__name__)

# Prevent "no handler" warnings to stderr in projects that do not configure
# logging.
try:
    from logging import NullHandler
except ImportError:
    # Python <2.7, just define it.
    class NullHandler(logging.Handler):
        def emit(self, record):
            pass
_log.addHandler(NullHandler())