import json
import os
import xml.etree.ElementTree as etree
from pathlib import Path

import pytest
from any2book_processors.models import BookDocument, Chapter
from any2book_processors.reader_html import write_reader_html


def test_reader_html_is_xhtml_and_localizes_assets(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    image = assets / "figure.png"
    image.write_bytes(b"png fixture")
    document = BookDocument(
        schema_version="2",
        metadata={"title": "Reader book", "language": "en"},
        chapters=[Chapter("First", f'<p>Text</p><img src="{image}" alt="Figure" />')],
        assets=["assets/figure.png"],
    )
    root = write_reader_html(document, tmp_path)
    chapter = next((root / "chapters").glob("*.xhtml"))
    etree.parse(chapter)
    value = chapter.read_text(encoding="utf-8")
    assert 'src="../assets/figure.png"' in value
    manifest = json.loads((root / "book.json").read_text(encoding="utf-8"))
    assert manifest["assets"] == ["assets/figure.png"]


def test_reader_html_preserves_assets_with_the_same_basename(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "figure.png"
    second = second_dir / "figure.png"
    first.write_bytes(b"first image")
    second.write_bytes(b"second image")
    document = BookDocument(
        schema_version="2",
        metadata={"title": "Reader book", "language": "en"},
        chapters=[
            Chapter("First", '<img src="first/figure.png" alt="First" />'),
            Chapter("Second", '<img src="second/figure.png" alt="Second" />'),
        ],
    )

    root = write_reader_html(document, tmp_path)

    manifest = json.loads((root / "book.json").read_text(encoding="utf-8"))
    assert len(manifest["assets"]) == 2
    localized = {path.read_bytes() for path in (root / "assets").iterdir()}
    assert localized == {b"first image", b"second image"}
    chapters = [path.read_text(encoding="utf-8") for path in (root / "chapters").iterdir()]
    references = {
        content.split('src="../assets/', 1)[1].split('"', 1)[0] for content in chapters
    }
    assert len(references) == 2


def test_reader_html_rejects_a_dangling_asset_destination_symlink(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "figure.png"
    source.write_bytes(b"source image")
    assets_dir = tmp_path / "reader-html" / "assets"
    assets_dir.mkdir(parents=True)
    external = tmp_path / "outside.png"
    os.symlink(external, assets_dir / "figure.png")
    document = BookDocument(
        schema_version="2",
        metadata={"title": "Reader book", "language": "en"},
        chapters=[Chapter("First", '<img src="source/figure.png" alt="Figure" />')],
    )

    with pytest.raises(RuntimeError, match="asset destination redirects"):
        write_reader_html(document, tmp_path)

    assert not external.exists()


@pytest.mark.parametrize("source", ["../outside.png", "//host/share/image.png"])
def test_reader_html_rejects_assets_outside_the_workspace(
    tmp_path: Path, source: str
) -> None:
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"private image")
    document = BookDocument(
        schema_version="2",
        metadata={"title": "Reader book", "language": "en"},
        chapters=[Chapter("First", f'<img src="{source}" alt="Figure" />')],
    )

    with pytest.raises(RuntimeError, match="outside the conversion workspace"):
        write_reader_html(document, tmp_path)

    assert not (tmp_path / "reader-html" / "assets" / "outside.png").exists()
