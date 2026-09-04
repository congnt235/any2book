import json
import os
import shutil
from pathlib import Path

import any2book_processors.cli as cli_module
import pytest
from any2book_processors.cli import (
    _ARTIFACT_MARKER,
    _ARTIFACT_MARKER_VALUE,
    _cleanup_transaction,
    _commit_conversion,
    _create_artifact_marker,
    convert,
)
from any2book_processors.models import BookDocument


def test_artifact_marker_creation_does_not_follow_a_dangling_symlink(tmp_path: Path) -> None:
    marker_target = tmp_path / "missing-marker-target"
    marker = tmp_path / "marker"
    try:
        marker.symlink_to(marker_target)
    except OSError:
        pytest.skip("file symlinks are unavailable on this platform")

    with pytest.raises(RuntimeError, match="changed unexpectedly"):
        _create_artifact_marker(marker)

    assert not marker_target.exists()


def test_conversion_rejects_unowned_sidecar_before_replacing_epub(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("Source text", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output = output_dir / "book.epub"
    output.write_bytes(b"existing epub")
    sidecar = output_dir / "book.any2book"
    sidecar.mkdir()
    sentinel = sidecar / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"output": {"directory": str(output_dir), "filename": "book.epub"}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="not owned by Any2Book"):
        convert(source, config, tmp_path / "work")

    assert output.read_bytes() == b"existing epub"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_conversion_preflights_owned_sidecar_redirects(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("Source text", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output = output_dir / "book.epub"
    output.write_bytes(b"existing epub")
    sidecar = output_dir / "book.any2book"
    sidecar.mkdir()
    (sidecar / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")
    external = tmp_path / "external-preview"
    external.mkdir()
    try:
        (sidecar / "preview").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"output": {"directory": str(output_dir), "filename": "book.epub"}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="must not redirect elsewhere"):
        convert(source, config, tmp_path / "work")

    assert output.read_bytes() == b"existing epub"


def test_conversion_rejects_an_output_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("Source text", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    external = tmp_path / "external-file"
    external.write_bytes(b"keep")
    output = output_dir / "book.epub"
    try:
        output.symlink_to(external)
    except OSError:
        pytest.skip("file symlinks are unavailable on this platform")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"output": {"directory": str(output_dir), "filename": "book.epub"}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Output path redirects through"):
        convert(source, config, tmp_path / "work")

    assert external.read_bytes() == b"keep"


def test_conversion_cleans_render_dir_when_transaction_allocation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("Source text", encoding="utf-8")
    output_dir = tmp_path / "output"
    work_dir = tmp_path / "work"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "book": {"title": "Book", "language": "en"},
                "output": {
                    "directory": str(output_dir),
                    "filename": "book.epub",
                    "preview": False,
                    "report": False,
                },
                "ai": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )
    real_temporary_directory = cli_module._temporary_directory
    allocations = 0

    def fail_transaction_allocation(*, prefix: str, directory: Path) -> Path:
        nonlocal allocations
        allocations += 1
        if allocations == 2:
            raise OSError("simulated transaction allocation failure")
        return real_temporary_directory(prefix=prefix, directory=directory)

    monkeypatch.setattr(cli_module, "_temporary_directory", fail_transaction_allocation)

    with pytest.raises(OSError, match="simulated transaction allocation failure"):
        convert(source, config, work_dir)

    assert list(work_dir.glob(".any2book-render-*")) == []


def test_conversion_uses_a_canonical_output_parent(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "fixtures" / "epub" / "sample.epub"
    output_target = tmp_path / "actual-output"
    output_target.mkdir()
    output_link = tmp_path / "output-link"
    try:
        output_link.symlink_to(output_target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "book": {"title": "Book", "language": "en"},
                "output": {
                    "directory": str(output_link),
                    "filename": "book.epub",
                    "preview": False,
                    "report": False,
                },
                "ai": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )

    result = convert(source, config, tmp_path / "work")

    expected = output_target / "book.epub"
    assert result["output"] == str(expected)
    assert expected.is_file()


def test_conversion_commit_replaces_a_hard_link_without_truncating_its_peer(
    tmp_path: Path,
) -> None:
    peer = tmp_path / "peer.epub"
    peer.write_bytes(b"existing epub")
    output = tmp_path / "book.epub"
    os.link(peer, output)
    transaction = tmp_path / "transaction"
    transaction.mkdir()
    staged_output = transaction / "output.new"
    staged_output.write_bytes(b"new epub")
    staged_artifacts = transaction / "artifacts.new"
    staged_artifacts.mkdir()
    (staged_artifacts / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")

    _commit_conversion(staged_output, staged_artifacts, output, tmp_path / "book.any2book")

    assert output.read_bytes() == b"new epub"
    assert peer.read_bytes() == b"existing epub"


def test_conversion_preserves_previous_result_when_sidecar_staging_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("Source text", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output = output_dir / "book.epub"
    output.write_bytes(b"existing epub")
    sidecar = output_dir / "book.any2book"
    sidecar.mkdir()
    (sidecar / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")
    sentinel = sidecar / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "book": {"title": "Book", "language": "en"},
                "output": {
                    "directory": str(output_dir),
                    "filename": "book.epub",
                    "preview": False,
                    "report": False,
                },
                "ai": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )
    reader_workspace = tmp_path / "work" / "reader-html"
    reader_workspace.mkdir(parents=True)
    document = BookDocument(
        schema_version="2",
        metadata={"title": "Book", "language": "en"},
        chapters=[],
    )
    monkeypatch.setattr(cli_module, "extract_document", lambda *args: document)
    monkeypatch.setattr(cli_module, "write_reader_html", lambda *args: reader_workspace)
    monkeypatch.setattr(
        cli_module, "render", lambda _document, target, *_args: target.write_bytes(b"new epub")
    )
    monkeypatch.setattr(cli_module, "internal_validate", lambda _path: None)
    monkeypatch.setattr(cli_module, "run_epubcheck", lambda _path, _warnings: {"available": False})
    real_copytree = shutil.copytree

    def fail_reader_copy(source_path: Path, destination: Path) -> None:
        if source_path == reader_workspace:
            raise OSError("simulated sidecar copy failure")
        real_copytree(source_path, destination)

    monkeypatch.setattr(shutil, "copytree", fail_reader_copy)

    with pytest.raises(OSError, match="simulated sidecar copy failure"):
        convert(source, config, tmp_path / "work")

    assert output.read_bytes() == b"existing epub"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_conversion_commit_rolls_back_both_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "book.epub"
    output.write_bytes(b"existing epub")
    artifact_dir = tmp_path / "book.any2book"
    artifact_dir.mkdir()
    (artifact_dir / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")
    sentinel = artifact_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    transaction = tmp_path / "transaction"
    transaction.mkdir()
    staged_output = transaction / "output.new"
    staged_output.write_bytes(b"new epub")
    staged_artifacts = transaction / "artifacts.new"
    staged_artifacts.mkdir()
    (staged_artifacts / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")
    original_replace = Path.replace

    def fail_second_install(source: Path, target: Path) -> Path:
        if source == staged_artifacts:
            raise OSError("simulated artifact install failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second_install)

    with pytest.raises(OSError, match="simulated artifact install failure"):
        _commit_conversion(staged_output, staged_artifacts, output, artifact_dir)

    assert output.read_bytes() == b"existing epub"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_conversion_commit_continues_rollback_after_one_restore_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "book.epub"
    output.write_bytes(b"existing epub")
    artifact_dir = tmp_path / "book.any2book"
    artifact_dir.mkdir()
    (artifact_dir / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")
    transaction = tmp_path / "transaction"
    transaction.mkdir()
    staged_output = transaction / "output.new"
    staged_output.write_bytes(b"new epub")
    staged_artifacts = transaction / "artifacts.new"
    staged_artifacts.mkdir()
    (staged_artifacts / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")
    previous_artifacts = transaction / "artifacts.previous"
    original_replace = Path.replace

    def fail_artifact_install_and_restore(source: Path, target: Path) -> Path:
        if source == staged_artifacts:
            raise OSError("simulated artifact install failure")
        if source == previous_artifacts:
            raise OSError("simulated artifact rollback failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_artifact_install_and_restore)

    with pytest.raises(OSError, match="simulated artifact rollback failure"):
        _commit_conversion(staged_output, staged_artifacts, output, artifact_dir)

    assert output.read_bytes() == b"existing epub"
    assert staged_output.read_bytes() == b"new epub"
    assert (previous_artifacts / _ARTIFACT_MARKER).exists()


def test_failed_rollback_preserves_recoverable_transaction_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "book.epub"
    output.write_bytes(b"existing epub")
    artifact_dir = tmp_path / "book.any2book"
    artifact_dir.mkdir()
    (artifact_dir / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")
    transaction = tmp_path / "transaction"
    transaction.mkdir()
    staged_output = transaction / "output.new"
    staged_output.write_bytes(b"new epub")
    staged_artifacts = transaction / "artifacts.new"
    staged_artifacts.mkdir()
    (staged_artifacts / _ARTIFACT_MARKER).write_text(_ARTIFACT_MARKER_VALUE, encoding="utf-8")
    original_replace = Path.replace

    def fail_install_and_rollback(source: Path, target: Path) -> Path:
        if (
            source == staged_artifacts
            or (source == output and target == staged_output)
            or (source == transaction / "output.previous" and target == output)
        ):
            raise OSError("simulated replace failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_install_and_rollback)

    with pytest.raises(OSError, match="simulated replace failure"):
        _commit_conversion(staged_output, staged_artifacts, output, artifact_dir)

    _cleanup_transaction(transaction, commit_completed=False)

    assert transaction.exists()
    assert (transaction / "output.previous").read_bytes() == b"existing epub"
    assert (artifact_dir / _ARTIFACT_MARKER).exists()
