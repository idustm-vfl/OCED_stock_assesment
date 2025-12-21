from __future__ import annotations

import massive_tracker.picker as picker


def test_select_chain_option_filters_itm_and_spread(monkeypatch):
    def fake_chain(ticker: str, expiry: str):
        return [
            {"strike": 95, "mid": 1.5, "bid": 1.4, "ask": 1.6, "delta": 0.25},  # ITM, ignored
            {"strike": 110, "mid": 2.5, "bid": 2.3, "ask": 2.7, "delta": 0.25},
            {"strike": 120, "mid": 0.5, "bid": 0.0, "ask": 1.5, "delta": 0.60},  # delta too high
        ]

    monkeypatch.setattr(picker, "get_chain_quotes", fake_chain)

    picked, status = picker._select_chain_option(
        ticker="TEST",
        price=100.0,
        lane="SAFE",
        expiry="2025-12-26",
        target_strike=105.0,
    )

    assert status == "ok"
    assert picked is not None
    assert picked["strike"] == 110
    assert picked["prem_yield"] > 0
