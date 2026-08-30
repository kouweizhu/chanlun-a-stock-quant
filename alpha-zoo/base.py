"""
Alpha Zoo base operators — 19 operators for quant factor computation.

All operators act on **wide** pd.DataFrame where index = trading_date
(DatetimeIndex) and columns = instrument_code (str). Each returns a
DataFrame of the same shape — raw scores, NaN preserved.

NaN policy: every operator propagates NaN; no silent fillna(0).
Lookahead ban: delta(df, d) requires d >= 1.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
import pandas as pd


class Market(str, Enum):
    EQUITY_US = "equity_us"
    EQUITY_CN = "equity_cn"
    EQUITY_HK = "equity_hk"
    CRYPTO = "crypto"
    FUTURES = "futures"


def _as_float(df: pd.DataFrame) -> pd.DataFrame:
    if df.dtypes.eq(np.float64).all():
        return df
    return df.astype(np.float64)


def rank(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank per row (axis=1, ties=average, pct=True)."""
    return df.rank(axis=1, method="average", pct=True, na_option="keep")


def scale(df: pd.DataFrame, a: float = 1.0) -> pd.DataFrame:
    """Per-row L1 normalize so sum of absolute values equals a."""
    df = _as_float(df)
    abs_sum = df.abs().sum(axis=1, skipna=True)
    abs_sum = abs_sum.where(abs_sum > 0)
    return df.mul(a).div(abs_sum, axis=0)


def ts_rank(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling rank (last value's rank within n-window), per column."""
    if n < 1:
        raise ValueError(f"ts_rank window must be >= 1, got {n}")

    def _last_rank(arr: np.ndarray) -> float:
        if np.isnan(arr).all():
            return np.nan
        last = arr[-1]
        if np.isnan(last):
            return np.nan
        valid = arr[~np.isnan(arr)]
        if valid.size == 0:
            return np.nan
        less = (valid < last).sum()
        eq = (valid == last).sum()
        rank_avg = less + 0.5 * (eq + 1)
        return float(rank_avg / valid.size)

    return df.rolling(window=n, min_periods=n).apply(_last_rank, raw=True)


def ts_corr(x: pd.DataFrame, y: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling Pearson correlation per column, min_periods=n."""
    if n < 2:
        raise ValueError(f"ts_corr window must be >= 2, got {n}")
    x = _as_float(x)
    y = _as_float(y)
    cols = x.columns.union(y.columns)
    xa = x.reindex(columns=cols)
    ya = y.reindex(columns=cols)
    corr = xa.rolling(window=n, min_periods=n).corr(ya)
    return corr.replace([np.inf, -np.inf], np.nan)


def ts_cov(x: pd.DataFrame, y: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling sample covariance per column, min_periods=n."""
    if n < 2:
        raise ValueError(f"ts_cov window must be >= 2, got {n}")
    x = _as_float(x)
    y = _as_float(y)
    cols = x.columns.union(y.columns)
    xa = x.reindex(columns=cols)
    ya = y.reindex(columns=cols)
    cov = xa.rolling(window=n, min_periods=n).cov(ya)
    return cov.replace([np.inf, -np.inf], np.nan)


def ts_mean(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling mean per column, warmup → NaN."""
    if n < 1:
        raise ValueError(f"ts_mean window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).mean()


def ts_std(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling sample std (ddof=1) per column, warmup → NaN."""
    if n < 2:
        raise ValueError(f"ts_std window must be >= 2, got {n}")
    return df.rolling(window=n, min_periods=n).std(ddof=1)


def ts_max(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling max per column, warmup → NaN."""
    if n < 1:
        raise ValueError(f"ts_max window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).max()


def ts_min(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling min per column, warmup → NaN."""
    if n < 1:
        raise ValueError(f"ts_min window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).min()


def ts_argmax(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling argmax (0-based index), warmup → NaN."""
    if n < 1:
        raise ValueError(f"ts_argmax window must be >= 1, got {n}")

    def _fn(arr):
        if np.isnan(arr).all():
            return np.nan
        return float(np.argmax(np.where(np.isnan(arr), -np.inf, arr)))

    return df.rolling(window=n, min_periods=n).apply(_fn, raw=True)


def ts_argmin(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling argmin (0-based index), warmup → NaN."""
    if n < 1:
        raise ValueError(f"ts_argmin window must be >= 1, got {n}")

    def _fn(arr):
        if np.isnan(arr).all():
            return np.nan
        return float(np.argmin(np.where(np.isnan(arr), np.inf, arr)))

    return df.rolling(window=n, min_periods=n).apply(_fn, raw=True)


def delta(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """First difference at lag d: df - df.shift(d). d >= 1 strictly."""
    if d < 1:
        raise ValueError(f"delta lag must be >= 1 (lookahead ban), got {d}")
    return df - df.shift(d)


def decay_linear(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Linear decay-weighted moving average, weights n, n-1, ..., 1."""
    if n < 1:
        raise ValueError(f"decay_linear window must be >= 1, got {n}")
    weights = np.arange(n, 0, -1, dtype=np.float64)
    weights /= weights.sum()

    def _apply(arr: np.ndarray) -> float:
        if np.isnan(arr).any():
            return np.nan
        return float(np.dot(arr, weights))

    return df.rolling(window=n, min_periods=n).apply(_apply, raw=True)


def signed_power(df: pd.DataFrame, p: float) -> pd.DataFrame:
    """sign(df) * |df|**p — preserves sign, no complex output."""
    arr = df.to_numpy(dtype=np.float64, na_value=np.nan)
    out = np.sign(arr) * np.power(np.abs(arr), p)
    return pd.DataFrame(out, index=df.index, columns=df.columns)


def safe_div(a: pd.DataFrame, b: pd.DataFrame, eps: float = 1e-12) -> pd.DataFrame:
    """Safe division: a / (b + eps * sign(b)). b==0 → NaN."""
    a = _as_float(a)
    b = _as_float(b)
    sign = np.sign(b.to_numpy(dtype=np.float64, na_value=np.nan))
    denom_arr = b.to_numpy(dtype=np.float64, na_value=np.nan) + eps * sign
    denom = pd.DataFrame(denom_arr, index=b.index, columns=b.columns)
    result = a.div(denom)
    return result.replace([np.inf, -np.inf], np.nan)


def vwap(panel: dict[str, pd.DataFrame], market: Market | str) -> pd.DataFrame:
    """Market-aware VWAP-equivalent reference price.

    - equity_cn: (amount * 1000) / (volume * 100 + 1)
    - others: typical price (H+L+C+O)/4
    """
    if isinstance(market, str):
        market = Market(market)

    if "vwap" in panel:
        return panel["vwap"]

    if market is Market.EQUITY_CN:
        if "amount" not in panel or "volume" not in panel:
            raise KeyError("vwap(equity_cn) requires panel['amount'] and panel['volume']")
        return safe_div(panel["amount"] * 1000.0, panel["volume"] * 100.0 + 1.0)

    required = ("open", "high", "low", "close")
    missing = [k for k in required if k not in panel]
    if missing:
        raise KeyError(f"vwap({market.value}) requires panel keys {required}; missing {missing}")
    return (panel["open"] + panel["high"] + panel["low"] + panel["close"]) / 4.0


__all__ = [
    "Market",
    "rank", "scale",
    "ts_rank", "ts_corr", "ts_cov", "ts_mean", "ts_std",
    "ts_max", "ts_min", "ts_argmax", "ts_argmin",
    "delta", "decay_linear", "signed_power", "safe_div", "vwap",
]