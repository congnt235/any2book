import json
from pathlib import Path
from typing import Any

import any2book_processors.ai_review as review
import pytest


def test_ai_batches_checkpoint_and_resume(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_review(
        markdown: str,
        provider: review.Provider,
        work_dir: Path,
        minimum_confidence: float,
        max_corrections: int,
        timeout_seconds: int,
    ) -> tuple[str, dict[str, object]]:
        calls.append(markdown)
        return markdown.replace("fusedword", "fused-word"), {
            "provider": provider,
            "providerMetadata": {},
            "applied": [],
            "rejected": [],
        }

    monkeypatch.setattr(review, "review_and_correct", fake_review)
    pages = [f"page {index} fusedword" for index in range(1, 6)]
    checkpoint = tmp_path / "checkpoint"
    output, audit = review.review_pages_in_batches(
        pages, "claude", tmp_path / "first", checkpoint, 0.9, 80, 60, 2, False
    )
    assert len(calls) == 3
    assert output.count("fused-word") == 5
    assert audit["totalBatches"] == 3
    state = json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["completedThroughPage"] == 5

    calls.clear()
    resumed, _ = review.review_pages_in_batches(
        pages, "claude", tmp_path / "second", checkpoint, 0.9, 80, 60, 2, True
    )
    assert calls == []
    assert resumed == output


def test_ai_batch_failure_persists_pause_state(tmp_path: Path, monkeypatch: Any) -> None:
    calls = 0

    def failed_review(*args: object, **kwargs: object) -> tuple[str, dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("quota exceeded")
        return str(args[0]), {
            "provider": "codex",
            "providerMetadata": {},
            "applied": [],
            "rejected": [],
        }

    monkeypatch.setattr(review, "review_and_correct", failed_review)
    checkpoint = tmp_path / "checkpoint"
    try:
        review.review_pages_in_batches(
            ["page one", "page two", "page three"],
            "codex",
            tmp_path / "work",
            checkpoint,
            0.9,
            80,
            60,
            1,
            False,
        )
    except RuntimeError as error:
        assert "--resume" in str(error)
    else:
        raise AssertionError("batch failure should stop conversion")
    state = json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "paused"
    assert state["completedThroughPage"] == 1
    assert state["failedPages"] == [2, 2]
    assert "quota exceeded" in state["lastError"]


def test_ai_batching_refuses_to_reset_an_unowned_directory(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    sentinel = checkpoint / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not owned by Any2Book"):
        review.review_pages_in_batches(
            [], "codex", tmp_path / "work", checkpoint, 0.9, 80, 60, 10, False
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_ai_batching_only_clears_owned_checkpoint_files(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(review, "_cli_version", lambda _provider: "test")
    checkpoint = tmp_path / "checkpoint"
    review.review_pages_in_batches(
        [], "codex", tmp_path / "first", checkpoint, 0.9, 80, 60, 10, False
    )
    sentinel = checkpoint / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    review.review_pages_in_batches(
        [], "codex", tmp_path / "second", checkpoint, 0.9, 80, 60, 10, False
    )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))["status"] == (
        "completed"
    )


def test_ai_batching_rejects_a_symlinked_ownership_marker(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    batches = checkpoint / "batches"
    batches.mkdir(parents=True)
    sentinel = batches / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    marker_target = tmp_path / "marker-target"
    marker_target.write_text(review._CHECKPOINT_MARKER_VALUE, encoding="utf-8")
    marker = checkpoint / review._CHECKPOINT_MARKER
    try:
        marker.symlink_to(marker_target)
    except OSError:
        pytest.skip("file symlinks are unavailable on this platform")

    with pytest.raises(RuntimeError, match="Invalid Any2Book checkpoint ownership marker"):
        review.review_pages_in_batches(
            [], "codex", tmp_path / "work", checkpoint, 0.9, 80, 60, 10, False
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_ai_batching_rejects_a_symlinked_checkpoint_root(tmp_path: Path) -> None:
    checkpoint_target = tmp_path / "checkpoint-target"
    batches = checkpoint_target / "batches"
    batches.mkdir(parents=True)
    (checkpoint_target / review._CHECKPOINT_MARKER).write_text(
        review._CHECKPOINT_MARKER_VALUE, encoding="utf-8"
    )
    sentinel = batches / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    checkpoint_link = tmp_path / "checkpoint-link"
    try:
        checkpoint_link.symlink_to(checkpoint_target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(RuntimeError, match="redirects through"):
        review.review_pages_in_batches(
            [], "codex", tmp_path / "work", checkpoint_link, 0.9, 80, 60, 10, False
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_ai_batching_canonicalizes_a_redirected_checkpoint_ancestor(tmp_path: Path) -> None:
    checkpoint_parent = tmp_path / "checkpoint-parent"
    checkpoint = checkpoint_parent / "checkpoint"
    batches = checkpoint / "batches"
    batches.mkdir(parents=True)
    (checkpoint / review._CHECKPOINT_MARKER).write_text(
        review._CHECKPOINT_MARKER_VALUE, encoding="utf-8"
    )
    sentinel = batches / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    parent_link = tmp_path / "parent-link"
    try:
        parent_link.symlink_to(checkpoint_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    output, audit = review.review_pages_in_batches(
        [],
        "codex",
        tmp_path / "work",
        parent_link / "checkpoint",
        0.9,
        80,
        60,
        10,
        False,
    )

    assert output == ""
    assert audit["checkpointDirectory"] == str(checkpoint)
    assert not sentinel.exists()
    state = json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"


def test_ai_batching_rejects_a_dangling_marker_symlink(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    marker_target = tmp_path / "missing-marker-target"
    marker = checkpoint / review._CHECKPOINT_MARKER
    try:
        marker.symlink_to(marker_target)
    except OSError:
        pytest.skip("file symlinks are unavailable on this platform")

    with pytest.raises(RuntimeError, match="Invalid Any2Book checkpoint ownership marker"):
        review.review_pages_in_batches(
            [], "codex", tmp_path / "work", checkpoint, 0.9, 80, 60, 10, True
        )

    assert not marker_target.exists()


def test_ai_batching_resume_rejects_a_symlinked_batches_directory(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(review, "_cli_version", lambda _provider: "test")
    checkpoint = tmp_path / "checkpoint"
    review.review_pages_in_batches(
        [], "codex", tmp_path / "first", checkpoint, 0.9, 80, 60, 10, False
    )
    (checkpoint / "batches").rmdir()
    external_batches = tmp_path / "external-batches"
    external_batches.mkdir()
    sentinel = external_batches / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    try:
        (checkpoint / "batches").symlink_to(external_batches, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(RuntimeError, match="Invalid checkpoint batches directory"):
        review.review_pages_in_batches(
            [], "codex", tmp_path / "second", checkpoint, 0.9, 80, 60, 10, True
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_ai_batching_atomic_state_write_ignores_reserved_temp_symlink(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(review, "_cli_version", lambda _provider: "test")
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / review._CHECKPOINT_MARKER).write_text(
        review._CHECKPOINT_MARKER_VALUE, encoding="utf-8"
    )
    external = tmp_path / "external-state"
    external.write_text("keep", encoding="utf-8")
    try:
        (checkpoint / "state.json.tmp").symlink_to(external)
    except OSError:
        pytest.skip("file symlinks are unavailable on this platform")

    review.review_pages_in_batches(
        [], "codex", tmp_path / "work", checkpoint, 0.9, 80, 60, 10, False
    )

    assert external.read_text(encoding="utf-8") == "keep"
    assert json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))["status"] == (
        "completed"
    )
