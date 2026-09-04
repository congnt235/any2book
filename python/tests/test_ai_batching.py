import json
from pathlib import Path
from typing import Any

import any2book_processors.ai_review as review


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
