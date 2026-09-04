from any2book_processors.security import sanitize_html


def test_sanitize_removes_active_content() -> None:
    cleaned, warnings = sanitize_html(
        '<p onclick="steal()">Safe</p><script>alert(1)</script>'
        '<img src="https://example.com/tracker.png">'
    )
    assert "script" not in cleaned
    assert "onclick" not in cleaned
    assert "tracker" not in cleaned
    assert len(warnings) == 3
