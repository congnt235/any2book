---
name: any2book
description: Convert personal knowledge files (PDF, DOCX, TXT, Markdown, HTML, EPUB, or MOBI) into faithful, reflowable EPUB3 books for Apple Books, Kobo, and Google Play Books. Use when a user asks to turn documents or ebooks into an EPUB, prepare content for an ereader, inspect ebook compatibility, or create a personal knowledge book.
---

# Any2Book

Convert files into standards-compliant EPUB3 without summarizing or rewriting their contents.

## Non-negotiable rules

- Preserve meaning, order, and wording. Do not summarize, translate, or invent content.
- Only process content the user is authorized to access.
- Never bypass DRM, authentication, or a paywall.
- Do not claim success unless conversion finishes and validation passes.
- Report every fallback or detected loss from `report.json`.
- Tell the user before `--ai claude`, `--ai codex`, or `--ai auto` uploads extracted text through a signed-in AI CLI.

## Workflow

1. Run `any2book doctor` and explain missing required dependencies.
2. Run `any2book inspect <input>`.
3. If `scanPdf` is true, stop: OCR is not supported in the MVP.
4. Confirm or infer title, author, language, cover, and output path. Ask only for metadata that matters to the user.
5. Convert deterministically, unless the user explicitly requests AI correction:

```bash
any2book convert INPUT \
  --output OUTPUT.epub \
  --title "Book title" \
  --author "Author" \
  --language vi

# Opt-in PDF correction after the user explicitly consents to cloud processing:
any2book convert INPUT.pdf --ai auto --yes-ai --non-interactive --output OUTPUT.epub

# For a large PDF, use a persistent checkpoint and bounded page batches:
any2book convert INPUT.pdf --ai auto --yes-ai --non-interactive \
  --ai-batch-pages 10 --job-dir .any2book/jobs/BOOK --output OUTPUT.epub

# If quota, timeout, or provider errors pause the job, repeat with --resume:
any2book convert INPUT.pdf --ai auto --yes-ai --non-interactive \
  --ai-batch-pages 10 --job-dir .any2book/jobs/BOOK --resume --output OUTPUT.epub
```

For repeatable jobs, copy `configs/default.yaml`, edit it, then pass `--config FILE`.

6. When running as a skill, never pass `--yes-ai` unless the user explicitly requested AI correction or confirmed the upload. Without that flag, stop and ask first.
7. Read `report.json`. Surface EPUBCheck status and all warning/error entries. If AI ran, also review `ai-review/ai-patches.json` and `ai-review/ai-diff.html`.
7. Check the generated HTML preview. Serve it when visual review is requested:

```bash
any2book preview OUTPUT_DIRECTORY/BOOK_NAME.any2book/preview
```

8. Return paths to the EPUB, HTML report, JSON report, and preview.

## Routing

- EPUB: preserve and validate; do not rebuild without reason.
- MOBI: direct conversion through Calibre, then validate.
- TXT, Markdown, HTML, DOCX: normalize to the canonical model, then render EPUB3.
- Text PDF: extract semantic Reader HTML with PyMuPDF4LLM, preserve images, and report coverage; always ask the user to review PDF warnings and preview.
- Scan PDF: unsupported until the OCR adapter is added.

## Dependency behavior

Pandoc is required. Calibre is required only for MOBI and compatibility testing. EPUBCheck is strongly recommended; when absent, Any2Book performs internal package validation and emits `EPUBCHECK_UNAVAILABLE`.

Internal EPUB inspection requires Expat 2.6.0 or newer and supports up to 64 MiB per parsed XML or CSS resource, 2 MiB per JavaScript resource, 20,000 unique references per resource, and 256 MiB total parsed content. XML is capped at 200,000 elements and 4,096 levels per resource, and CSS function nesting is capped at 4,096 levels. JavaScript parsing is interrupted after 2,500,000 parser operations per publication, and AST inspection is capped at 500,000 syntax-node and scope-resolution steps. Embedded data URLs are allowed without separate manifest entries; package links, top-level browsing links, statically identifiable `window.open`/`document.open` calls and aliases that use data URLs, and `iframe[srcdoc]` content that cannot be safely inspected are rejected. Binary media are not charged to the byte budget.

Read `references/supported-formats.md` for format caveats and `references/conversion-policy.md` before handling risky or copyrighted inputs.
