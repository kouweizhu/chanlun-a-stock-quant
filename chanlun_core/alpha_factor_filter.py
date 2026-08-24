"""
alpha_factor_filter.py — Alpha 因子过滤器 v1.1

在 pool_scanner 产出的候选股上跑 Alpha Zoo 因子，输出 alpha_score + 风控否决信息。

工作流：
  pool_scanner -> scan_results.json -> alpha_factor_filter -> 附加 alpha_score + veto -> composite_scorer

设计：
  - 候选股 30-80 只 -> 从 DBHub 加载 panel -> 跑 4 个 GTJA 幸存因子
  - 每个因子最后一天值的 cross-sectional rank -> 聚合为 alpha_score [0, 100]
  - 可选的 risk_filter 深度检查（慢，靠 AKShare）

用法：
    from alpha_factor_filter import compute_alpha_scores, check_candidate_risks
    scores = compute_alpha_scores(candidates)
    candidates = check_candidate_risks(candidates)
    # 每个 candidate 现在有 alpha_score, veto_reasons, severe_reasons
"""
from __future__ import annotations

import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Alpha Zoo 路径
ALPHA_ZOO_DIR = Path(r"D:/常用文件/DeepSeek Harness项目/trading-skills/alpha-zoo")
if str(ALPHA_ZOO_DIR) not in sys.path:
    sys.path.insert(0, str(ALPHA_ZOO_DIR))

from dbhub_panel import load_panel
from zoo import FACTORS

# risk_filter（可选，需要时导入）
_RISK_FILTER_AVAILABLE = False
try:
    from risk_filter import check_risk  # noqa: F401
    _RISK_FILTER_AVAILABLE = True
except ImportError:
    pass

# 配置
SCAN_CACHE_PATH = Path(__file__).parent / ".scanner_cache.json"
DB_DATA_YEARS = 2

# 使用的 4 个 GTJA 幸存因子（v5.0 按 IC 加权，不再等权）
ACTIVE_FACTORS = [
    "gtja191_002",   # delta(close-position-in-range)  IC=0.0262
    "gtja191_054",   # neg rank of std|c-o|+corr(c,o) IC=0.0272
    "gtja191_111",   # volume-weighted SMA diff         IC=0.0349
    "gtja191_171",   # microstructure ratio             IC=0.0432 (最强)
]

# 因子 IC 权重（v5.0 新增）：按 CSI300 验证的 IC 值加权，强因子占比更高
# 权重 = IC / ΣIC，然后归一化
FACTOR_IC = {
    "gtja191_002": 0.0262,
    "gtja191_054": 0.0272,
    "gtja191_111": 0.0349,
    "gtja191_171": 0.0432,
}
_IC_TOTAL = sum(FACTOR_IC.values()) or 1.0
FACTOR_WEIGHTS = {fid: ic / _IC_TOTAL for fid, ic in FACTOR_IC.items()}


def compute_alpha_scores(
    candidates: list[dict],
    data_start: Optional[str] = None,
    data_end: Optional[str] = None,
) -> dict[str, float]:
    """对候选股列表计算 Alpha 因子排名分。

    Args:
        candidates: pool_scanner 输出的候选股列表
                    ([{code, name, price, score, ...}, ...])
        data_start: 数据起始日期，默认 2 年前
        data_end: 数据截止日期，默认当天

    Returns:
        {stock_code: alpha_score} — alpha_score ∈ [0, 100]
    """
    if not candidates:
        return {}

    codes = [c["code"] for c in candidates]
    seen = set()
    codes_unique = [c for c in codes if c not in seen and not seen.add(c)]  # type: ignore

    if data_end is None:
        data_end = datetime.now().strftime("%Y-%m-%d")
    if data_start is None:
        start_dt = datetime.now() - timedelta(days=365 * DB_DATA_YEARS)
        data_start = start_dt.strftime("%Y-%m-%d")

    print(f"  [alpha_filter] 加载 {len(codes_unique)} 只候选股的 panel...", end=" ")
    try:
        panel = load_panel(codes_unique, data_start, data_end)
    except Exception as e:
        # v5.3.1(P0-3): 捕获所有异常——load_panel 可能抛 sqlite3.OperationalError/
        # KeyError/DBHub 连接失败等, 窄捕获会让子进程非零退出后被编排器忽略,
        # 下游 .get('alpha_score', 50) 静默填中性分(历史 bash-env-prefix 事故同构)。
        import traceback
        print(f"FAIL: {e}")
        traceback.print_exc()
        return {}

    days = len(panel["close"])
    print(f"{days} 个交易日")
    print(f"  [alpha_filter] 计算 {len(ACTIVE_FACTORS)} 个因子")

    factor_scores: dict[str, pd.Series] = {}
    for fid in ACTIVE_FACTORS:
        try:
            df = FACTORS[fid]["fn"](panel)
            latest = df.iloc[-1]
            latest = pd.to_numeric(latest, errors="coerce")
            factor_scores[fid] = latest
        except Exception as e:
            print(f"  WARNING: {fid} 计算失败: {e}")
            continue

    if not factor_scores:
        print("  [alpha_filter] WARNING: 无因子计算成功")
        return {}

    rank_dfs = []
    for fid, series in factor_scores.items():
        rank_series = series.rank(pct=True, na_option="keep")
        rank_dfs.append(rank_series)

    rank_df = pd.concat(rank_dfs, axis=1)
    # v5.0：按 IC 加权平均（等权 → 强因子占比更高）
    # v5.4(C-02): 逐行按"可用因子"重归一化——旧实现只在列层面归一一次(w/w.sum()),
    # 缺因子的股(次新股历史不足/个别因子计算失败)有效权重和<1: 仅剩 gtja191_171
    # 一只因子的股 mean_rank≤0.326 → 映射后≈41分以下 < ALPHA_BUY_THRESHOLD(40)
    # → 被系统性压分强制轻仓(审计实测: 11只候选中此类股 alpha 全部 25~35)。
    # 新口径 sum(w_i*r_i)/sum(w_i 可用): 权重守恒、单因子股得分=其池内排名本身。
    # 全缺行→NaN→中性50, 语义不变。
    computed_ids = [fid for fid in rank_df.columns if fid in FACTOR_WEIGHTS]
    if computed_ids:
        _w = pd.Series({fid: FACTOR_WEIGHTS[fid] for fid in computed_ids})
        _numer = rank_df[computed_ids].mul(_w, axis=1).sum(axis=1, min_count=1)
        _denom = rank_df[computed_ids].notna().mul(_w, axis=1).sum(axis=1)
        mean_rank = _numer / _denom.replace(0, np.nan)
    else:
        mean_rank = rank_df.mean(axis=1, skipna=True)
    valid_counts = rank_df.notna().sum(axis=1)
    min_factors = max(1, len(ACTIVE_FACTORS) // 3)
    mean_rank = mean_rank.where(valid_counts >= min_factors, np.nan)
    # v5.3.2(D-4/A1): 池内 pct rank 直接 ×100 是零和的——21只候选必然有人
    # 0分有人100分, 即使全部是好票(因子IC本是在全市场几千只上验证的, 小池
    # 座次≠绝对质量)。收缩为中心50的[25,75]: 相对强弱保留, 绝对水平不再
    # 两极化, 且与"无数据=中性50"语义自然衔接。
    # 阈值换算: ALPHA_BUY_THRESHOLD 30(旧尺度=池内后30%) → 40(新尺度等价)
    alpha_scores_series = (25 + mean_rank * 50).clip(10, 90).round(1)
    alpha_scores_series = alpha_scores_series.fillna(50.0)

    result = alpha_scores_series.to_dict()
    print(f"  [alpha_filter] 完成: {len(result)} 只股票获得 alpha_score")

    sorted_stocks = sorted(result.items(), key=lambda x: -x[1])
    if sorted_stocks:
        print(f"  [alpha_filter] Top 5:")
        for code, score in sorted_stocks[:5]:
            name = next((c["name"] for c in candidates if c["code"] == code), code)
            print(f"    {code:>6} {name:<6} alpha={score:.1f}")
        print(f"  [alpha_filter] Bottom 3:")
        for code, score in sorted_stocks[-3:]:
            name = next((c["name"] for c in candidates if c["code"] == code), code)
            print(f"    {code:>6} {name:<6} alpha={score:.1f}")

    return result


def attach_scores_to_candidates(
    candidates: list[dict],
    alpha_scores: dict[str, float],
) -> list[dict]:
    """将 alpha_score + veto 字段初始值附加到候选股字典。"""
    for c in candidates:
        c["alpha_score"] = alpha_scores.get(c["code"], 50.0)
        c.setdefault("veto_reasons", [])
        c.setdefault("severe_reasons", [])
    return candidates


def check_candidate_risks(
    candidates: list[dict],
    manual_blacklist: Optional[dict] = None,
) -> list[dict]:
    """对候选股执行风控检查，附加 veto 标记。

    检查项目：
      1. 人工黑名单（config.yaml manual_blacklist）
      2. ST/*ST 名称
      3. risk_filter 深度检查（AKShare，需开启 use_risk_filter）

    Args:
        candidates: 候选股列表
        manual_blacklist: 人工黑名单 {code: reason}

    Returns:
        同列表，每个元素附加了 veto_reasons / severe_reasons
    """
    manual_blacklist = manual_blacklist or {}

    for c in candidates:
        code = c["code"]
        name = c["name"]
        veto = []
        severe = []

        # 1. 人工黑名单
        if code in manual_blacklist:
            veto.append(f"人工黑名单: {manual_blacklist[code]}")

        # 2. ST 名称检查
        if "*ST" in name or "ST" in name.upper():
            veto.append(f"ST股({name})")

        c["veto_reasons"] = veto
        c["severe_reasons"] = severe

    vetoed = sum(1 for c in candidates if c["veto_reasons"])
    sev_cnt = sum(1 for c in candidates if c["severe_reasons"] and not c["veto_reasons"])
    if vetoed or sev_cnt:
        print(f"  [alpha_filter] 风控: {vetoed} 只否决, {sev_cnt} 只严重降级")
        for c in candidates:
            if c["veto_reasons"]:
                print(f"    BLOCK {c['code']} {c['name']}: {'; '.join(c['veto_reasons'])}")
    return candidates


def load_candidates_from_cache() -> list[dict]:
    """从 pool_scanner 的缓存读取最近一次扫描的候选股。"""
    if not SCAN_CACHE_PATH.exists():
        print(f"  [alpha_filter] 未找到扫描缓存: {SCAN_CACHE_PATH}")
        return []
    try:
        # v5.3.1: 显式 utf-8——GBK locale 下 open() 缺省编码读中文 JSON 直接
        # UnicodeDecodeError(编排器子进程 locale 与交互控制台不同, 曾是
        # "手动跑正常、编排跑挂"的元凶之一, 由 P0-2 中止机制首次暴露)
        with open(SCAN_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        candidates = data.get("candidates", [])
        print(f"  [alpha_filter] 从缓存加载 {len(candidates)} 只候选股")
        return candidates
    except (json.JSONDecodeError, IOError) as e:
        print(f"  [alpha_filter] 读取缓存失败: {e}")
        return []


def merge_into_phase2(alpha_scores: dict[str, float],
                       candidates: list[dict]) -> None:
    """将 alpha_score + 风控信息合并到 .phase2_results.json。

    Args:
        alpha_scores: {code: score}
        candidates: 候选股列表（含 veto_reasons/severe_reasons）
    """
    phase2_path = Path(__file__).parent / ".phase2_results.json"
    if not phase2_path.exists():
        return

    with open(phase2_path, "r", encoding="utf-8") as f:
        phase2 = json.load(f)

    score_map = {c["code"]: c for c in candidates}
    updated = 0
    for item in phase2:
        code = item["code"]
        if code in alpha_scores:
            item["alpha_score"] = alpha_scores[code]
            updated += 1
        if code in score_map:
            c = score_map[code]
            item["veto_reasons"] = c.get("veto_reasons", [])
            item["severe_reasons"] = c.get("severe_reasons", [])

    with open(phase2_path, "w", encoding="utf-8") as f:
        json.dump(phase2, f, ensure_ascii=False, indent=2)
    print(f"  [alpha_filter] 已合并到 phase2_results: {updated} 只更新")


def main():
    """独立运行入口：从缓存读候选股 -> 跑因子 -> 风控检查 -> 输出。"""
    candidates = load_candidates_from_cache()
    if not candidates:
        print("请先运行 pool_scanner.py 生成候选股")
        return 1

    print(f"\n{'=' * 50}")
    print("Alpha 因子过滤器 + 风控")
    print(f"{'=' * 50}")
    print(f"候选股: {len(candidates)} 只")
    print(f"数据范围: 最近 {DB_DATA_YEARS} 年")

    scores = compute_alpha_scores(candidates)
    if not scores:
        # v5.3.1(P0-3): alpha 失败必须以非零码退出——编排器 run() 据此中止全流程。
        # 静默 return 会让下游用 alpha=50 中性分照常生成"今日"报告。
        print("[alpha_filter] ❌ alpha 因子计算失败, 以非零码退出(编排器将中止)")
        return 1

    candidates = attach_scores_to_candidates(candidates, scores)
    candidates = check_candidate_risks(candidates)

    # 合并回 phase2_results（供 rescore_news 读取）
    merge_into_phase2(scores, candidates)

    print(f"\n{'=' * 50}")
    print("综合结果（按 alpha_score 降序）:")
    print(f"{'=' * 50}")
    for c in sorted(candidates, key=lambda x: -x.get("alpha_score", 50)):
        marker = " BLOCK" if c.get("veto_reasons") else ""
        print(f"  {c['code']:>6} {c['name']:<6}{marker}"
              f" chanlun={c['score']} alpha={c['alpha_score']:.1f}"
              f" pattern={c['pattern'][:20]}")


if __name__ == "__main__":
    sys.exit(main())  # v5.3.1: 返回值作为进程退出码, 编排器据此判断失败