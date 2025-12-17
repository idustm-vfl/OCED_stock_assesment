from __future__ import annotations

import math
from typing import Iterable, Dict, Any

try:
    import numpy as np
except Exception:  # numpy optional
    np = None


def _as_float_list(series: Iterable[float]) -> list[float]:
    out: list[float] = []
    for x in series:
        try:
            out.append(float(x))
        except Exception:
            continue
    return out


def compute_fft_features(series: Iterable[float]) -> Dict[str, Any]:
    values = _as_float_list(series)
    n = len(values)
    if np is None or n < 4:
        return {
            "dominant_frequency": None,
            "dominant_power": None,
            "spectral_entropy": None,
            "series_len": n,
            "status": "insufficient_history" if n < 4 else "numpy_missing",
        }

    arr = np.array(values, dtype=float)
    # Remove mean to reduce DC component bias
    arr = arr - np.mean(arr)
    fft_vals = np.fft.rfft(arr)
    power = np.abs(fft_vals) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0)

    if power.size == 0:
        return {
            "dominant_frequency": None,
            "dominant_power": None,
            "spectral_entropy": None,
            "series_len": n,
            "status": "no_power",
        }

    # Ignore DC component at index 0 when finding dominant frequency
    dom_idx = 1 if power.size > 1 else 0
    if power.size > 1:
        dom_idx = int(np.argmax(power[1:]) + 1)

    dominant_frequency = float(freqs[dom_idx]) if dom_idx < freqs.size else None
    dominant_power = float(power[dom_idx]) if dom_idx < power.size else None

    # Spectral entropy (Shannon) over normalized power
    power_sum = float(np.sum(power))
    if power_sum <= 0:
        spectral_entropy = None
    else:
        probs = power / power_sum
        spectral_entropy = float(-np.sum(probs * np.log(probs + 1e-12)))

    return {
        "dominant_frequency": dominant_frequency,
        "dominant_power": dominant_power,
        "spectral_entropy": spectral_entropy,
        "series_len": n,
        "status": "ok",
    }


def compute_fractal_features(series: Iterable[float]) -> Dict[str, Any]:
    values = _as_float_list(series)
    n = len(values)
    if n < 3:
        return {
            "roughness": None,
            "hurst_proxy": None,
            "series_len": n,
            "status": "insufficient_history",
        }

    diffs = [abs(values[i] - values[i - 1]) for i in range(1, n)]
    mean_abs_diff = sum(diffs) / len(diffs) if diffs else 0.0
    mean_val = sum(values) / n
    variance = sum((v - mean_val) ** 2 for v in values) / max(1, n - 1)
    std = math.sqrt(variance)

    roughness = mean_abs_diff / (std + 1e-9)

    # Simple Hurst-like proxy using R/S over the series
    cumulative = []
    total = 0.0
    for v in values:
        total += (v - mean_val)
        cumulative.append(total)
    R = max(cumulative) - min(cumulative) if cumulative else 0.0
    S = std if std > 0 else 1e-9
    hurst_proxy = math.log(R / S + 1e-9) / math.log(n + 1e-9)

    return {
        "roughness": roughness,
        "hurst_proxy": hurst_proxy,
        "series_len": n,
        "status": "ok",
    }


def compute_signal_features(series: Iterable[float]) -> Dict[str, Any]:
    return {
        "fft": compute_fft_features(series),
        "fractal": compute_fractal_features(series),
    }
