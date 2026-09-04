# Any2Book

Any2Book converts personal knowledge files into faithful, reflowable EPUB3 books. It ships as both a standalone CLI and an installable Agent Skill.

## Install

```bash
npx any2book@latest install

# Or install the CLI globally first:
npm install --global any2book
any2book install
```

The `install` command defaults to installing the global CLI and Agent Skill. It detects supported agent CLIs and lets you choose where to install the skill:

- Shared Agent Skills root (`~/.agents/skills`)
- Claude Code
- OpenAI Codex CLI
- Pi
- Gemini CLI
- OpenCode
- Cursor

The installer asks what you want to convert, then derives the implementation dependencies automatically. Users choose needs such as text, Word/HTML, PDF, EPUB, or MOBI rather than choosing Pandoc, uv, or Calibre directly.

For CI or scripted setup:

```bash
any2book install --usage both --agent claude --agent codex --source pdf --source documents --yes
any2book install --usage agent --all --all-sources --yes
any2book install --usage cli --source text --skip-deps --yes
any2book skill-status
any2book uninstall-skill --agent claude
```

`npx any2book@latest install` follows the same explicit installer pattern as BMAD: npx downloads the package temporarily, invokes its `any2book` bin, and passes `install` to the CLI.

## System requirements

- Node.js 22+
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Pandoc](https://pandoc.org/)
- Optional: Calibre (`ebook-convert`) for MOBI
- Recommended: EPUBCheck for complete EPUB3 validation

Python dependencies are isolated under `~/.cache/any2book/venv` by default. Override this with `ANY2BOOK_PYTHON_ENV`.

## CLI

```bash
any2book doctor
any2book inspect notes.md
any2book convert notes.md --output dist/notes.epub --title "My Notes"
any2book convert document.pdf --ai claude --output dist/document.epub
any2book convert document.pdf --ai codex --output dist/document.epub
# Review more aggressively while retaining patch guardrails:
any2book convert document.pdf --ai claude --ai-minimum-confidence 0.85 --output dist/document.epub
any2book preview dist/document.any2book/preview
```

Repeatable conversion:

```bash
cp configs/default.yaml any2book.yaml
any2book convert input.docx --config any2book.yaml
```

Supported inputs are TXT, Markdown, local HTML, DOCX, text-based PDF, EPUB, and MOBI. Scanned PDF/OCR, web URLs, feeds, and media are planned adapters.

For safe EPUB validation, Any2Book requires Expat 2.6.0 or newer and parses at most 64 MiB per XML or CSS resource, 2 MiB per JavaScript resource, 20,000 unique references per resource, and 256 MiB across all parsed resources in one EPUB. XML is capped at 200,000 elements and 4,096 levels per resource, and CSS function nesting is capped at 4,096 levels. JavaScript parsing is interrupted after 2,500,000 parser operations per publication, and AST inspection is capped at 500,000 syntax-node and scope-resolution steps. Embedded data URLs are allowed without separate manifest entries, while EPUB-prohibited package links, top-level browsing links, statically identifiable `window.open`/`document.open` calls and aliases, and `iframe[srcdoc]` content that cannot be safely inspected are rejected. Large binary media remain subject only to the overall archive checks because validation does not load them into memory.

Canonical conversions emit an EPUB plus a per-book `<output-stem>.any2book/` directory containing the semantic `reader-html/` workspace, `manifest.json`, an optional preview, and HTML/JSON reports. PDF reports include estimated text coverage, chapter count, image accounting, and removed layout artifacts.

## AI correction

The default path is deterministic and uses:

- Pandoc for structured formats and EPUB rendering
- PyMuPDF4LLM plus deterministic cleanup for text PDFs, semantic chapters, and image extraction
- Calibre for MOBI when installed

AI is off by default. For PDFs, `--ai claude`, `--ai codex`, or `--ai auto` invokes the user's signed-in Claude Code or Codex CLI—no direct API key is required. Any2Book asks before uploading extracted text; use `--yes-ai` only for explicit non-interactive approval. AI returns conservative JSON patches, and guardrails reject non-unique, low-confidence, image-changing, link-changing, or excessively expansive replacements. Audit output is written under `ai-review/`.

Large PDFs are reviewed in checkpointed page batches (10 pages by default):

```bash
any2book convert large.pdf --ai claude --ai-batch-pages 10 \
  --job-dir .any2book/jobs/large-book --output large.epub
```

Each successful batch is saved atomically. If quota, timeout, or provider errors stop a run, repeat the same conversion with:

```bash
any2book convert large.pdf --ai claude --ai-batch-pages 10 \
  --job-dir .any2book/jobs/large-book --resume --output large.epub
```

Resume validates the extracted source hash, provider, batch size, confidence, and correction limit before reusing completed batches.

## Development

```bash
pnpm install
uv sync
pnpm quality:core
pnpm fixtures:build
npm pack --dry-run
```

See [SKILL.md](SKILL.md) for the agent workflow.
