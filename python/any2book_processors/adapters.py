from __future__ import annotations

import html
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, cast

import pymupdf as fitz
import pymupdf4llm  # type: ignore[import-untyped]

from .ai_review import Provider, review_pages_in_batches
from .models import BookDocument, Chapter, ConversionWarning
from .security import sanitize_html

SUPPORTED = {"txt", "markdown", "html", "docx", "pdf", "epub", "mobi"}
EXTENSIONS = {
    ".txt": "txt",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".docx": "docx",
    ".pdf": "pdf",
    ".epub": "epub",
    ".mobi": "mobi",
}


def detect_format(path: Path) -> str:
    head = path.read_bytes()[:16]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"BOOKMOBI") or b"BOOKMOBI" in path.read_bytes()[:128]:
        return "mobi"
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "mimetype" in names and archive.read("mimetype") == b"application/epub+zip":
                return "epub"
            if "word/document.xml" in names:
                return "docx"
    suffix_format = EXTENSIONS.get(path.suffix.lower())
    if suffix_format:
        return suffix_format
    sample = head.lower().lstrip()
    if sample.startswith((b"<!doctype html", b"<html")):
        return "html"
    return "unsupported"


def inspect(path: Path) -> dict[str, object]:
    file_format = detect_format(path)
    result: dict[str, object] = {
        "path": str(path.resolve()),
        "format": file_format,
        "adapter": f"{file_format}-adapter",
        "size": path.stat().st_size,
        "supported": file_format in SUPPORTED,
    }
    if file_format == "pdf":
        with fitz.open(path) as document:
            sample = "".join(
                document.load_page(index).get_text() for index in range(min(document.page_count, 5))
            )
        result["scanPdf"] = len(sample.strip()) < 40
    return result


def _run(command: list[str], cwd: Path | None = None) -> None:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing dependency: {command[0]}. Run `any2book doctor`.") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Command failed: {' '.join(command)}")


def _body(value: str) -> str:
    match = re.search(r"<body[^>]*>(.*?)</body>", value, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else value.strip()


def _plain_heading(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"\s+", " ", value).strip()


def _chapters_from_html(value: str, title: str, source: str) -> list[Chapter]:
    body = _body(value)
    headings = list(re.finditer(r"<h1(?:\s[^>]*)?>(.*?)</h1>", body, re.IGNORECASE | re.DOTALL))
    if not headings:
        return [Chapter(title, body, source)]
    chapters: list[Chapter] = []
    preamble = body[: headings[0].start()].strip()
    if _plain_heading(preamble):
        chapters.append(Chapter(title, preamble, source))
    for index, heading in enumerate(headings):
        chapter_title = _plain_heading(heading.group(1)) or title
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        chapter_body = body[heading.end() : end].strip()
        if not _plain_heading(chapter_body):
            continue
        if chapters and chapters[-1].title.casefold() == chapter_title.casefold():
            chapters[-1].html = f"{chapters[-1].html}\n{chapter_body}"
        else:
            chapters.append(Chapter(chapter_title, chapter_body, source))
    return chapters or [Chapter(title, body, source)]


def _pandoc_document(
    path: Path, file_format: str, work_dir: Path, metadata: dict[str, Any]
) -> BookDocument:
    source = path
    warnings: list[ConversionWarning] = []
    if file_format == "html":
        cleaned, notices = sanitize_html(path.read_text(encoding="utf-8", errors="replace"))
        source = work_dir / "sanitized-input.html"
        source.write_text(cleaned, encoding="utf-8")
        warnings.extend(ConversionWarning("HTML_SANITIZED", message) for message in notices)

    content = work_dir / "content.html"
    assets = work_dir / "assets"
    from_format = {"markdown": "commonmark_x", "html": "html", "docx": "docx"}[file_format]
    _run(
        [
            "pandoc",
            str(source),
            f"--from={from_format}",
            "--to=html5",
            "--standalone",
            f"--extract-media={assets}",
            f"--resource-path={path.parent}",
            "--output",
            str(content),
        ]
    )
    raw = content.read_text(encoding="utf-8")
    cleaned, notices = sanitize_html(raw)
    warnings.extend(ConversionWarning("HTML_SANITIZED", message) for message in notices)
    asset_files = [str(item.relative_to(work_dir)) for item in assets.rglob("*") if item.is_file()]
    return BookDocument(
        schema_version="2",
        metadata=metadata,
        chapters=_chapters_from_html(cleaned, str(metadata["title"]), str(path)),
        assets=asset_files,
        source={"path": str(path), "format": file_format},
        warnings=warnings,
    )


def _text_document(path: Path, metadata: dict[str, Any]) -> BookDocument:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    content = "\n".join(
        f"<p>{html.escape(part).replace(chr(10), '<br />')}</p>" for part in paragraphs
    )
    return BookDocument(
        schema_version="2",
        metadata=metadata,
        chapters=[Chapter(str(metadata["title"]), content, str(path))],
        source={"path": str(path), "format": "txt"},
    )


def _clean_pdf_markdown(value: str) -> tuple[str, dict[str, int]]:
    lines = value.replace("\r\n", "\n").splitlines()
    standalone_numbers = sum(bool(re.fullmatch(r"\s*\d{1,4}\s*", line)) for line in lines)
    remove_line_numbers = standalone_numbers >= 10
    cleaned: list[str] = []
    removed_numbers = 0
    removed_page_markers = 0
    for line in lines:
        if re.fullmatch(r"\s*\d+\s+of\s+\d+\s*", line, re.IGNORECASE):
            removed_page_markers += 1
            continue
        if remove_line_numbers and re.fullmatch(r"\s*\d{1,4}\s*", line):
            removed_numbers += 1
            continue
        section = re.match(r"^\s*(?:[-*]\s+)?\*\*<u>((?:\d+\.)\s+[^<]+)</u>\*\*\s*(.*)$", line)
        if section:
            heading, remainder = section.groups()
            cleaned.extend([f"# {heading.strip()}", ""])
            if remainder.strip():
                cleaned.extend([remainder.strip(), ""])
            continue
        title = re.match(r"^\s*\*\*<u>([^<]+)</u>\*\*\s*$", line)
        if title and not any(item.strip() for item in cleaned):
            cleaned.extend([f"# {title.group(1).strip()}", ""])
            continue
        line = re.sub(r"^(#{1,6})\s*<u>(.*?)</u>\s*$", r"\1 \2", line)
        cleaned.append(line.rstrip())
    value = "\n".join(cleaned)
    value = re.sub(r"\n{4,}", "\n\n\n", value).strip() + "\n"
    return value, {
        "removedLineNumbers": removed_numbers,
        "removedPageMarkers": removed_page_markers,
    }


def _word_count(value: str) -> int:
    without_images = re.sub(r"!\[[^]]*]\([^)]*\)", "", value)
    without_markup = re.sub(r"[<>*_#`\[\]()]", " ", without_images)
    return len(re.findall(r"\b\w+\b", without_markup, re.UNICODE))


def _pdf_document(
    path: Path,
    work_dir: Path,
    metadata: dict[str, Any],
    ai_config: dict[str, Any],
) -> BookDocument:
    document = fitz.open(path)
    source_text = "".join(
        document.load_page(index).get_text() for index in range(document.page_count)
    )
    if len(source_text.strip()) < 40:
        raise RuntimeError("Scanned PDF detected. OCR is not included in the MVP.")

    image_occurrences: list[int] = []
    for page_index in range(document.page_count):
        image_occurrences.extend(
            image[0] for image in document.load_page(page_index).get_images(full=True)
        )
    unique_images = len(set(image_occurrences))
    assets = work_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    # Legacy mode currently handles line-numbered technical documents more faithfully than
    # the optional neural layout engine, which can misclassify margin numbers as list items.
    pymupdf4llm.use_layout(False)
    chunks = pymupdf4llm.to_markdown(
        str(path),
        page_chunks=True,
        write_images=True,
        image_path=str(assets),
        table_strategy="lines_strict",
        show_progress=False,
    )
    if not isinstance(chunks, list):
        raise RuntimeError("PyMuPDF4LLM did not return page chunks")
    generated_images = [item for item in assets.rglob("*") if item.is_file()]
    raw_pages = [
        str(chunk.get("text", "")).replace(str(assets), "assets") for chunk in chunks
    ]
    separator = "\n\n<!-- A2B_PAGE_BREAK -->\n\n"
    cleaned_with_markers, cleanup = _clean_pdf_markdown(separator.join(raw_pages))
    cleaned_pages = [
        part.strip() + "\n" for part in cleaned_with_markers.split("<!-- A2B_PAGE_BREAK -->")
    ]
    if len(cleaned_pages) != len(raw_pages):
        raise RuntimeError("Could not preserve PDF page boundaries for AI batching")
    cleaned_markdown = "\n\n".join(cleaned_pages)
    ai_audit: dict[str, object] | None = None
    provider = str(ai_config.get("provider", "off"))
    if provider in {"claude", "codex"}:
        job_value = ai_config.get("jobDirectory")
        if not isinstance(job_value, str) or not job_value:
            raise RuntimeError("AI batching requires a persistent jobDirectory")
        cleaned_markdown, ai_audit = review_pages_in_batches(
            cleaned_pages,
            cast(Provider, provider),
            work_dir,
            Path(job_value),
            float(ai_config.get("minimumConfidence", 0.9)),
            int(ai_config.get("maxCorrections", 80)),
            int(ai_config.get("timeoutSeconds", 600)),
            int(ai_config.get("batchPages", 10)),
            bool(ai_config.get("resume", False)),
        )
    markdown_path = work_dir / "pdf-reader.md"
    markdown_path.write_text(cleaned_markdown, encoding="utf-8")

    result = _pandoc_document(markdown_path, "markdown", work_dir, metadata)
    extracted_images = len(generated_images)
    source_words = _word_count(source_text)
    extracted_words = _word_count(cleaned_markdown)
    raw_coverage = min(1.0, extracted_words / source_words) if source_words else 0.0
    meaningful_source_words = max(
        1,
        source_words - cleanup["removedLineNumbers"] - cleanup["removedPageMarkers"] * 3,
    )
    coverage = min(1.0, extracted_words / meaningful_source_words)
    result.source = {"path": str(path), "format": "pdf", "pages": document.page_count}
    result.provenance = [
        {
            "sourcePage": int(chunk.get("metadata", {}).get("page_number", index + 1)),
            "textCharacters": len(str(chunk.get("text", ""))),
            "engine": "pymupdf4llm-legacy",
        }
        for index, chunk in enumerate(chunks)
    ]
    ai_applied = ai_audit.get("applied", []) if ai_audit else []
    ai_rejected = ai_audit.get("rejected", []) if ai_audit else []
    if not isinstance(ai_applied, list) or not isinstance(ai_rejected, list):
        raise RuntimeError("Invalid AI correction audit")
    result.quality = {
        "textCoverage": round(coverage, 4),
        "rawTextCoverage": round(raw_coverage, 4),
        "sourceWords": source_words,
        "meaningfulSourceWords": meaningful_source_words,
        "extractedWords": extracted_words,
        "sourcePages": document.page_count,
        "chapters": len(result.chapters),
        "sourceImageOccurrences": len(image_occurrences),
        "sourceUniqueImages": unique_images,
        "embeddedImages": extracted_images,
        "duplicateImageOccurrences": max(0, len(image_occurrences) - unique_images),
        "unaccountedUniqueImages": max(0, unique_images - extracted_images),
        "aiProvider": provider,
        "aiCorrectionsApplied": len(ai_applied),
        "aiCorrectionsRejected": len(ai_rejected),
        "aiBatchPages": int(ai_config.get("batchPages", 10)) if ai_audit else 0,
        "aiTotalBatches": ai_audit.get("totalBatches", 0) if ai_audit else 0,
        "aiEstimatedCostUsd": ai_audit.get("estimatedCostUsd") if ai_audit else None,
        "aiDurationMs": ai_audit.get("durationMs") if ai_audit else None,
        "aiCheckpointDirectory": ai_audit.get("checkpointDirectory") if ai_audit else None,
        **cleanup,
    }
    if ai_audit:
        result.warnings.append(
            ConversionWarning(
                "AI_CORRECTIONS_APPLIED",
                f"Applied {len(ai_applied)} conservative corrections from {provider} CLI.",
                "info",
            )
        )
    result.warnings.append(
        ConversionWarning(
            "PDF_STRUCTURED_EXTRACTION",
            "PDF structure was reconstructed with PyMuPDF4LLM; "
            "review low-confidence layout in the preview.",
            "info",
        )
    )
    if extracted_images < unique_images:
        result.warnings.append(
            ConversionWarning(
                "PDF_IMAGE_COVERAGE",
                f"Extracted {extracted_images} of {unique_images} unique PDF image objects.",
            )
        )
    if coverage < 0.95:
        result.warnings.append(
            ConversionWarning(
                "PDF_TEXT_COVERAGE_LOW",
                f"Estimated text coverage is {coverage:.1%}; inspect the conversion report.",
            )
        )
    return result


def extract_document(
    path: Path,
    file_format: str,
    work_dir: Path,
    metadata: dict[str, Any],
    ai_config: dict[str, Any] | None = None,
) -> BookDocument:
    if file_format == "txt":
        return _text_document(path, metadata)
    if file_format in {"markdown", "html", "docx"}:
        return _pandoc_document(path, file_format, work_dir, metadata)
    if file_format == "pdf":
        return _pdf_document(path, work_dir, metadata, ai_config or {"provider": "off"})
    raise ValueError(f"No canonical adapter for {file_format}")


def direct_convert(path: Path, file_format: str, output: Path) -> list[ConversionWarning]:
    if file_format == "epub":
        with zipfile.ZipFile(path) as archive:
            if "META-INF/encryption.xml" in archive.namelist():
                encryption = archive.read("META-INF/encryption.xml").decode("utf-8", "replace")
                allowed = ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC")
                algorithms = re.findall(r'Algorithm=["\']([^"\']+)', encryption)
                if any(algorithm not in allowed for algorithm in algorithms):
                    raise RuntimeError("Encrypted or DRM-protected EPUB is not supported")
        if path.resolve() != output.resolve():
            shutil.copy2(path, output)
        return [
            ConversionWarning(
                "EPUB_PASSTHROUGH",
                "Existing EPUB was preserved; metadata overrides were not applied.",
                "info",
            )
        ]
    if file_format == "mobi":
        _run(["ebook-convert", str(path), str(output)])
        return []
    raise ValueError(f"No direct adapter for {file_format}")
