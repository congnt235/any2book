from importlib.metadata import version

from any2book_processors import __version__


def test_backend_version_matches_project_metadata() -> None:
    assert __version__ == version("any2book-processors")
