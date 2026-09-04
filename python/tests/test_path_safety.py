from pathlib import Path

from any2book_processors.path_safety import path_redirect_component


def test_path_safety_allows_platform_redirects_in_existing_ancestors() -> None:
    candidate = Path("/tmp") / "any2book-nonexistent-parent" / "book.epub"

    assert path_redirect_component(candidate) is None
