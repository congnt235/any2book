from pathlib import Path

from any2book_processors.adapters import detect_format, extract_document


def test_detect_and_extract_text(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")
    assert detect_format(source) == "txt"
    document = extract_document(
        source, "txt", tmp_path, {"title": "Notes", "language": "en", "authors": []}
    )
    assert document.schema_version == "2"
    assert len(document.chapters) == 1
    assert "First paragraph" in document.chapters[0].html
