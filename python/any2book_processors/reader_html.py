from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .models import BookDocument
from .path_safety import path_redirect_component

_FORBIDDEN = re.compile(r"<(script|iframe|object|embed|form)\b", re.IGNORECASE)
_REMOTE_ASSET = re.compile(r"src=[\"']https?://", re.IGNORECASE)
_IMAGE_SRC = re.compile(r"src=([\"'])(.*?)\1", re.IGNORECASE)


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value[:60] or "chapter"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _same_asset(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve():
        return True
    return first.stat().st_size == second.stat().st_size and _digest(first) == _digest(second)


def _reject_redirected_destination(destination: Path) -> None:
    redirect = path_redirect_component(destination)
    if redirect is not None:
        raise RuntimeError(f"Reader HTML asset destination redirects through: {redirect}")


def _asset_destination(source: Path, assets_dir: Path) -> Path:
    destination = assets_dir / source.name
    _reject_redirected_destination(destination)
    if not destination.exists() or _same_asset(source, destination):
        return destination
    digest = _digest(source)
    destination = assets_dir / f"{source.stem}-{digest[:12]}{source.suffix}"
    _reject_redirected_destination(destination)
    if destination.exists() and not _same_asset(source, destination):
        destination = assets_dir / f"{source.stem}-{digest}{source.suffix}"
        _reject_redirected_destination(destination)
    if destination.exists() and not _same_asset(source, destination):
        raise RuntimeError(f"Could not create a collision-safe asset name for: {source}")
    return destination


def _copy_asset(source: Path, destination: Path) -> None:
    """Copy through an exclusively-created file, then atomically install it."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".any2book-asset-", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as source_stream, os.fdopen(descriptor, "wb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        temporary.replace(destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _reader_asset_source(source_value: str, work_dir: Path) -> Path:
    if source_value.startswith(("//", "\\\\")):
        raise RuntimeError(f"Reader HTML asset is outside the conversion workspace: {source_value}")
    root = work_dir.resolve(strict=True)
    source = Path(source_value)
    candidate = source if source.is_absolute() else work_dir / source
    if source.is_absolute():
        try:
            candidate.absolute().relative_to(work_dir.absolute())
        except ValueError as exc:
            raise RuntimeError(
                f"Reader HTML asset is outside the conversion workspace: {source_value}"
            ) from exc
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Reader HTML asset is outside the conversion workspace: {source_value}"
        ) from exc
    if not resolved.is_file():
        raise RuntimeError(f"Reader HTML references a missing asset: {source_value}")
    return resolved


def _localize_assets(content: str, work_dir: Path, assets_dir: Path) -> tuple[str, set[str]]:
    copied: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        source_value = html.unescape(match.group(2))
        source = _reader_asset_source(source_value, work_dir)
        destination = _asset_destination(source, assets_dir)
        if not destination.exists():
            _copy_asset(source, destination)
        copied.add(destination.name)
        return f'src="../assets/{html.escape(destination.name, quote=True)}"'

    return _IMAGE_SRC.sub(replace, content), copied


def write_reader_html(document: BookDocument, work_dir: Path) -> Path:
    """Write the inspectable semantic HTML contract used before EPUB packaging."""
    work_dir = work_dir.resolve(strict=True)
    root = work_dir / "reader-html"
    chapters_dir = root / "chapters"
    assets_dir = root / "assets"
    for directory in (root, chapters_dir, assets_dir):
        _reject_redirected_destination(directory)
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
