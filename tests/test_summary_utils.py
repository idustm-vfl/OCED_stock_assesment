from __future__ import annotations

from massive_tracker import summary


def test_fmt_returns_na_for_none():
    assert summary._fmt(None) == "N/A"
