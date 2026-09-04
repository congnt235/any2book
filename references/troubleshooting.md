# Troubleshooting

- **Missing dependency:** run `any2book doctor`, then rerun `any2book install` to select automatic dependency installation.
- **Scanned PDF:** OCR is outside the MVP; create a searchable PDF first.
- **MOBI fails:** install Calibre and confirm the source is not DRM-protected.
- **EPUBCheck unavailable:** install the EPUBCheck executable; internal validation is less comprehensive.
- **Wrong PDF reading order:** PDF extraction is heuristic. Review preview and keep the report when reporting a fixture.
- **Missing HTML image:** use a local image path; remote images are omitted for privacy and deterministic packaging.
- **AI quota or timeout:** inspect `<job-dir>/state.json`, then repeat the same command with `--resume --job-dir <job-dir>`. Completed batches are not sent again.
- **Resume mismatch:** use the same PDF, AI provider, batch size, confidence, and correction limit as the original run, or start a new job directory.
