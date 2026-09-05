# Supported formats

| Format | Adapter | Notes |
|---|---|---|
| TXT | Canonical | Paragraphs are separated by blank lines; no headings are invented. |
| Markdown | Canonical/Pandoc | CommonMark-compatible structure is retained. |
| HTML | Canonical/Pandoc | Scripts, forms, event handlers, JavaScript URLs, and remote images are removed. |
| DOCX | Canonical/Pandoc | Headings, lists, tables, links, and embedded media are retained where Pandoc supports them. |
| PDF | Canonical/PyMuPDF4LLM | Text PDFs produce semantic Reader HTML, extracted images, chapter headings, provenance, and quality metrics. Legacy Vietnamese TCVN3/ABC text is normalized to NFC Unicode. Dense prose can use per-page semantic layout extraction; sparse front matter retains the conservative legacy extractor. Repeated headers, Roman page numbers, split paragraphs, matching footnotes, and ornaments are repaired when detected. Review the preview because reading order remains heuristic. |
| EPUB | Direct | Existing package is preserved and validated. Metadata override is not applied on pass-through. |
| MOBI | Direct/Calibre | Requires `ebook-convert`; DRM is not supported. |

Output is EPUB3 reflowable. Fixed-layout or pixel-perfect PDF reproduction is not a goal.
