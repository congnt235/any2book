from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import SUPPORTED, detect_format, direct_convert, extract_document, inspect
from .epub import internal_validate, make_preview, render, run_epubcheck, write_reports
from .path_safety import is_path_redirect, path_redirect_component
from .reader_html import write_reader_html

_ARTIFACT_MARKER = ".any2book-artifacts"
_ARTIFACT_MARKER_VALUE = "any2book-artifacts-v1\n"
_OWNED_ARTIFACTS = (
    "reader-html",
    "ai-review",
    "preview",
    "manifest.json",
    "report.json",
    "report.html",
)


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_artifact_marker(marker: Path) -> bool:
    if is_path_redirect(marker):
        raise RuntimeError(f"Invalid Any2Book artifact ownership marker: {marker}")
    if not marker.exists():
        return False
    if not marker.is_file() or marker.read_text(encoding="utf-8") != _ARTIFACT_MARKER_VALUE:
        raise RuntimeError(f"Invalid Any2Book artifact ownership marker: {marker}")
    return True


def _create_artifact_marker(marker: Path) -> None:
    try:
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(_ARTIFACT_MARKER_VALUE)
    except FileExistsError as exc:
        raise RuntimeError(f"Artifact ownership marker changed unexpectedly: {marker}") from exc


def _validate_artifact_dir(output: Path) -> tuple[Path, bool]:
    artifact_dir = output.parent / f"{output.stem}.any2book"
    redirect = path_redirect_component(artifact_dir)
    if redirect:
        raise RuntimeError(f"Artifact path redirects through: {redirect}")
    if artifact_dir.exists() and not artifact_dir.is_dir():
        raise RuntimeError(f"Artifact path is not a safe directory: {artifact_dir}")
    marker = artifact_dir / _ARTIFACT_MARKER
    owned = artifact_dir.is_dir() and _validate_artifact_marker(marker)
    if artifact_dir.is_dir():
        entries = list(artifact_dir.iterdir())
        if entries and not owned:
            raise RuntimeError(
                f"Refusing to overwrite non-empty artifact directory not owned by Any2Book: "
                f"{artifact_dir}"
            )
        if owned:
            for name in _OWNED_ARTIFACTS:
                target = artifact_dir / name
                if is_path_redirect(target):
                    raise RuntimeError(f"Owned artifact path must not redirect elsewhere: {target}")
    return artifact_dir, owned


def _entry_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _stage_output(candidate: Path, staged_output: Path) -> None:
    with candidate.open("rb") as source, staged_output.open("xb") as target:
        shutil.copyfileobj(source, target)
        target.flush()
        os.fsync(target.fileno())


@contextmanager
def _output_lock(output: Path) -> Iterator[None]:
    lock_path = output.parent / f".{output.name}.any2book.lock"
    if path_redirect_component(lock_path):
        raise RuntimeError(f"Output lock path must not redirect elsewhere: {lock_path}")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            lock_region = msvcrt.locking  # type: ignore[attr-defined]
            nonblocking_lock = int(msvcrt.LK_NBLCK)  # type: ignore[attr-defined]
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    lock_region(descriptor, nonblocking_lock, 1)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                unlock_region = msvcrt.locking  # type: ignore[attr-defined]
                unlock_mode = int(msvcrt.LK_UNLCK)  # type: ignore[attr-defined]
                unlock_region(descriptor, unlock_mode, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _commit_conversion_locked(
    staged_output: Path, staged_artifacts: Path, output: Path, artifact_dir: Path
) -> None:
    output_redirect = path_redirect_component(output)
    if output_redirect:
        raise RuntimeError(f"Output path redirects through: {output_redirect}")
    current_artifact_dir, _ = _validate_artifact_dir(output)
    if current_artifact_dir != artifact_dir:
        raise RuntimeError("Artifact path changed unexpectedly before commit")
    if output.exists() and not output.is_file():
        raise RuntimeError(f"Output path is not a regular file: {output}")

    transaction_dir = staged_output.parent
    previous_output = transaction_dir / "output.previous"
    previous_artifacts = transaction_dir / "artifacts.previous"
    output_backed_up = False
    artifacts_backed_up = False
    output_installed = False
    try:
        if _entry_exists(output):
            output.replace(previous_output)
            output_backed_up = True
        if _entry_exists(artifact_dir):
            artifact_dir.replace(previous_artifacts)
            artifacts_backed_up = True
        staged_output.replace(output)
        output_installed = True
        staged_artifacts.replace(artifact_dir)
    except BaseException as commit_error:
        rollback_errors: list[BaseException] = []

        def restore(source: Path, target: Path, enabled: bool) -> None:
            if not enabled or not _entry_exists(source):
                return
            try:
                source.replace(target)
            except BaseException as rollback_error:
                rollback_errors.append(rollback_error)

        restore(output, staged_output, output_installed)
        restore(previous_artifacts, artifact_dir, artifacts_backed_up)
        restore(previous_output, output, output_backed_up)
        if rollback_errors:
            primary_rollback_error = rollback_errors[0]
            primary_rollback_error.add_note(f"Original conversion commit failure: {commit_error!r}")
            for additional_error in rollback_errors[1:]:
                primary_rollback_error.add_note(
                    f"Additional rollback failure: {additional_error!r}"
                )
            raise primary_rollback_error from commit_error
        raise


def _commit_conversion(
    staged_output: Path, staged_artifacts: Path, output: Path, artifact_dir: Path
) -> None:
    """Commit the EPUB and sidecar together, restoring both if either replace fails."""
    with _output_lock(output):
        _commit_conversion_locked(staged_output, staged_artifacts, output, artifact_dir)


def _cleanup_transaction(transaction_dir: Path, commit_completed: bool) -> None:
    recoverable_backups = any(
        _entry_exists(transaction_dir / name) for name in ("output.previous", "artifacts.previous")
    )
    if commit_completed or not recoverable_backups:
        shutil.rmtree(transaction_dir, ignore_errors=True)


def _temporary_directory(*, prefix: str, directory: Path) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir=directory))


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
    output = output.parent.resolve(strict=True) / output.name
    output_redirect = path_redirect_component(output)
    if output_redirect:
        raise RuntimeError(f"Output path redirects through: {output_redirect}")
    _validate_artifact_dir(output)

    metadata = dict(config["book"])
    metadata["source"] = str(input_path)
    adapter = f"{file_format}-adapter"
    canonical_summary: dict[str, object] | None = None
    reader_workspace: Path | None = None
    ai_workspace: Path | None = None
    reader_html_path: Path | None = None
    ai_output_path: Path | None = None
    quality: dict[str, object] = {}
    work_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir: Path | None = None
    transaction_dir: Path | None = None
    commit_completed = False
    try:
        candidate_dir = _temporary_directory(prefix=".any2book-render-", directory=work_dir)
        transaction_dir = _temporary_directory(
            prefix=f".{output.stem}.any2book-transaction-",
            directory=output.parent,
        )
        candidate_output = candidate_dir / "output.epub"
        staged_output = transaction_dir / "output.new"
        staged_artifact_dir = transaction_dir / "artifacts.new"
        if file_format in {"epub", "mobi"}:
            warnings = direct_convert(input_path, file_format, candidate_output)
        else:
            document = extract_document(
                input_path, file_format, work_dir, metadata, config.get("ai")
            )
            warnings = document.warnings
            render(document, candidate_output, work_dir, config)
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
            candidate_ai_workspace = work_dir / "ai-review"
            if candidate_ai_workspace.exists():
                ai_workspace = candidate_ai_workspace

        internal_validate(candidate_output)
        validation = run_epubcheck(candidate_output, warnings)
        artifact_dir, _ = _validate_artifact_dir(output)
        staged_artifact_dir.mkdir()
        _create_artifact_marker(staged_artifact_dir / _ARTIFACT_MARKER)
        if reader_workspace:
            reader_html_path = artifact_dir / "reader-html"
            shutil.copytree(reader_workspace, staged_artifact_dir / "reader-html")
        if ai_workspace:
            ai_output_path = artifact_dir / "ai-review"
            shutil.copytree(ai_workspace, staged_artifact_dir / "ai-review")
        staged_preview = (
            make_preview(candidate_output, staged_artifact_dir / "preview", warnings)
            if config["output"]["preview"]
            else None
        )
        preview_path = artifact_dir / "preview" / "index.html" if staged_preview else None
        report_enabled = bool(config["output"]["report"])
        report_path = artifact_dir / "report.html" if report_enabled else None
        result: dict[str, object] = {
            "status": "success",
            "output": str(output),
            "report": str(report_path) if report_path else None,
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
            "input": {
                "path": str(input_path),
                "format": file_format,
                "sha256": _checksum(input_path),
            },
            "output": {"path": str(output), "sha256": _checksum(candidate_output)},
            "adapter": adapter,
            "config": config,
            "canonicalModel": canonical_summary,
        }
        write_reports(staged_artifact_dir, result, manifest, report_enabled)
        _stage_output(candidate_output, staged_output)
        _commit_conversion(staged_output, staged_artifact_dir, output, artifact_dir)
        commit_completed = True
        return result
    finally:
        if candidate_dir is not None:
            shutil.rmtree(candidate_dir, ignore_errors=True)
        if transaction_dir is not None:
            _cleanup_transaction(transaction_dir, commit_completed)


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
