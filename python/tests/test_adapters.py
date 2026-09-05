import re
from pathlib import Path

from any2book_processors.adapters import (
    _normalize_tcvn3,
    _prefer_pdf_prose_layout,
    _remove_pdf_page_artifacts,
    _repair_pdf_page_flow,
    _restore_pdf_font_case,
    detect_format,
    extract_document,
)


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


def test_normalize_tcvn3_text_layer() -> None:
    source = "Hå ChÝ Minh Toµn TËp. Tr−¬ng TÊn Sang. §éc lËp - Tù do - H¹nh phóc."

    normalized, changed = _normalize_tcvn3(source)

    assert normalized == "Hồ Chí Minh Toàn Tập. Trương Tấn Sang. Độc lập - Tự do - Hạnh phúc."
    assert changed > 0


def test_normalize_tcvn3_restores_uppercase_words() -> None:
    source = (
        "**KH¤NG Cã G× QUý H¥N §éC LËP, Tù DO !**\n"
        "NGUYÔN KH¸NH BËT Tr−ëng nhãm"
    )

    normalized, _ = _normalize_tcvn3(source)

    assert normalized == (
        "**KHÔNG CÓ GÌ QUÝ HƠN ĐỘC LẬP, TỰ DO !**\n"
        "NGUYỄN KHÁNH BẬT Trưởng nhóm"
    )


def test_normalize_tcvn3_leaves_unicode_text_unchanged() -> None:
    source = "Hồ Chí Minh Toàn tập © 2011. Giá trị: 5 µm."

    normalized, changed = _normalize_tcvn3(source)

    assert normalized == source
    assert changed == 0


def test_restore_pdf_font_case_from_tcvn3_all_caps_font() -> None:
    text_dictionary = {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {"font": ".VnCenturySchoolbookH", "text": "THEO quyÕt ®Þnh cñA"},
                            {"font": ".VnCenturySchoolbookH", "text": "®ÆNG v¨n th¸I"},
                            {"font": ".VnCenturySchoolbook", "text": "Chñ tÞch Héi ®ång"},
                        ]
                    }
                ]
            }
        ]
    }
    source = "**THEO quyết định củA**\n\nĐẶNG văn tháI\n\nChủ tịch Hội đồng"

    normalized, changed = _restore_pdf_font_case(source, text_dictionary)

    assert normalized == "**THEO QUYẾT ĐỊNH CỦA**\n\nĐẶNG VĂN THÁI\n\nChủ tịch Hội đồng"
    assert changed == 2


def test_prefer_pdf_prose_layout_when_it_reconstructs_paragraphs() -> None:
    lines = [f"Dòng văn bản dài thứ {index} trong cùng một đoạn của trang." for index in range(30)]
    legacy = "\n\n".join(lines)
    candidate = " ".join(lines)

    assert _prefer_pdf_prose_layout(legacy, candidate)
    assert not _prefer_pdf_prose_layout("\n\n".join(lines[:5]), " ".join(lines[:5]))


def test_remove_pdf_repeated_headers_and_roman_page_numbers() -> None:
    pages = [
        "vii\n\n## Lời giới thiệu\n\nĐoạn mở đầu.",
        "HỒ CHÍ MINH TOÀN TẬP\n\nviii\n\nNội dung trang tám.",
        "LỜI GIỚI THIỆU\n\nix\n\nNội dung trang chín.",
        "HỒ CHÍ MINH TOÀN TẬP\n\nx\n\nNội dung trang mười.",
        "LỜI GIỚI THIỆU\n\nxi\n\nNội dung trang mười một.",
        "HỒ CHÍ MINH TOÀN TẬP\n\nxii\n\nNội dung trang mười hai.",
        "LỜI GIỚI THIỆU\n\nxiii\n\nNội dung trang mười ba.",
    ]

    cleaned, metrics = _remove_pdf_page_artifacts(pages)

    assert "## Lời giới thiệu" in cleaned[0]
    assert all("HỒ CHÍ MINH TOÀN TẬP" not in page for page in cleaned)
    assert "LỜI GIỚI THIỆU" not in "\n".join(cleaned[2:])
    assert all(not re.search(r"(?m)^[ivxlcdm]+$", page) for page in cleaned)
    assert metrics == {"removedRunningHeaders": 6, "removedRomanPageNumbers": 7}


def test_repair_pdf_footnotes_ornaments_and_cross_page_paragraphs() -> None:
    first_paragraph = " ".join(["Một đoạn văn đang tiếp tục qua trang"] * 6)
    continuation = "phần còn lại của câu vẫn tiếp tục ở trang sau và chưa kết thúc."
    pages = [
        (
            f"{first_paragraph}<sup>1</sup>\n\n*\n\n*      *\n\n"
            "1. Nội dung chú thích nguyên bản."
        ),
        f"{continuation}\n\nMột đoạn mới hoàn chỉnh.",
    ]

    repaired, metrics = _repair_pdf_page_flow(pages)
    joined = "\n".join(repaired)

    assert "[^1]" in joined
    assert "[^1]: Nội dung chú thích nguyên bản." in joined
    assert '<div class="ornament">* * *</div>' in joined
    assert f"{first_paragraph}[^1] {continuation}" in joined
    assert metrics == {
        "convertedFootnotes": 1,
        "collapsedOrnaments": 1,
        "joinedPageParagraphs": 1,
    }
