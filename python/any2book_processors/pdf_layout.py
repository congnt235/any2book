"""Conservative recognition of headed, aligned name/role lists from PDF geometry."""

import html
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from typing import Any


@dataclass
class Line:
    text: str
    x0: float
    x1: float
    baseline: float
    size: float


def aligned_roster(
    dictionary: dict[str, Any], decode: Callable[[dict[str, Any]], str]
) -> tuple[str, int, int] | None:
    """Return a complete page only when every visible line has an unambiguous role.

    Unlike newspaper columns, roster cells share baselines and the left cells are
    short uppercase labels. Thresholds scale with font size, not page dimensions.
    Unsupported pages fall back intact to the existing extractor.
    """
    lines: list[Line] = []
    for block in dictionary.get("blocks", []):
        if block.get("type", 0) != 0:
            return None
        for line in block.get("lines", []):
            if tuple(line.get("dir", (1, 0))) != (1, 0):
                return None
            spans = [s for s in line.get("spans", []) if s["text"].strip()]
            if not spans:
                continue
            lines.append(Line(
                "".join(decode(s) for s in line.get("spans", [])).strip(),
                min(s["bbox"][0] for s in spans),
                max(s["bbox"][2] for s in spans),
                median(s["origin"][1] for s in spans),
                median(s["size"] for s in spans),
            ))
    if not lines:
        return None
    size = median(line.size for line in lines)
    tolerance = size * 0.25
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda item: (item.baseline, item.x0)):
        if rows and abs(line.baseline - rows[-1][0].baseline) <= tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    for row in rows:
        row.sort(key=lambda item: item.x0)
        if len(row) > 2:
            return None

    sections: list[str] = []
    total_pairs = 0
    total_members = 0
    index = 0
    while index < len(rows):
        heading = rows[index]
        if len(heading) != 1 or not heading[0].text.isupper():
            return None
        index += 1
        if index >= len(rows) or len(rows[index]) != 2:
            return None
        left, right = rows[index]
        if right.x0 - left.x1 < size:
            return None
        center = (left.x0 + right.x1) / 2
        if abs((heading[0].x0 + heading[0].x1) / 2 - center) > size * 2:
            return None
        cells: list[tuple[str, str]] = []
        previous_baseline = heading[0].baseline
        while index < len(rows):
            row = rows[index]
            first = row[0]
            if abs(first.x0 - left.x0) > tolerance:
                break
            if not first.text.isupper() or len(first.text.split()) > 10:
                return None
            role = ""
            if len(row) == 2:
                second = row[1]
                if abs(second.x0 - right.x0) > tolerance or second.x0 - first.x1 < size:
                    return None
                role = second.text
                total_pairs += 1
            elif cells and first.baseline - previous_baseline > size * 2.5:
                return None
            cells.append((first.text, role))
            previous_baseline = first.baseline
            index += 1
        if len(cells) < 3:
            return None
        total_members += len(cells)
        body = "".join(
            f"<tr><td>{html.escape(name)}</td><td>{html.escape(role)}</td></tr>"
            for name, role in cells
        )
        sections.append(
            '<table class="pdf-roster">'
            f"<caption>{html.escape(heading[0].text)}</caption><tbody>{body}</tbody></table>"
        )
    if total_pairs < 3:
        return None
    return "\n\n".join(sections), len(sections), total_members
