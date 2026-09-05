"""Document processing backend for Any2Book."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("any2book-processors")
except PackageNotFoundError:
    __version__ = "0+unknown"
