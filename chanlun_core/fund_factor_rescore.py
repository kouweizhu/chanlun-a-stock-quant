#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fund_factor_rescore.py —— 资金面因子补扫（P1-1）

读取 Phase 2 缓存 (.phase2_results.json)，对 Top N 候选补扫资金面因子
（股东户数/融资融券/资金流120日），把 fund_factor_score 写入候选并重排。

用法（对齐 rescore_news.py）:
    python fund_factor_rescore.py [--top 30]

融合规则（与四维综合评分兼容）:
    composite 重算时，fund_factor_score 作为独立增强维度（w_fund_factor），
    与 tech/fund/alpha/news 一起加权（默认 w=0.10，会从 fund 中拆借一半）。

⚠️ 东财限流：补扫前 set_em_interval(1.5)，Top 30 × 3 端点 ≈ 90 次请求 ≈ 2-3 分钟。
"""
import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import em_utils  # noqa: E402
from fund_factors import compute_fund_factor_score  # noqa: E402

PHASE2_JSON = os.path.join(_SCRIPT_DIR, ".phase2_results.json")
TOP_N = 30
# 五维权重 v5.0：统一从 config_loader 读取，消除硬编码不一致
# tech=0.35 fund=0.25 alpha=0.20 news=0.10 ff=0.10（权重和 = 1.00）
from config_loader import W_TECH, W_FUND, W_ALPHA, W_NEWS, W_FUND_FACTOR  # noqa: E402


def load_phase2() -> list[dict]:
    if not os.path.exists(PHASE2_JSON):
        print(f"[FundFactor] 未找到 Phase 2 缓存: {PHASE2_JSON}")
        return []
    with open(PHASE2_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def save_phase2(stocks: list[dict]):
    with open(PHASE2_JSON, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)


def recompute_composite(s: dict) -> float:
    """v5.3.1(F3): 已废弃自有线性公式, 改由统一出口 compute_3d_score 承接。
    保留函数名向后兼容(外部若有引用), 内部直接委托。"""
    return _recompute_stock(s)["composite"]


def _recompute_stock(s: dict) -> dict:
    """v5.3.1(F1/F3): 统一走 compute_3d_score——自带 veto 否决检查/共振惩罚/
    buy_level 仓位调整/severe 降级。原自有纯线性公式的缺陷:
    (a) 无 veto 检查 → 被立案调查否决的股票落 Top30 补扫后"复活"成正常候选;
    (b) 无共振惩罚 → tech<60 且 fund<60 双弱股惩罚蒸发、分数抬高;
    (c) tech 缺省 0 其余缺省 50 的不对称默认。
    v5.4(C-06): 重算点必须重应用大盘 regime cap——pool_screener 两个评分点
    都做 min(position, _get_cached_cap()), 此处遗漏会让弱市封顶在五维重算后
    失效(仓位可突破上限)。
    v5.4(M-01): reason 与最终分数同源重建——旧实现保留 Phase3 生成的旧文案
    (alpha 尚未 merge 时的"Alpha因子50分"), 个股报告加权表与理由文字自相矛盾
    (长江电力 62.5 vs 50 实证)。
    """
    from composite_scorer import compute_3d_score, buy_level_from_type, position_reason

    result = compute_3d_score(
        tech_score=s.get("tech_score", 50),
        fund_score=s.get("fund_score", 50),
        alpha_score=s.get("alpha_score", 50),
        news_score=s.get("news_score", 50),
        fund_factor_score=s.get("fund_factor_score"),
        w_tech=W_TECH, w_fund=W_FUND, w_alpha=W_ALPHA, w_news=W_NEWS,
        w_fund_factor=W_FUND_FACTOR,
        code=s["code"], name=s.get("name", ""),
        news_detail=s.get("news_detail", ""),
        resonance_penalty=True,
        # v5.3.1(F1): 重算点必须传 buy_level（缺省 0 = 反转降档 → 全体压仓）
        buy_level=buy_level_from_type(s.get("buy_type", "")),
        # v5.3.1(F2): 五维重算点接通 severe 链——解禁预警等在此阶段
        # 真正进入评分(-15分+限轻仓), 不再只躺字段
        risk_reasons=(s.get("risk_reasons") or []) + (s.get("severe_reasons") or []),
        # v5.3.3(E-1/E-2): 买卖冲突仲裁与观察型标记跨阶段透传
        recent_top_sell=bool(s.get('sell_conflict') or s.get('suppressed_by_sell')),
        observational=bool(s.get('observational')),
    )
    # v5.4(C-06): regime cap 与 pool_screener 同源(惰性导入避免循环依赖)
    try:
        from pool_screener import _get_cached_cap
        _pos = min(result.position, _get_cached_cap())
    except Exception:
        _pos = result.position
    return {
        "composite": result.composite,
        "grade": result.grade,
        "can_buy": result.can_buy,
        "position": _pos,
        "position_pct": f"{_pos*100:.0f}%",
        # v5.4(M-01): 理由文字随最终五维结果重建
        "reason": position_reason(result),
        # v5.3.3(E-1/E-2): 标志回写, 报告层读取
        "sell_conflict": result.components.get('sell_conflict', False),
        "observational": result.components.get('observational', False),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--report", action="store_true",
                    help="重算后重新生成 MD/Excel 评分报告（调用 pool_screener.generate_reports）")
    args = ap.parse_args()
    top_n = args.top

    all_stocks = load_phase2()
    if not all_stocks:
        return 1
    print(f"[FundFactor] 共 {len(all_stocks)} 只候选，补扫 Top {top_n} 资金面因子")

    # v5.3.1(F11): 清除本次 top 范围之外的残留 ff 分——上次 --top 30 本次
    # --top 10 时, rank 11-30 的旧 ff 分会残留并参与五维重排(口径混排)
    for idx, s in enumerate(all_stocks):
        if idx >= top_n:
            for k in ("fund_factor_score", "holder_score", "margin_score",
                      "flow_score", "fund_factor_detail", "fund_factor_degraded"):
                s.pop(k, None)

    em_utils.set_em_interval(1.5)  # 批量场景调大间隔防封

    top = all_stocks[:top_n]
    ok = 0
    for i, s in enumerate(top):
        code = s["code"]
        name = s.get("name", code)
        try:
            ff = compute_fund_factor_score(code)
            s["fund_factor_score"] = ff["fund_factor_score"]
            s["holder_score"] = ff["holder_score"]
            s["margin_score"] = ff["margin_score"]
            s["flow_score"] = ff["flow_score"]
            s["fund_factor_detail"] = (
                f"筹码:{ff['holder_detail']} | 两融:{ff['margin_detail']} | {ff['flow_detail']}")
            ok += 1
            print(f"  [{i+1}/{top_n}] {code} {name}: fund_factor={ff['fund_factor_score']} "
                  f"(筹码{ff['holder_score']}/两融{ff['margin_score']}/资金流{ff['flow_score']})")
        except Exception as e:
            s["fund_factor_score"] = 50.0
            s["fund_factor_detail"] = f"获取失败({str(e)[:40]})"
            # v5.3.1(F11): 标记降级——"无数据的50"不再与真实中位排名同形,
            # 报告/后续分析可识别
            s["fund_factor_degraded"] = True
            print(f"  [{i+1}/{top_n}] {code} {name}: FAIL {str(e)[:60]}")

    # v5.4(C-09/性能#11): 新入围增量消息面补扫——Alpha 洗牌/--top 变化后新进
    # TopN 的股可能从未跑过消息面(news_detail 缺失或为占位符), 会以缺省50分
    # 假中性混进推荐。仅对缺失者补扫, 通常 0-5 只; 失败者打标供报告层警示。
    try:
        from news_scanner import scan_news as _scan_news
        _need_news = [s for s in top
                      if not s.get("news_detail")
                      or s.get("news_detail") == "跳过(非Top30)"]
        if _need_news:
            print(f"[FundFactor] C-09: {len(_need_news)} 只新入围股缺少消息面, 增量补扫...")
        for s in _need_news:
            try:
                _ns, _nd = _scan_news(s["code"], s.get("name", ""))
                s["news_score"] = round(_ns, 1)
                s["news_detail"] = _nd
                print(f"  [C-09补扫] {s['code']} {s.get('name','')}: news={_ns:.0f}")
            except Exception as e:
                s["news_scan_failed"] = True
                print(f"  [C-09补扫] {s['code']} {s.get('name','')}: FAIL {str(e)[:60]}")
    except ImportError:
        print("[FundFactor] C-09: news_scanner 不可用, 跳过增量补扫")

    # 重算综合分 + 重排
    # v5.3.1(F1/F3): 统一 compute_3d_score 出口, grade/position/position_pct 同源更新
    # v5.4(M-02): 对全体 all_stocks 无条件重算——旧实现仅重算带 fund_factor_score
    # 的股票, F11 清掉范围外 ff 分后这些股保留旧口径 composite, 与新口径 TopN
    # 混排同一榜单; 无 ff 者由 compute_3d_score 按 ff=None 自适应权重走四维归一。
    for s in all_stocks:
        r = _recompute_stock(s)
        s.update(r)
    all_stocks.sort(key=lambda x: x.get("composite", 0), reverse=True)

    save_phase2(all_stocks)

    print(f"\n[FundFactor] 完成: {ok}/{top_n} 只成功，已写回 {PHASE2_JSON}")
    print("资金面因子对 Top 30 排序影响（前 5）:")
    for i, s in enumerate(all_stocks[:5]):
        ff = s.get("fund_factor_score", "-")
        print(f"  {i+1}. {s['code']} {s.get('name','')} composite={s.get('composite')} fund_factor={ff}")

    # --report: 重新生成 MD/Excel 评分报告（让五维 composite 反映到报告里）
    if args.report:
        print("\n[FundFactor] 重新生成评分报告（五维口径）...")
        try:
            from pool_screener import generate_reports
            generate_reports(all_stocks)
            print("[FundFactor] 报告已重新生成")
        except Exception as e:
            print(f"[FundFactor] 报告生成失败（不影响 phase2 写回）: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
