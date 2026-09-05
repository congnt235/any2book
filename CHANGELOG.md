# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Recognize headed name/role lists from PDF baselines and section-local column alignment,
  retaining empty role cells and legacy Vietnamese font case in borderless EPUB tables.
- Report recognized roster pages, groups, and rows; retain the existing extraction path
  for ambiguous layouts, prose columns, rotated text, and pages containing images.

### Fixed

- Exclude HTML tags from word-count coverage and prevent roster tables from being joined
  to prose on the following page.

## [0.2.2] - 2026-09-05

### Fixed

- Read the CLI and backend version from package metadata so reported conversion provenance stays
  synchronized with the published release.

## [0.2.1] - 2026-09-05

### Fixed

- Prevent duplicate XHTML identifiers when Markdown code blocks pass through Pandoc's
  intermediate HTML conversion and final EPUB syntax-highlighting stages.
- Reject duplicate content identifiers during internal EPUB validation, even when the external
  EPUBCheck tool is unavailable.

## [0.2.0] - 2026-09-05

### Added

- Normalize legacy Vietnamese TCVN3/ABC PDF text layers to NFC Unicode.
- Recover visual uppercase text from legacy all-caps PDF font variants.
- Report layout selection, Unicode normalization, font-case recovery, repeated headers,
  Roman page numbers, footnotes, ornaments, and cross-page paragraph repairs.

### Changed

- Select semantic layout extraction per page when it preserves text coverage and materially
  reduces paragraph fragmentation, while retaining legacy extraction for sparse front matter.
- Style decorative ornaments consistently in reflowable EPUB output.

### Fixed

- Remove repeated running headers and Roman page numbers from extracted PDF content.
- Rejoin prose paragraphs split at PDF page boundaries without merging completed sentences.
- Convert matching inline note markers and note bodies into navigable EPUB footnotes.
- Prevent decorative asterisks from becoming empty or malformed lists.

## [0.1.1] - 2026-09-05

### Fixed

- Harden conversion, validation, output staging, preview serving, AI checkpoints, and installer
  behavior.
- Correct the npm executable path for global installations.

[Unreleased]: https://github.com/congnt235/any2book/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/congnt235/any2book/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/congnt235/any2book/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/congnt235/any2book/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/congnt235/any2book/releases/tag/v0.1.1
