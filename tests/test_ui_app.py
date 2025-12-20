"""Lightweight tests for ui_app helpers without launching Streamlit."""
from __future__ import annotations

from types import SimpleNamespace

import massive_tracker.ui_app as ui


def test_cost_bucket_boundaries() -> None:
    assert ui._cost_bucket(None) == "unknown"
    assert ui._cost_bucket(4000) == "≤ $5k"
    assert ui._cost_bucket(8000) == "≤ $10k"
    assert ui._cost_bucket(20000) == "≤ $25k"
    assert ui._cost_bucket(50000) == "> $25k"


def test_apply_theme_smoke(monkeypatch) -> None:
    """Ensure the theme helper runs without raising when Streamlit is stubbed."""

    calls = []

    class FakeStreamlit:
        def set_page_config(self, **kwargs):
            calls.append(("set_page_config", kwargs))

        def markdown(self, *_args, **_kwargs):
            calls.append(("markdown", {}))

    fake_st = FakeStreamlit()
    orig_st = ui.st
    try:
        monkeypatch.setattr(ui, "st", fake_st)
        ui._apply_theme()
    finally:
        monkeypatch.setattr(ui, "st", orig_st)

    assert calls, "theme helper did not invoke streamlit API stubs"
