from __future__ import annotations

import html
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, cast

from .models import BookDocument, ConversionWarning

_CSS = """body { font-family: serif; line-height: 1.55; margin: 5%; }
img, svg { display: block; max-width: 100%; height: auto; margin: 1.25em auto; }
figure { margin: 1.5em auto; text-align: center; break-inside: avoid; page-break-inside: avoid; }
figure img, figure svg { margin-left: auto; margin-right: auto; }
figcaption { margin-top: .5em; text-align: center; font-size: .9em; }
table { border-collapse: collapse; max-width: 100%; }
th, td { border: 1px solid #888; padding: .3em; }
pre, code { white-space: pre-wrap; overflow-wrap: anywhere; }
a { text-decoration: none; }
"""


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing dependency: {command[0]}. Run `any2book doctor`.") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Command failed: {' '.join(command)}")
    return result


def render(document: BookDocument, output: Path, work_dir: Path, config: dict[str, Any]) -> None:
    source = work_dir / "normalized.html"
    css = work_dir / "book.css"
    css.write_text(_CSS, encoding="utf-8")
    title = html.escape(str(document.metadata["title"]))
    chapters = []
    for chapter in document.chapters:
        chapters.append(f"<section><h1>{html.escape(chapter.title)}</h1>{chapter.html}</section>")
    source.write_text(
        f'<!doctype html><html lang="{html.escape(str(document.metadata["language"]))}">'
        f'<head><meta charset="utf-8"><title>{title}</title></head>'
        f"<body>{''.join(chapters)}</body></html>",
        encoding="utf-8",
    )
    command = [
        "pandoc",
        str(source),
        "--from=html",
        "--to=epub3",
        "--output",
        str(output),
        "--css",
        str(css),
        "--metadata",
        f"title={document.metadata['title']}",
        "--metadata",
        f"lang={document.metadata['language']}",
        "--split-level",
        str(config["conversion"]["splitLevel"]),
        "--resource-path",
        str(work_dir),
    ]
    authors = cast(list[object], document.metadata.get("authors", []))
    for author in authors:
        command.extend(["--metadata", f"author={author}"])
    if config["conversion"]["tableOfContents"] != "none":
        command.append("--toc")
    cover = document.metadata.get("cover")
    if cover:
        command.extend(["--epub-cover-image", str(cover)])
    _run(command)


def internal_validate(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise RuntimeError("Output is not a ZIP-based EPUB")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names or names[0] != "mimetype":
            raise RuntimeError("Invalid EPUB: mimetype must be the first ZIP entry")
        mimetype = archive.getinfo("mimetype")
        if mimetype.compress_type != zipfile.ZIP_STORED:
            raise RuntimeError("Invalid EPUB: mimetype entry must be uncompressed")
        if archive.read("mimetype") != b"application/epub+zip":
            raise RuntimeError("Invalid EPUB mimetype")
        if "META-INF/container.xml" not in names:
            raise RuntimeError("Invalid EPUB: missing META-INF/container.xml")
        if (
            len(names) > 10_000
            or sum(item.file_size for item in archive.infolist()) > 2_000_000_000
        ):
            raise RuntimeError("Unsafe EPUB archive size")
        if any(name.startswith(("/", "\\")) or ".." in Path(name).parts for name in names):
            raise RuntimeError("Unsafe path found in EPUB archive")


def run_epubcheck(path: Path, warnings: list[ConversionWarning]) -> dict[str, object]:
    executable = shutil.which("epubcheck")
    if not executable:
        warnings.append(
            ConversionWarning(
                "EPUBCHECK_UNAVAILABLE", "EPUBCheck is not installed; only internal validation ran."
            )
        )
        return {"available": False, "passed": None}
    result = subprocess.run([executable, str(path)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"EPUBCheck failed:\n{result.stdout}\n{result.stderr}")
    return {"available": True, "passed": True, "output": result.stdout.strip()}


def make_preview(epub: Path, preview_dir: Path, warnings: list[ConversionWarning]) -> Path | None:
    preview_dir.mkdir(parents=True, exist_ok=True)
    target = preview_dir / "index.html"
    try:
        _run(
            [
                "pandoc",
                str(epub),
                "--to=html5",
                "--standalone",
                "--extract-media",
                str(preview_dir / "assets"),
                "--output",
                str(target),
            ]
        )
    except RuntimeError as exc:
        warnings.append(ConversionWarning("PREVIEW_FAILED", str(exc)))
        return None
    value = target.read_text(encoding="utf-8")
    value = value.replace("</head>", f"<style>{_CSS}</style></head>")
    target.write_text(value, encoding="utf-8")
    return target


def write_reports(
    output_dir: Path, result: dict[str, object], manifest: dict[str, object], enabled: bool
) -> Path | None:
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not enabled:
        return None
    json_path = output_dir / "report.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    warnings = cast(list[dict[str, object]], result["warnings"])
    warning_rows = (
        "".join(
            f"<tr><td>{html.escape(str(item['severity']))}</td><td>{html.escape(str(item['code']))}</td>"
            f"<td>{html.escape(str(item['message']))}</td></tr>"
            for item in warnings
        )
        or '<tr><td colspan="3">No warnings</td></tr>'
    )
    quality = cast(dict[str, object], result.get("quality", {}))
    quality_rows = (
        "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in quality.items()
        )
        or '<tr><td colspan="2">No source quality metrics</td></tr>'
    )
    html_path = output_dir / "report.html"
    html_path.write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>Any2Book report</title>'
        f"<style>{_CSS}td,th{{text-align:left}}</style></head><body><h1>Conversion report</h1>"
        f"<p><strong>Status:</strong> {html.escape(str(result['status']))}</p>"
        f"<p><strong>Adapter:</strong> {html.escape(str(result['adapter']))}</p>"
        f"<p><strong>Output:</strong> {html.escape(str(result.get('output', '')))}</p>"
        f"<h2>Quality metrics</h2><table><tbody>{quality_rows}</tbody></table>"
        f"<h2>Warnings</h2><table><thead><tr><th>Severity</th><th>Code</th><th>Message</th>"
        f"</tr></thead><tbody>{warning_rows}</tbody></table></body></html>",
        encoding="utf-8",
    )
    return html_path
