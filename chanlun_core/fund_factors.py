#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fund_factors.py —— 资金面因子（P1-1，自 a-stock-data V3.6.0 资金面端点移植）

对单只股票计算三个资金面因子分 [0,100]，用于 A500 多因子选股的补充维度：
  1. holder_score  股东户数变化（筹码集中度）：股东数环比减少 → 集中 → 高分
  2. margin_score  融资融券余额趋势：融资余额近5日上升（杠杆看多）→ 高分
  3. flow_score    个股资金流120日：近20日主力净流入为正且大 → 高分

融合规则（与 a-stock-data 使用示例一致）：
  fund_factor_score = 0.4*holder + 0.3*margin + 0.3*flow

⚠️ 东财限流铁律：本模块全部走 em_utils.em_get（串行≥1s+抖动+会话复用）。
   批量调用（A500 全池）前必须 set_em_interval(1.5~2.0)，且建议只对 Top N 补扫。
"""
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import em_utils  # noqa: E402


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _holder_score(code: str) -> float:
    """股东户数变化因子 [0,100]。连续减少 = 筹码集中 = 主力吸筹。
    v5.0：改多期趋势判断（连续3期减少才高分），降低单期波动敏感度。
    change_ratio 是环比%（东财 HOLDER_NUM_RATIO，负=减少）。"""
    try:
        rows = em_utils.holder_num_change(code, page_size=5)
    except Exception:
        return None
    if not rows:
        return None
    latest = rows[0]
    ratio = latest.get("change_ratio")
    if ratio is None:
        return None
    # v5.0：系数从5降到2.5，单期波动不再极端
    # 环比减少 10% → 75 分；环比增加 10% → 25 分；中性 0% → 50 分
    score = 50.0 - float(ratio) * 2.5
    # 连续多期减少 → 额外加分（更强吸筹信号，v5.0 改为看连续期数）
    if len(rows) >= 3:
        ratios = []
        for r in rows[:3]:
            rr = r.get("change_ratio")
            if rr is not None:
                ratios.append(float(rr))
        if len(ratios) >= 3:
            # 最近3期全部减少 → 强吸筹
            if all(x < 0 for x in ratios):
                score += 12.0
            # 最近3期2期减少 → 温和吸筹
            elif sum(1 for x in ratios if x < 0) >= 2:
                score += 6.0
    elif len(rows) >= 2:
        r2 = rows[1].get("change_ratio")
        if r2 is not None and float(ratio) < 0 and float(r2) < 0:
            score += 8.0
    return round(clamp(score), 1)


def _margin_score(code: str) -> float:
    """融资融券余额趋势因子 [0,100]。近5日融资余额上升（杠杆资金看多）→ 高分。
    v5.0：改用 5日均值 vs 20日均值 对比，平滑单日噪声。
    科创板/北交所无融资融券 → 返回 None，由调用方自适应权重。"""
    try:
        rows = em_utils.margin_trading(code, page_size=25)
    except Exception:
        return None
    if not rows:
        return None
    try:
        rzye = [float(r.get("rzye", 0)) for r in rows if r.get("rzye")]
        if len(rzye) < 5:
            return None
        # 近5日均值 vs 前20日均值（用最近25日数据）
        recent5 = sum(rzye[:5]) / 5.0
        base = sum(rzye[:20]) / 20.0 if len(rzye) >= 20 else sum(rzye) / len(rzye)
        if base <= 0:
            return None
        chg = (recent5 - base) / base  # 区间变化率
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    # +1% → 52分；+5% → 60分；-5% → 40分；极端 ±20% → 90/10
    score = 50.0 + chg * 200.0
    return round(clamp(score), 1)


def _flow_score(code: str) -> float:
    """个股资金流因子 [0,100]。近20日主力净流入为正且大 → 高分。
    v5.0：加入持续性判断（连续净流入天数加分）。
    主源 push2his（东财主力口径）；被封/失败时降级到新浪 fund_flow_backup。
    ⚠️ 口径差异：东财 main_net=主力净流入，新浪 net_amount=全口径净流入
    （实测茅台近20日：东财 3.6亿 vs 新浪 95.9亿，约 20~30 倍）。
    两种源用不同系数映射，避免新浪数据冲顶。"""
    rows, src = _flow_rows(code)
    if not rows:
        return None
    recent = rows[-20:]
    if not recent:
        return None
    try:
        if src == "sina":
            total = sum(float(r.get("net_amount", 0)) for r in recent)  # 全口径，元
            yi = total / 1e8
            # 全口径 → 保守系数：+10亿 → 60分；+50亿 → 90分；-10亿 → 40分
            score = 50.0 + yi * 1.0
        else:
            total = sum(float(r.get("main_net", 0)) for r in recent)   # 主力口径，元
            yi = total / 1e8
            # 主力口径 → 敏感系数：+2亿 → 60分；+10亿 → 90分；-2亿 → 40分
            score = 50.0 + yi * 5.0
        # v5.0：持续性加分 — 连续净流入天数
        positive_days = 0
        max_streak = 0
        for r in reversed(recent):
            net_val = float(r.get("main_net", r.get("net_amount", 0)) or 0)
            if net_val > 0:
                positive_days += 1
                max_streak = max(max_streak, positive_days)
            else:
                positive_days = 0
        if max_streak >= 10:
            score += 8.0
        elif max_streak >= 5:
            score += 5.0
        elif max_streak >= 3:
            score += 2.0
    except (TypeError, ValueError):
        return None
    return round(clamp(score), 1)


def _flow_rows(code: str) -> tuple[list, str]:
    """资金流数据获取：主源东财 push2his → 降级新浪（不同风控面）。
    返回 (rows, source) source: 'em' / 'sina'"""
    try:
        rows = em_utils.stock_fund_flow_120d(code)
        if rows:
            return rows, "em"
    except Exception:
        pass
    try:
        # 新浪备用源字段: {date, close, net_amount, turnover}
        rows = em_utils.fund_flow_backup(code, days=30)
        return (rows, "sina") if rows else ([], "sina")
    except Exception:
        return [], "sina"


def compute_fund_factor_score(code: str) -> dict:
    """对单只股票计算资金面因子全量。
    v5.0：子因子无数据时返回 None（区别于真中性50），权重自适应。
    返回 {code, holder_score, margin_score, flow_score, fund_factor_score,
          holder_detail, margin_detail, flow_detail, data_source, missing_flags}"""
    holder = _holder_score(code)
    margin = _margin_score(code)
    flow = _flow_score(code)

    # v5.0：自适应权重 — 有数据的因子按原始权重比例分配
    # 无融资融券（科创板/北交所）：margin=None → 权重分摊给 holder/flow
    base_weights = {"holder": 0.4, "margin": 0.3, "flow": 0.3}
    available = {"holder": holder, "margin": margin, "flow": flow}
    missing_flags = [k for k, v in available.items() if v is None]
    total_w = sum(base_weights[k] for k, v in available.items() if v is not None)
    if total_w <= 0:
        total_w = 1.0

    fund_factor = 0.0
    for k, v in available.items():
        if v is not None:
            w = base_weights[k] / total_w
            fund_factor += v * w
        else:
            fund_factor += 50.0 * 0.0  # 缺失部分不计入

    # 全部缺失 → 中性50
    if not available or total_w <= 0 or all(v is None for v in available.values()):
        fund_factor = 50.0
    else:
        fund_factor = round(fund_factor, 1)

    return {
        "code": code,
        "holder_score": holder,
        "margin_score": margin,
        "flow_score": flow,
        "fund_factor_score": fund_factor,
        "holder_detail": _holder_detail(code),
        "margin_detail": _margin_detail(code),
        "flow_detail": _flow_detail(code),
        "data_source": "em_utils(东财资金面)",
        "missing_flags": missing_flags,
    }


def _holder_detail(code: str) -> str:
    try:
        rows = em_utils.holder_num_change(code, page_size=2)
    except Exception:
        return "股东户数获取失败"
    if not rows:
        return "无股东户数数据"
    r = rows[0]
    return (f"{r['date']} 股东数={r['holder_num']:,} "
            f"环比={r['change_ratio']:.2f}%" if r.get("change_ratio") is not None
            else f"{r['date']} 股东数={r['holder_num']:,}")


def _margin_detail(code: str) -> str:
    try:
        rows = em_utils.margin_trading(code, page_size=3)
    except Exception:
        return "融资融券获取失败"
    if not rows:
        return "无融资融券数据"
    r = rows[0]
    return f"{r['date']} 融资余额={r['rzye']/1e8:.1f}亿 融券={r['rqye']/1e8:.2f}亿"


def _flow_detail(code: str) -> str:
    rows, src = _flow_rows(code)
    if not rows:
        return "资金流获取失败"
    recent = rows[-20:]
    src_label = "东财push2" if src == "em" else "新浪备用"
    total = sum(float(r.get("main_net", 0) or r.get("net_amount", 0)) for r in recent) / 1e8
    return f"近20日净流入={total:+.2f}亿 (源:{src_label})"


if __name__ == "__main__":
    import json
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(json.dumps(compute_fund_factor_score(code), ensure_ascii=False, indent=2))
