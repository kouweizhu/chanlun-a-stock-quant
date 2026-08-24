"""
Alpha Zoo — 精选 A 股验证有效的因子合集

包含：
- GTJA191 在 CSI300 上验证有效的 5 个幸存因子（Top 5 by IC）
- qlib158 的 8 个 K 线形态因子（与缠论语义重叠）

每个因子是一个函数 compute(panel) -> pd.DataFrame。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from base import (
    rank, safe_div, delta, ts_mean, ts_std, ts_corr, vwap, Market,
)

# ── GTJA191 幸存因子（CSI300 2018-2025 验证存活）───────────────────


def gtja191_002(panel: dict) -> pd.DataFrame:
    """Daily change of close-position-within-range.

    Formula: -1 * DELTA(((CLOSE - LOW) - (HIGH - CLOSE)) / (HIGH - LOW), 1)
    Theme: reversal, microstructure
    Columns: close, high, low
    """
    c = panel["close"]
    h = panel["high"]
    l = panel["low"]
    raw = safe_div((c - l) - (h - c), h - l)
    return -1.0 * delta(raw, 1)


def gtja191_054(panel: dict) -> pd.DataFrame:
    """Negated rank of (std|c-o|,10) + (c-o) + corr(c,o,10).

    Formula: -1*RANK((STD(ABS(CLOSE-OPEN),10)+(CLOSE-OPEN))+CORR(CLOSE,OPEN,10))
    Theme: volatility, microstructure
    Columns: close, open
    """
    c = panel["close"]
    o = panel["open"]
    return -1.0 * rank(ts_std((c - o).abs(), 10) + (c - o) + ts_corr(c, o, 10))


def gtja191_111(panel: dict) -> pd.DataFrame:
    """Volume-weighted close-position change, fast-slow SMA difference.

    Formula: sma(v*((c-l)-(h-c))/(h-l),11,2)-sma(v*((c-l)-(h-c))/(h-l),4,2)
    Theme: volume, microstructure
    Columns: open, high, low, close, volume
    """
    def _sma(x, n, m):
        return x.ewm(alpha=m / n, adjust=False).mean()
    c = panel["close"]
    h = panel["high"]
    l = panel["low"]
    v = panel["volume"]
    ratio = safe_div(v * ((c - l) - (h - c)), h - l)
    return _sma(ratio, 11, 2) - _sma(ratio, 4, 2)


def gtja191_163(panel: dict) -> pd.DataFrame:
    """Rank of neg-return * 20d-vol * vwap * (high-close).

    Formula: rank(((-1*ret)*mean(v,20))*vwap*(high-close))
    Theme: volume
    Columns: open, high, low, close, volume, amount (for vwap)
    """
    c = panel["close"]
    h = panel["high"]
    v = panel["volume"]
    vw = vwap(panel, "equity_cn")
    ret = safe_div(c, c.shift(1)) - 1.0
    return rank(((-1.0 * ret) * ts_mean(v, 20)) * vw * (h - c))


def gtja191_171(panel: dict) -> pd.DataFrame:
    """Complex microstructure ratio.

    Formula: -1*((l-c)*(o^5))/((c-h)*(c^5))
    Theme: microstructure
    Columns: open, high, low, close
    Mean IC=0.0432, IR=0.2690 — 最强存活的 GTJA 因子
    """
    c = panel["close"]
    o = panel["open"]
    h = panel["high"]
    l = panel["low"]
    return safe_div(-1.0 * ((l - c) * (o ** 5)), (c - h) * (c ** 5))


# ── qlib158 K 线形态因子（与缠论分型/笔语义重叠）───────────────────


def qlib158_kup(panel: dict) -> pd.DataFrame:
    """Upper shadow / open: (high - max(open,close)) / open"""
    o = panel["open"]
    c = panel["close"]
    h = panel["high"]
    upper = o.where(o >= c, c)
    return safe_div(h - upper, o)


def qlib158_kup2(panel: dict) -> pd.DataFrame:
    """Upper shadow / range: (high - max(open,close)) / (high - low)"""
    o = panel["open"]
    c = panel["close"]
    h = panel["high"]
    lo = panel["low"]
    upper = o.where(o >= c, c)
    return safe_div(h - upper, h - lo)


def qlib158_kmid(panel: dict) -> pd.DataFrame:
    """Body / open: (close - open) / open"""
    o = panel["open"]
    c = panel["close"]
    return safe_div(c - o, o)


def qlib158_kmid2(panel: dict) -> pd.DataFrame:
    """Body / range: (close - open) / (high - low)"""
    o = panel["open"]
    c = panel["close"]
    h = panel["high"]
    lo = panel["low"]
    return safe_div(c - o, h - lo)


def qlib158_klow(panel: dict) -> pd.DataFrame:
    """Lower shadow / open: (min(open,close) - low) / open"""
    o = panel["open"]
    c = panel["close"]
    lo = panel["low"]
    lower = o.where(o <= c, c)
    return safe_div(lower - lo, o)


def qlib158_klow2(panel: dict) -> pd.DataFrame:
    """Lower shadow / range: (min(open,close) - low) / (high - low)"""
    o = panel["open"]
    c = panel["close"]
    h = panel["high"]
    lo = panel["low"]
    lower = o.where(o <= c, c)
    return safe_div(lower - lo, h - lo)


def qlib158_ksft(panel: dict) -> pd.DataFrame:
    """Softness / open: (2*close - high - low) / open"""
    o = panel["open"]
    c = panel["close"]
    h = panel["high"]
    lo = panel["low"]
    return safe_div(2.0 * c - h - lo, o)


def qlib158_ksft2(panel: dict) -> pd.DataFrame:
    """Softness / range: (2*close - high - low) / (high - low)"""
    o = panel["open"]
    c = panel["close"]
    h = panel["high"]
    lo = panel["low"]
    return safe_div(2.0 * c - h - lo, h - lo)


# ── 因子注册表 ──────────────────────────────────────────────────

FACTORS = {
    # GTJA 幸存
    "gtja191_002": {"fn": gtja191_002, "columns": ["close", "high", "low"],
                    "meta": {"theme": ["reversal", "microstructure"], "ic_mean": 0.0262, "ir": 0.1619}},
    "gtja191_054": {"fn": gtja191_054, "columns": ["close", "open"],
                    "meta": {"theme": ["volatility", "microstructure"], "ic_mean": 0.0272, "ir": 0.1606}},
    "gtja191_111": {"fn": gtja191_111, "columns": ["open", "high", "low", "close", "volume"],
                    "meta": {"theme": ["volume", "microstructure"], "ic_mean": 0.0349, "ir": 0.2232}},
    "gtja191_163": {"fn": gtja191_163, "columns": ["open", "high", "low", "close", "volume", "amount"],
                    "meta": {"theme": ["volume"], "ic_mean": 0.0347, "ir": 0.2008}},
    "gtja191_171": {"fn": gtja191_171, "columns": ["open", "high", "low", "close"],
                    "meta": {"theme": ["microstructure"], "ic_mean": 0.0432, "ir": 0.2690}},
    # qlib158 形态
    "qlib158_kup":   {"fn": qlib158_kup,   "columns": ["open", "high", "close"],
                      "meta": {"theme": ["microstructure"], "desc": "上影线/开盘"}},
    "qlib158_kup2":  {"fn": qlib158_kup2,  "columns": ["open", "high", "low", "close"],
                      "meta": {"theme": ["microstructure"], "desc": "上影线/振幅"}},
    "qlib158_kmid":  {"fn": qlib158_kmid,  "columns": ["open", "close"],
                      "meta": {"theme": ["microstructure"], "desc": "实体/开盘"}},
    "qlib158_kmid2": {"fn": qlib158_kmid2, "columns": ["open", "high", "low", "close"],
                      "meta": {"theme": ["microstructure"], "desc": "实体/振幅"}},
    "qlib158_klow":  {"fn": qlib158_klow,  "columns": ["open", "low", "close"],
                      "meta": {"theme": ["microstructure"], "desc": "下影线/开盘"}},
    "qlib158_klow2": {"fn": qlib158_klow2, "columns": ["open", "high", "low", "close"],
                      "meta": {"theme": ["microstructure"], "desc": "下影线/振幅"}},
    "qlib158_ksft":  {"fn": qlib158_ksft,  "columns": ["open", "high", "low", "close"],
                      "meta": {"theme": ["microstructure"], "desc": "软度/开盘"}},
    "qlib158_ksft2": {"fn": qlib158_ksft2, "columns": ["open", "high", "low", "close"],
                      "meta": {"theme": ["microstructure"], "desc": "软度/振幅"}},
}


def list_factors() -> list[str]:
    """返回所有因子 ID 列表。"""
    return list(FACTORS.keys())


def compute(factor_id: str, panel: dict) -> pd.DataFrame:
    """计算单个因子。"""
    entry = FACTORS.get(factor_id)
    if entry is None:
        raise KeyError(f"未知因子: {factor_id}")
    return entry["fn"](panel)


def compute_all(panel: dict, skip_missing_cols: bool = True) -> dict[str, pd.DataFrame]:
    """计算所有面板数据可支持的因子。

    Args:
        panel: DBHub panel dict
        skip_missing_cols: True=跳过需要 amount 的因子（如 gtja191_163）

    Returns:
        {factor_id: result_df}
    """
    results = {}
    for fid, entry in FACTORS.items():
        needed = entry["columns"]
        missing = [c for c in needed if c not in panel]
        if missing:
            if skip_missing_cols:
                continue
            else:
                raise ValueError(f"{fid}: 面板缺少 {missing}")
        try:
            results[fid] = entry["fn"](panel)
        except Exception as e:
            results[fid] = None  # 计算失败
    return results