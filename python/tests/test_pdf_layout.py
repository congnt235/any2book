import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from any2book_processors.adapters import _repair_pdf_page_flow, _word_count, extract_document
from any2book_processors.epub import internal_validate, render
from any2book_processors.pdf_layout import aligned_roster


def page(scale: float = 1) -> dict[str, Any]:
    lines = []
    for text, x, y, width in [
        ("EDITORIAL BOARD", 130, 20, 140),
        ("ALICE", 100, 40, 70), ("Chair", 230, 40, 70),
        ("BOB", 100, 55, 70), ("Deputy", 230, 55, 70),
        ("CAROL", 100, 70, 70), ("Member", 230, 70, 70),
        ("DAVE", 100, 85, 70),
        ("SECOND GROUP", 140, 120, 140),
        ("ERIN", 110, 140, 70), ("Lead", 240, 140, 70),
        ("FRANK", 110, 155, 70), ("Member", 240, 155, 70),
        ("GRACE", 110, 170, 70), ("Member", 240, 170, 70),
    ]:
        lines.append({"spans": [{
            "text": text, "size": 10 * scale,
            "bbox": [x * scale, (y - 10) * scale, (x + width) * scale, y * scale],
            "origin": [x * scale, y * scale],
        }]})
    return {"blocks": [{"type": 0, "lines": lines}]}


@pytest.mark.parametrize("scale", [0.5, 1, 2])
def test_preserves_pairs_empty_cells_and_section_specific_columns(scale: float) -> None:
    result = aligned_roster(page(scale), lambda s: str(s["text"]))
    assert result is not None
    markup, groups, members = result
    assert (groups, members) == (2, 7)
    root = ET.fromstring(f"<root>{markup}</root>")
    assert [c.text for c in root.findall(".//caption")] == ["EDITORIAL BOARD", "SECOND GROUP"]
    assert [[c.text or "" for c in row] for row in root.findall(".//tr")] == [
        ["ALICE", "Chair"], ["BOB", "Deputy"], ["CAROL", "Member"], ["DAVE", ""],
        ["ERIN", "Lead"], ["FRANK", "Member"], ["GRACE", "Member"],
    ]


def test_rejects_prose_in_columns() -> None:
    data = page()
    data["blocks"][0]["lines"][1]["spans"][0]["text"] = "A sentence in a newspaper."
    assert aligned_roster(data, lambda s: str(s["text"])) is None


def test_rejects_misaligned_role_instead_of_guessing_pair() -> None:
    data = page()
    data["blocks"][0]["lines"][2]["spans"][0]["origin"][1] += 6
    assert aligned_roster(data, lambda s: str(s["text"])) is None


def test_rejects_image_page_and_preserves_extractor_fallback() -> None:
    data = page()
    data["blocks"].append({"type": 1})
    assert aligned_roster(data, lambda s: str(s["text"])) is None


def test_table_markup_does_not_inflate_word_coverage() -> None:
    markup = '<table class="pdf-roster"><tr><td>ALICE</td><td>Chair</td></tr></table>'
    assert _word_count(markup) == 2


def test_roster_cannot_be_joined_to_next_pages_prose() -> None:
    table = '<table><tr><td>' + "ALICE " * 30 + '</td></tr></table>'
    next_page = "continuing text " * 10
    pages, metrics = _repair_pdf_page_flow([table, next_page])
    assert metrics["joinedPageParagraphs"] == 0
    assert next_page.strip() == pages[1].strip()


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_pdf_roster_survives_full_epub_pipeline(tmp_path: Path) -> None:
    source = tmp_path / "roster.pdf"
    with pymupdf.open() as pdf:  # type: ignore[no-untyped-call]
        sheet = pdf.new_page()
        for line in page()["blocks"][0]["lines"]:
            span = line["spans"][0]
            sheet.insert_text(span["origin"], span["text"], fontsize=10)
        pdf.save(source)
    work = tmp_path / "work"
    work.mkdir()
    book = extract_document(
        source, "pdf", work, {"title": "Roster", "language": "en", "authors": []}
    )
    assert book.quality["rosterRows"] == 7
    output = tmp_path / "result.epub"
    render(book, output, work, {"conversion": {"splitLevel": 1, "tableOfContents": "auto"}})
    internal_validate(output)
    with zipfile.ZipFile(output) as archive:
        chapters = [ET.fromstring(archive.read(n)) for n in archive.namelist()
                    if n.endswith(".xhtml") and "/text/ch" in n]
    tables = [t for chapter in chapters for t in chapter.iter()
              if t.tag.endswith("}table") and t.get("class") == "pdf-roster"]
    assert len(tables) == 2
    rows = [row for table in tables for row in table.iter() if row.tag.endswith("}tr")]
    assert [["".join(c.itertext()) for c in row] for row in rows] == [
        ["ALICE", "Chair"], ["BOB", "Deputy"], ["CAROL", "Member"], ["DAVE", ""],
        ["ERIN", "Lead"], ["FRANK", "Member"], ["GRACE", "Member"],
    ]
