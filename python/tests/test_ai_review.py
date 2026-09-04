from any2book_processors.ai_review import _apply_patches


def test_ai_patch_guardrails_apply_unique_high_confidence_only() -> None:
    text = "A machineexecutable rule and an image ![](asset.png)."
    patches = [
        {
            "original": "machineexecutable",
            "replacement": "machine-executable",
            "reason": "dropped line-break hyphen",
            "confidence": 0.95,
        },
        {
            "original": "![](asset.png)",
            "replacement": "![](changed.png)",
            "reason": "not allowed",
            "confidence": 1.0,
        },
        {
            "original": "rule",
            "replacement": "better rule",
            "reason": "low confidence rewrite",
            "confidence": 0.5,
        },
    ]
    corrected, applied, rejected = _apply_patches(text, patches, 0.9)
    assert "machine-executable" in corrected
    assert "asset.png" in corrected
    assert len(applied) == 1
    assert len(rejected) == 2
