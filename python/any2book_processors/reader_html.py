from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

from .models import BookDocument

_FORBIDDEN = re.compile(r"<(script|iframe|object|embed|form)\b", re.IGNORECASE)
_REMOTE_ASSET = re.compile(r"src=[\"']https?://", re.IGNORECASE)
_IMAGE_SRC = re.compile(r"src=([\"'])(.*?)\1", re.IGNORECASE)


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value[:60] or "chapter"


def _localize_assets(content: str, work_dir: Path, assets_dir: Path) -> tuple[str, set[str]]:
    copied: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        source_value = html.unescape(match.group(2))
        source = Path(source_value)
        if not source.is_absolute():
            source = work_dir / source
        if not source.is_file():
            raise RuntimeError(f"Reader HTML references a missing asset: {source_value}")
        destination = assets_dir / source.name
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        copied.add(destination.name)
        return f'src="../assets/{html.escape(destination.name, quote=True)}"'

    return _IMAGE_SRC.sub(replace, content), copied


def write_reader_html(document: BookDocument, work_dir: Path) -> Path:
    """Write the inspectable semantic HTML contract used before EPUB packaging."""
    root = work_dir / "reader-html"
    chapters_dir = root / "chapters"
    assets_dir = root / "assets"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    copied_assets: set[str] = set()
    chapter_manifest: list[dict[str, object]] = []
    for index, chapter in enumerate(document.chapters, 1):
        content, chapter_assets = _localize_assets(chapter.html, work_dir, assets_dir)
        copied_assets.update(chapter_assets)
        if _FORBIDDEN.search(content):
            raise RuntimeError(f"Forbidden active HTML in chapter: {chapter.title}")
        if _REMOTE_ASSET.search(content):
            raise RuntimeError(f"Remote asset in reader HTML chapter: {chapter.title}")
        filename = f"{index:03d}-{_safe_name(chapter.title)}.xhtml"
        target = chapters_dir / filename
        language = html.escape(str(document.metadata.get("language", "en")))
        target.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{language}">'
            f'<head><meta charset="utf-8"/><title>{html.escape(chapter.title)}</title></head>'
            f"<body><section><h1>{html.escape(chapter.title)}</h1>{content}</section></body></html>",
            encoding="utf-8",
        )
        chapter_manifest.append(
            {
                "id": f"chapter-{index}",
                "title": chapter.title,
                "href": f"chapters/{filename}",
                "sourceLocation": chapter.source_location,
            }
        )

    manifest = {
        "schemaVersion": document.schema_version,
        "metadata": document.metadata,
        "chapters": chapter_manifest,
        "assets": [f"assets/{name}" for name in sorted(copied_assets)],
        "quality": document.quality,
    }
    (root / "book.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (root / "provenance.json").write_text(
        json.dumps(document.provenance, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    warning_data = [warning.to_dict() for warning in document.warnings]
    (root / "warnings.json").write_text(
        json.dumps(warning_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return root
