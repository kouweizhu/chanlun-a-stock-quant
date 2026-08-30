#!/usr/bin/env python
"""
extreme_market_backtest.py — 极端行情回测验证
对 2015/2018/2020/2024 四个极端年份跑回测，验证系统在崩盘时不死扛。

核心检测：
  2015: 6-8月股灾 → 卖点/止损是否在暴跌前触发？
  2018: 全年阴跌 → 是否频繁假信号反复止损？
  2020: 2-3月急跌 → 是否及时离场？后续反弹是否重新入场？
  2024: 9月底暴涨 → 是否有买点信号？

用法:
    python extreme_market_backtest.py
    python extreme_market_backtest.py --output extreme_results.csv
"""

import sys, os, csv, json
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_engine import run_single

# ============================================================
# 配置
# ============================================================

# 10 只跨越全部测试周期的老蓝筹（2014年前上市）
UNIVERSE_STOCKS = [
    ("000001", "平安银行"),
    ("000651", "格力电器"),
    ("000858", "五粮液"),
    ("000333", "美的集团"),
    ("600036", "招商银行"),
    ("600519", "贵州茅台"),
    ("601318", "中国平安"),
    ("600900", "长江电力"),
    ("601166", "兴业银行"),
    ("002594", "比亚迪"),
]

# 四个极端行情周期
PERIODS = [
    {
        "name": "2015 牛转熊",
        "start": "2015-01-01",
        "end": "2015-12-31",
        "hs300_return": 5.58,      # 沪深300 2015全年涨幅（过山车）
        "hs300_peak_dd": -43.5,    # 沪深300 6-8月最大回撤
        "key_question": "6月中旬股灾前是否已清仓？",
        "pass_criteria": "最大回撤 < 沪深300回撤的50%（即 > -22%）",
    },
    {
        "name": "2018 单边下跌",
        "start": "2018-01-01",
        "end": "2018-12-31",
        "hs300_return": -25.31,
        "hs300_peak_dd": -31.9,
        "key_question": "全年是否空仓为主？假信号是否频繁？",
        "pass_criteria": "胜率 > 30% 且 最大回撤 < -20%",
    },
    {
        "name": "2020 疫情V型",
        "start": "2020-01-01",
        "end": "2020-12-31",
        "hs300_return": 27.21,
        "hs300_peak_dd": -16.1,    # 3月急跌
        "key_question": "3月急跌是否离场？7月反弹是否重新入场？",
        "pass_criteria": "有 ≥1 次买入 且 最大回撤 < -15%",
    },
    {
        "name": "2024 全年度",
        "start": "2024-01-01",
        "end": "2024-12-31",
        "hs300_return": 14.68,
        "hs300_peak_dd": -8.5,
        "key_question": "9月底暴涨是否有买点？全年是否积极参与？",
        "pass_criteria": "交易次数 ≥ 1 且 胜率 > 40%",
    },
]

INITIAL_CAPITAL = 2000000.0


# ============================================================
# 辅助函数
# ============================================================

def classify_buy_type(reason: str) -> str:
    if not reason:
        return '未知'
    if '一类买点' in reason or '一买' in reason:
        return '一买'
    if '二类买点' in reason or '二买' in reason:
        return '二买'
    if '三类买点' in reason or '三买' in reason:
        return '三买'
    if '中枢' in reason and '兜底' in reason:
        return '中枢兜底'
    return '其他'


def extract_stop_loss_count(stats: dict) -> int:
    """从交易明细中提取止损触发次数"""
    count = 0
    if 'trades' in stats and stats['trades']:
        for t in stats['trades']:
            reason = t.get('reason_exit', '')
            if any(kw in reason for kw in ['止损', 'stop', '清仓', '跌破']):
                count += 1
    return count


def extract_trade_periods(stats: dict) -> list:
    """提取每笔交易的进出日期，用于分析持仓时段"""
    periods_list = []
    if 'trades' in stats and stats['trades']:
        for t in stats['trades']:
            entry = t.get('entry_date', '')
            exit_d = t.get('exit_date', '')
            if entry and exit_d:
                periods_list.append((date_to_str(entry), date_to_str(exit_d)))
    return periods_list


# ============================================================
# 主流程
# ============================================================

def run_period(period: dict, stocks: list, quiet: bool = True):
    """对单个周期跑批量回测"""
    results = []
    errors = []

    for i, (code, name) in enumerate(stocks):
        try:
            stats = run_single(
                code, name,
                start_date=period["start"],
                end_date=period["end"],
                capital=INITIAL_CAPITAL,
                quiet=quiet,
            )

            if stats and stats.get('total_trades', 0) > 0:
                buy_types = Counter()
                for t in stats.get('trades', []):
                    bt = classify_buy_type(t.get('reason_entry', ''))
                    buy_types[bt] += 1

                stop_count = extract_stop_loss_count(stats)
                trade_periods = extract_trade_periods(stats)

                # 计算持仓天数占比
                total_days = (datetime.strptime(period["end"], "%Y-%m-%d") -
                              datetime.strptime(period["start"], "%Y-%m-%d")).days
                holding_days = 0
                for entry, exit_d in trade_periods:
                    try:
                        d1 = max(datetime.strptime(entry, "%Y-%m-%d"),
                                 datetime.strptime(period["start"], "%Y-%m-%d"))
                        d2 = min(datetime.strptime(exit_d, "%Y-%m-%d"),
                                 datetime.strptime(period["end"], "%Y-%m-%d"))
                        holding_days += max(0, (d2 - d1).days)
                    except:
                        pass

                result = {
                    "code": code,
                    "name": name,
                    "total_return": stats.get("total_return", 0),
                    "annual_return": stats.get("annual_return", 0),
                    "total_trades": stats.get("total_trades", 0),
                    "win_rate": stats.get("win_rate", 0),
                    "max_drawdown": stats.get("max_drawdown", 0),
                    "sharpe_ratio": stats.get("sharpe_ratio", 0),
                    "stop_loss_count": stop_count,
                    "holding_pct": holding_days / max(total_days, 1) * 100,
                    "buy_1st": buy_types.get("一买", 0),
                    "buy_2nd": buy_types.get("二买", 0),
                    "buy_3rd": buy_types.get("三买", 0),
                    "first_trade_date": trade_periods[0][0] if trade_periods else "",
                    "last_trade_date": trade_periods[-1][1] if trade_periods else "",
                }
                results.append(result)
            else:
                results.append({
                    "code": code, "name": name,
                    "total_return": 0, "total_trades": 0,
                    "win_rate": 0, "max_drawdown": 0,
                    "stop_loss_count": 0, "holding_pct": 0,
                    "buy_1st": 0, "buy_2nd": 0, "buy_3rd": 0,
                    "first_trade_date": "", "last_trade_date": "",
                })

        except Exception as e:
            errors.append(f"{code} {name}: {str(e)[:100]}")

    return results, errors


def print_period_report(period: dict, results: list, errors: list):
    """打印单周期报告"""
    name = period["name"]
    print(f"\n{'=' * 110}")
    print(f"  {name} | {period['start']} ~ {period['end']}")
    print(f"  沪深300: {period['hs300_return']:+.2f}%  |  最大回撤: {period['hs300_peak_dd']:+.1f}%")
    print(f"{'=' * 110}")

    traded = [r for r in results if r["total_trades"] > 0]

    if not traded:
        print(f"\n  ⚠️ 全部零交易！系统在该周期完全空仓。")
        print(f"  解读: 缠论未发现任何买点信号。")
        return

    # ── 整体统计 ──
    n = len(traded)
    avg_ret = sum(r["total_return"] for r in traded) / n
    avg_wr = sum(r["win_rate"] for r in traded) / n
    avg_mdd = sum(r["max_drawdown"] for r in traded) / n
    avg_trades = sum(r["total_trades"] for r in traded) / n
    avg_holding = sum(r["holding_pct"] for r in traded) / n
    total_stops = sum(r["stop_loss_count"] for r in traded)
    pos_count = sum(1 for r in traded if r["total_return"] > 0)
    beat_hs300 = sum(1 for r in traded if r["total_return"] > period["hs300_return"])

    print(f"\n  整体 ({n}/10 只有交易):")
    print(f"    平均收益:       {avg_ret:+.2f}%")
    print(f"    正收益率:       {pos_count}/{n} ({pos_count*100/max(n,1):.0f}%)")
    print(f"    跑赢沪深300:    {beat_hs300}/{n}")
    print(f"    平均胜率:       {avg_wr:.1f}%")
    print(f"    平均最大回撤:   {avg_mdd:+.2f}%")
    print(f"    平均交易次数:   {avg_trades:.1f}")
    print(f"    平均持仓占比:   {avg_holding:.0f}%")
    print(f"    总止损次数:     {total_stops}")

    # ── 崩盘检测指标 ──
    worst_mdd = min(r["max_drawdown"] for r in traded)
    worst_stock = min(traded, key=lambda r: r["max_drawdown"])
    print(f"\n  🔴 崩盘检测:")
    print(f"    最差个股回撤:   {worst_mdd:+.2f}% ({worst_stock['code']} {worst_stock['name']})")
    print(f"    vs 沪深300回撤: {period['hs300_peak_dd']:+.1f}%")

    # ── 评判 ──
    print(f"\n  📋 关键问题: {period['key_question']}")
    print(f"  通过标准: {period['pass_criteria']}")

    # 自动评判
    if "max_drawdown" in period.get("pass_criteria", ""):
        # 简单判定
        if all(r["max_drawdown"] > -22 for r in traded):
            verdict = "✅ 通过"
        elif avg_mdd > -20:
            verdict = "✅ 基本通过"
        else:
            verdict = "❌ 未通过"
    elif "胜率" in period.get("pass_criteria", ""):
        if avg_wr > 30 and avg_mdd > -20:
            verdict = "✅ 通过"
        else:
            verdict = "⚠️ 需关注"
    elif "买入" in period.get("pass_criteria", "") and "回撤" in period.get("pass_criteria", ""):
        if n >= 3 and avg_mdd > -15:
            verdict = "✅ 通过"
        else:
            verdict = "⚠️ 需关注"
    else:
        verdict = "🔍 人工判断"

    print(f"  自动评判: {verdict}")

    # ── 逐股明细 ──
    print(f"\n  {'─' * 100}")
    print(f"  {'代码':<8} {'名称':<10} {'收益':>8} {'胜率':>7} {'回撤':>7} {'交易':>5} {'止损':>5} {'持仓%':>6} {'一买':>4} {'二买':>4} {'三买':>4}")
    print(f"  {'─' * 100}")
    for r in sorted(results, key=lambda x: x["total_return"], reverse=True):
        print(f"  {r['code']:<8} {r['name']:<10} {r['total_return']:>+7.2f}% "
              f"{r['win_rate']:>5.1f}% {r['max_drawdown']:>+6.2f}% "
              f"{r['total_trades']:>5} {r['stop_loss_count']:>5} {r['holding_pct']:>5.0f}% "
              f"{r['buy_1st']:>4} {r['buy_2nd']:>4} {r['buy_3rd']:>4}")

    # ── 零交易 ──
    no_trade = [r for r in results if r["total_trades"] == 0]
    if no_trade:
        print(f"\n  零交易 ({len(no_trade)}只):", ", ".join(f"{r['code']} {r['name']}" for r in no_trade))

    if errors:
        print(f"\n  错误 ({len(errors)}):")
        for e in errors[:5]:
            print(f"    {e}")


def save_csv(all_results: dict, path: str):
    """保存所有周期结果到 CSV"""
    fieldnames = [
        "period", "code", "name", "total_return", "win_rate",
        "max_drawdown", "total_trades", "stop_loss_count",
        "holding_pct", "buy_1st", "buy_2nd", "buy_3rd",
        "first_trade_date", "last_trade_date",
    ]
    rows = []
    for period_name, results in all_results.items():
        for r in results:
            row = {k: r.get(k, "") for k in fieldnames if k != "period"}
            row["period"] = period_name
            rows.append(row)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n详细结果已保存: {path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="极端行情回测验证")
    parser.add_argument("--output", type=str,
                        default="D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/extreme_market_results.csv",
                        help="输出CSV路径")
    parser.add_argument("--stocks", type=int, default=10,
                        help="测试股票数量 (默认10)")
    args = parser.parse_args()

    print("=" * 70)
    print("  极端行情回测验证")
    print("  目标: 验证系统在2015/2018/2020/2024不会死扛崩盘")
    print("=" * 70)

    stocks = UNIVERSE_STOCKS[:args.stocks]
    print(f"\n测试标的 ({len(stocks)} 只):")
    for code, name in stocks:
        print(f"  {code} {name}")

    all_results = {}

    for period in PERIODS:
        print(f"\n\n⏳ 正在回测: {period['name']} ({period['start']} ~ {period['end']})...")
        print(f"   (预计 2-3 分钟)")

        results, errors = run_period(period, stocks)

        # 去掉 None 结果
        results = [r for r in results if r is not None]

        all_results[period["name"]] = results
        print_period_report(period, results, errors)

    # ── 综合对比 ──
    print(f"\n\n{'=' * 110}")
    print(f"  四周期综合对比")
    print(f"{'=' * 110}")
    print(f"\n  {'周期':<20} {'平均收益':>10} {'平均胜率':>8} {'平均回撤':>10} {'平均交易':>8} {'止损次数':>8} {'持仓%':>8}")
    print(f"  {'─' * 85}")

    for period in PERIODS:
        name = period["name"]
        results = all_results.get(name, [])
        traded = [r for r in results if r["total_trades"] > 0]
        n = max(len(traded), 1)
        avg_ret = sum(r["total_return"] for r in traded) / n
        avg_wr = sum(r["win_rate"] for r in traded) / n
        avg_mdd = sum(r["max_drawdown"] for r in traded) / n
        avg_tr = sum(r["total_trades"] for r in traded) / n
        total_st = sum(r["stop_loss_count"] for r in traded)
        avg_hold = sum(r["holding_pct"] for r in traded) / n
        hs300 = period["hs300_return"]

        print(f"  {name:<20} {avg_ret:>+9.2f}% {avg_wr:>7.1f}% {avg_mdd:>+9.2f}% "
              f"{avg_tr:>7.1f} {total_st:>8} {avg_hold:>7.0f}%  "
              f"(HS300 {hs300:+.1f}%)")

    # 保存
    save_csv(all_results, args.output)
    print("\n完毕。")


if __name__ == "__main__":
    main()
