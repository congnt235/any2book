import shutil
import zipfile
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from any2book_processors.adapters import extract_document
from any2book_processors.epub import render
from any2book_processors.fidelity import text_content, verify_epub
from any2book_processors.pdf_layout import aligned_roster
from any2book_processors.pdf_preserve import LineLayout, PageEvent, _reflow_lines

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc required")


def test_reflow_wraps_preserves_paragraphs_and_inline_styles() -> None:
    def line(y: float, left: float, right: float, text: str) -> PageEvent:
        return PageEvent(y, left, f"<p>{text}</p>", text_content(text), None,
                         LineLayout(y, left, right, 10, False))

    events = [line(50, 60, 350, "First paragraph reaches the margin"),
              line(65, 40, 350, "and continues with <em>italic words</em>"),
              line(80, 40, 200, "and ends here."),
              line(95, 60, 350, "Second paragraph starts indented"),
              line(110, 40, 180, "and ends."),
              line(140, 40, 350, "A paragraph after a vertical gap"),
              line(155, 40, 200, "continues here.")]
    result = _reflow_lines(events)
    assert result.count("<p>") == 3
    assert "margin and continues with <em>italic words</em> and ends here." in result
    assert text_content(result) == text_content(" ".join(e.markup for e in events))


def test_reflow_keeps_short_lines_lists_and_graphics_separate() -> None:
    events = [PageEvent(y, 40, f"<p>{text}</p>", text, None,
                        LineLayout(y, 40, right, 10, False))
              for y, right, text in [(50, 350, "Full line"),
                                     (65, 350, "1. A list item"),
                                     (80, 120, "Short verse"),
                                     (95, 120, "Another verse")]]
    events.insert(2, PageEvent(70, 40, '<img src="rule.png" />', "", "hash"))
    result = _reflow_lines(events)
    assert result.count("<p>") == 4
    assert '<img src="rule.png" />' in result


def make_book(tmp_path: Path) -> Any:
    source = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:  # type: ignore[no-untyped-call]
        for _ in range(3):
            page = pdf.new_page()
            page.insert_text((60, 60), "REPEATED SOURCE HEADING")
            for index, text in enumerate(["100 is not 900.", "*", "IV"] +
                                          [str(year) for year in range(2000, 2010)]):
                page.insert_text((60, 90 + index * 18), text)
        pdf.save(source)
    return extract_document(source, "pdf", tmp_path,
                            {"title": "Metadata only", "language": "en", "authors": []},
                            {"provider": "codex"})


def test_default_preserves_numbers_repeats_ornaments_and_disables_ai(tmp_path: Path) -> None:
    book = make_book(tmp_path)
    expected = book.source["preservedText"]
    assert expected.count("REPEATED SOURCE HEADING") == 3
    assert expected.count("*") == 3
    assert expected.count("IV") == 3
    for year in range(2000, 2010):
        assert expected.count(str(year)) == 3
    assert book.quality["aiCorrectionsApplied"] == 0
    output = tmp_path / "book.epub"
    render(book, output, tmp_path,
           {"conversion": {"splitLevel": 1, "tableOfContents": "auto"}})
    assert book.quality["verification"] == "passed-epub-text-and-assets"
    verify_epub(output, expected, [])
    with pytest.raises(RuntimeError, match="text differs"):
        verify_epub(output, expected.replace("100", "900"), [])
    with pytest.raises(RuntimeError, match="image occurrences"):
        verify_epub(output, expected, ["missing-image"])
    with zipfile.ZipFile(output) as archive:
        text = "".join(archive.read(n).decode() for n in archive.namelist()
                       if "/text/" in n and n.endswith(".xhtml"))
    assert "Metadata only" not in text_content(text)


def test_mixed_unicode_and_legacy_fonts_are_decoded_per_span(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    import any2book_processors.pdf_preserve as module

    def capture(path: Path, work: Path, metadata: dict[str, Any], decode: Any) -> Any:
        for font, text in [(".VnTime", "Hå ChÝ Minh"), ("Times", "Hồ Chí Minh"),
                           (".VnTimeH", "Nam"), ("Times", "Việt Nam")]:
            captured[text] = decode({"font": font, "text": text})
        return make_result

    make_result = make_book(tmp_path)
    monkeypatch.setattr(module, "extract_preserved_pdf", capture)
    extract_document(tmp_path / "source.pdf", "pdf", tmp_path, {})
    assert captured == {"Hå ChÝ Minh": "Hồ Chí Minh", "Hồ Chí Minh": "Hồ Chí Minh",
                        "Nam": "NAM", "Việt Nam": "Việt Nam"}


def test_space_only_spans_survive_roster() -> None:
    from test_pdf_layout import page

    data = page()
    span = data["blocks"][0]["lines"][1]["spans"][0]
    data["blocks"][0]["lines"][1]["spans"] = [span, {**span, "text": " "},
                                                {**span, "text": "SMITH"}]
    result = aligned_roster(data, lambda s: str(s["text"]))
    assert result is not None
    assert "ALICE SMITH" in result[0]


def test_vector_occurrences_and_image_only_page_survive(tmp_path: Path) -> None:
    source = tmp_path / "graphics.pdf"
    with pymupdf.open() as pdf:  # type: ignore[no-untyped-call]
        for _ in range(2):
            page = pdf.new_page()
            page.draw_rect((40, 40, 120, 120), color=(1, 0, 0))
        pdf.save(source)
    book = extract_document(source, "pdf", tmp_path,
                            {"title": "Graphics", "language": "en", "authors": []})
    hashes = book.source["preservedAssetHashes"]
    assert isinstance(hashes, list)
    assert len(hashes) == 2
    output = tmp_path / "graphics.epub"
    render(book, output, tmp_path,
           {"conversion": {"splitLevel": 1, "tableOfContents": "auto"}})
    assert book.quality["verification"] == "passed-epub-text-and-assets"


def test_parallel_prose_columns_require_review(tmp_path: Path) -> None:
    source = tmp_path / "columns.pdf"
    with pymupdf.open() as pdf:  # type: ignore[no-untyped-call]
        page = pdf.new_page()
        for y in [50, 70, 90, 110]:
            page.insert_text((40, y), "Left paragraph line.")
            page.insert_text((300, y), "Right paragraph line.")
        pdf.save(source)
    with pytest.raises(RuntimeError, match="ambiguous column"):
        extract_document(source, "pdf", tmp_path,
                         {"title": "Columns", "language": "en", "authors": []})
