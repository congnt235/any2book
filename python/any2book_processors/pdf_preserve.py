"""PDF reflow without editorial cleanup or AI substitutions."""

import hashlib
import html
import re
from collections.abc import Callable
from pathlib import Path
from statistics import median
from typing import Any, NamedTuple, cast

import pymupdf

from .fidelity import normalize_text, text_content
from .models import BookDocument, Chapter, ConversionWarning
from .pdf_layout import aligned_roster


class LineLayout(NamedTuple):
    baseline: float
    left: float
    right: float
    size: float
    bold: bool


class PageEvent(NamedTuple):
    top: float
    left: float
    markup: str
    text: str
    digest: str | None
    layout: LineLayout | None = None


def _reflow_lines(events: list[PageEvent]) -> str:
    """Join physical wraps, not paragraphs; never change text or inline markup.

    PDF blocks are not paragraphs (some producers emit one block per line).
    Use the right margin, first-line indents, font size and baseline spacing.
    Short lines, list starts, graphics and changes of weight remain boundaries.
    """
    lines = [e.layout for e in events if e.layout is not None]
    steps = [b.baseline - a.baseline for a, b in zip(lines, lines[1:], strict=False)
             if 0.7 * a.size < b.baseline - a.baseline < 2.2 * a.size
             and abs(a.size - b.size) < a.size * 0.1]
    spacing = median(steps) if steps else 0.0
    result: list[str] = []
    previous: PageEvent | None = None
    for event in events:
        a = previous.layout if previous else None
        b = event.layout
        join = False
        if a is not None and b is not None and spacing:
            right = max(line.right for line in lines
                        if abs(line.size - a.size) < a.size * 0.1
                        and line.left < a.right and line.right > a.left)
            list_start = re.match(r"^\s*(?:[•●▪*]|[-–—]|\d+[.)])\s+", event.text)
            join = (
                abs(a.size - b.size) < a.size * 0.1
                and a.bold == b.bold
                and 0.7 * a.size < b.baseline - a.baseline <= spacing * 1.25
                and -2.5 * a.size <= b.left - a.left <= 0.6 * a.size
                and a.right >= right - 2 * a.size
                and not list_start
            )
        if join:
            result[-1] = result[-1][:-4] + " " + event.markup[3:]
        else:
            result.append(event.markup)
        previous = event
    return "\n".join(result)


def extract_preserved_pdf(
    path: Path, work: Path, metadata: dict[str, Any],
    decode: Callable[[dict[str, Any]], str],
) -> BookDocument:
    chapters: list[Chapter] = []
    expected_pages: list[str] = []
    asset_hashes: list[str] = []
    assets: list[str] = []
    provenance: list[dict[str, object]] = []
    roster_groups = roster_rows = roster_pages = 0
    asset_dir = work / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(path) as pdf:
        for index, page in enumerate(pdf):
            if page.get_links() or list(page.annots() or []) or list(page.widgets() or []):
                raise RuntimeError(
                    f"Page {index + 1}: links, annotations or forms require preservation review"
                )
            dictionary = page.get_text("dict", sort=True)
            events: list[PageEvent] = []
            text_boxes = []
            span_audit: list[dict[str, object]] = []
            baselines: list[tuple[float, float, float, float]] = []
            for block in dictionary["blocks"]:
                if block["type"] == 1:
                    filename = f"assets/page-{index + 1}-image-{len(assets)}.{block['ext']}"
                    payload = block["image"]
                    # Preserve transparent masks through the PDF renderer when necessary.
                    if block.get("mask"):
                        payload = page.get_pixmap(clip=block["bbox"], alpha=True).tobytes("png")
                        filename += ".png"
                    (work / filename).write_bytes(payload)
                    digest = hashlib.sha256(payload).hexdigest()
                    assets.append(filename)
                    events.append(PageEvent(block["bbox"][1], block["bbox"][0],
                                   f'<img src="{filename}" alt="" />', "", digest))
                    continue
                for line in block.get("lines", []):
                    if tuple(line.get("dir", (1, 0))) != (1, 0):
                        raise RuntimeError(f"Page {index + 1}: rotated text requires review")
                    spans = line["spans"]
                    text = "".join(decode(s) for s in spans)
                    if "\ufffd" in text or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
                        raise RuntimeError(f"Page {index + 1}: undecodable text requires review")
                    if not text.strip():
                        continue
                    text_boxes.append(pymupdf.Rect(line["bbox"]))
                    baselines.append((spans[0]["origin"][1], line["bbox"][0],
                                      line["bbox"][2], spans[0]["size"]))
                    markup = ""
                    for span in spans:
                        span_audit.append({"text": span["text"], "decodedText": decode(span),
                                           "font": span.get("font", ""), "bbox": span["bbox"],
                                           "flags": span.get("flags", 0)})
                        value = html.escape(decode(span))
                        if span.get("flags", 0) & 16:
                            value = f"<strong>{value}</strong>"
                        if span.get("flags", 0) & 2:
                            value = f"<em>{value}</em>"
                        if span.get("flags", 0) & 1:
                            value = f"<sup>{value}</sup>"
                        markup += value
                    main_span = max(spans, key=lambda s: len(s["text"].strip()))
                    layout = LineLayout(main_span["origin"][1], line["bbox"][0],
                                        line["bbox"][2], main_span["size"],
                                        bool(main_span.get("flags", 0) & 16))
                    events.append(PageEvent(line["bbox"][1], line["bbox"][0],
                                            f"<p>{markup}</p>", text, None, layout))
            for drawing in page.get_drawings():
                rect = pymupdf.Rect(drawing["rect"])
                rect = pymupdf.Rect(rect.x0 - 1, rect.y0 - 1, rect.x1 + 1, rect.y1 + 1)
                if any(rect.intersects(box) for box in text_boxes):
                    raise RuntimeError(
                        f"Page {index + 1}: overlapping vector graphics require review"
                    )
                payload = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(2, 2),
                                          alpha=True).tobytes("png")
                filename = f"assets/page-{index + 1}-vector-{len(assets)}.png"
                (work / filename).write_bytes(payload)
                assets.append(filename)
                events.append(PageEvent(rect.y0, rect.x0, f'<img src="{filename}" alt="" />', "",
                               hashlib.sha256(payload).hexdigest()))
            events.sort(key=lambda event: (event[0], event[1]))
            markup = _reflow_lines(events)
            expected = normalize_text(" ".join(e[3] for e in events))
            styled = any(cast(int, span["flags"]) & 19 for span in span_audit)
            roster = (aligned_roster(dictionary, decode)
                      if not page.get_drawings() and not styled else None)
            if roster is not None:
                candidate, groups, rows = roster
                # A geometry reconstruction must preserve the exact ordered transcription.
                if text_content(candidate) == expected:
                    markup = candidate
                    roster_groups += groups
                    roster_rows += rows
                    roster_pages += 1
                else:
                    roster = None
            if roster is None:
                ordered = sorted(baselines)
                for left, right in zip(ordered, ordered[1:], strict=False):
                    # Isolated top/bottom furniture is retained in left-to-right order,
                    # never deleted. Interior parallel columns need a reading-order review.
                    if (abs(left[0] - ordered[0][0]) < left[3] * 0.25
                            or abs(right[0] - ordered[-1][0]) < right[3] * 0.25):
                        continue
                    if (abs(left[0] - right[0]) < min(left[3], right[3]) * 0.25
                            and right[1] - left[2] > min(left[3], right[3])):
                        raise RuntimeError(
                            f"Page {index + 1}: ambiguous column reading order requires review"
                        )
            if text_content(markup) != expected:
                raise RuntimeError(f"Page {index + 1}: HTML transcription mismatch")
            expected_pages.append(expected)
            asset_hashes.extend(e[4] for e in events if e[4] is not None)
            chapters.append(Chapter(f"Page {index + 1}", markup, f"{path}#page={index + 1}"))
            provenance.append({"sourcePage": index + 1, "engine": "pdf-source-preserving",
                               "spans": span_audit,
                               "textSha256": hashlib.sha256(expected.encode()).hexdigest()})
    return BookDocument(
        schema_version="2", metadata=metadata, chapters=chapters, assets=assets,
        source={"path": str(path), "format": "pdf", "pages": len(chapters),
                "preservedText": " ".join(expected_pages), "preservedAssetHashes": asset_hashes},
        provenance=provenance,
        quality={"preservationMode": "strict", "sourcePages": len(chapters),
                 "rosterPages": roster_pages, "rosterGroups": roster_groups,
                 "rosterRows": roster_rows, "embeddedImages": len(asset_hashes),
                 "aiCorrectionsApplied": 0, "verification": "pending-epub"},
        warnings=[ConversionWarning(
            "PDF_REFLOW", "Source text, page numbers and ornaments retained; "
            "line wrapping and pagination may change. Vector graphics are rasterized.", "info")],
    )
