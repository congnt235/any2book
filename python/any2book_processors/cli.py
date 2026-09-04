from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import SUPPORTED, detect_format, direct_convert, extract_document, inspect
from .epub import internal_validate, make_preview, render, run_epubcheck, write_reports
from .reader_html import write_reader_html


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert(input_path: Path, config_path: Path, work_dir: Path) -> dict[str, object]:
    config: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    file_format = detect_format(input_path)
    if file_format not in SUPPORTED:
        raise RuntimeError(f"Unsupported input format: {input_path.suffix or 'unknown'}")
    ai_provider = str(config.get("ai", {}).get("provider", "off"))
    if ai_provider != "off" and file_format != "pdf":
        raise RuntimeError("AI correction currently supports PDF inputs only")
    output_dir = Path(config["output"]["directory"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / config["output"]["filename"]
    if output.suffix.lower() != ".epub":
        output = output.with_suffix(".epub")

    metadata = dict(config["book"])
    metadata["source"] = str(input_path)
    adapter = f"{file_format}-adapter"
    canonical_summary: dict[str, object] | None = None
    reader_html_path: Path | None = None
    ai_output_path: Path | None = None
    quality: dict[str, object] = {}
    if file_format in {"epub", "mobi"}:
        warnings = direct_convert(input_path, file_format, output)
    else:
        document = extract_document(input_path, file_format, work_dir, metadata, config.get("ai"))
        warnings = document.warnings
        canonical = document.to_dict()
        quality = document.quality
        canonical_summary = {
            "schemaVersion": document.schema_version,
            "chapterCount": len(document.chapters),
            "assetCount": len(document.assets),
            "quality": quality,
        }
        (work_dir / "book.json").write_text(
            json.dumps(canonical, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        reader_workspace = write_reader_html(document, work_dir)
        reader_html_path = output_dir / "reader-html"
        if reader_html_path.exists():
            shutil.rmtree(reader_html_path)
        shutil.copytree(reader_workspace, reader_html_path)
        ai_workspace = work_dir / "ai-review"
        if ai_workspace.exists():
            ai_output_path = output_dir / "ai-review"
            if ai_output_path.exists():
                shutil.rmtree(ai_output_path)
            shutil.copytree(ai_workspace, ai_output_path)
        render(document, output, work_dir, config)

    internal_validate(output)
    validation = run_epubcheck(output, warnings)
    preview_path = (
        make_preview(output, output_dir / "preview", warnings)
        if config["output"]["preview"]
        else None
    )
    result: dict[str, object] = {
        "status": "success",
        "output": str(output),
        "report": None,
        "preview": str(preview_path) if preview_path else None,
        "readerHtml": str(reader_html_path) if reader_html_path else None,
        "aiReview": str(ai_output_path) if ai_output_path else None,
        "adapter": adapter,
        "warnings": [warning.to_dict() for warning in warnings],
        "validation": validation,
        "quality": quality,
    }
    manifest = {
        "schemaVersion": 1,
        "createdAt": datetime.now(UTC).isoformat(),
        "converter": {"name": "any2book", "version": __version__},
        "runtime": {"python": platform.python_version()},
        "input": {"path": str(input_path), "format": file_format, "sha256": _checksum(input_path)},
        "output": {"path": str(output), "sha256": _checksum(output)},
        "adapter": adapter,
        "config": config,
        "canonicalModel": canonical_summary,
    }
    report_path = write_reports(output_dir, result, manifest, bool(config["output"]["report"]))
    result["report"] = str(report_path) if report_path else None
    # Rewrite JSON report with its final self-reference.
    if report_path:
        (output_dir / "report.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="any2book-backend")
    commands = root.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--input", type=Path, required=True)
    convert_parser = commands.add_parser("convert")
    convert_parser.add_argument("--input", type=Path, required=True)
    convert_parser.add_argument("--config", type=Path, required=True)
    convert_parser.add_argument("--work-dir", type=Path, required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "inspect":
            result = inspect(args.input)
        else:
            args.work_dir.mkdir(parents=True, exist_ok=True)
            result = convert(args.input, args.config, args.work_dir)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(f"any2book: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
