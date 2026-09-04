import json
import xml.etree.ElementTree as etree
from pathlib import Path

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
