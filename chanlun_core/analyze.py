#!/usr/bin/env python
# analyze.py — 三维辅助分析统一入口
# 调用方式: python analyze.py <股票代码> <类型>
# 类型: chip(筹码) | money(资金) | sentiment(情绪) | all(全部)
# 示例: python analyze.py 688036 chip
#       python analyze.py 688036 all

import sys
import os

# 确保能找到同目录下的工具模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from portable_chip_tool import PortableChipTool
from portable_hot_money_tool import PortableHotMoneyTool
from portable_sentiment_tool import PortableSentimentTool


def clean_code(stock_code: str) -> str:
    """清洗股票代码: 去前缀、纯6位"""
    return stock_code.replace('sh', '').replace('sz', '').replace('bj', '').strip()


def header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def run_chip(stock_code: str):
    """筹码分布"""
    header(f"筹码分布 — {stock_code}")
    tool = PortableChipTool()
    result = tool.execute(stock_code)
    if result.get("error"):
        print(f"❌ {result['error']}")
        return

    info = result.get("stock_info", {})
    analysis = result.get("analysis", {})
    main_cost = analysis.get("main_cost", {})
    trapped = analysis.get("trapped", {})
    signals = analysis.get("signals", {})
    
    # 格式化市值
    market_cap_raw = info.get("market_cap", "0")
    try:
        market_cap_yi = float(market_cap_raw) / 1_0000_0000
        market_cap_str = f"{market_cap_yi:.1f}亿"
    except (ValueError, TypeError):
        market_cap_str = market_cap_raw
    
    print(f"  当前价: {info.get('current_price', 'N/A')}  |  股票: {info.get('name', 'N/A')}  |  市值: {market_cap_str}")
    print(f"  数据源: {result.get('data_quality', 'N/A')}")
    print()
    print(f"  💰 主力成本区: {main_cost.get('main_cost_area', 'N/A')}")
    print(f"  🎯 控盘程度: {main_cost.get('control_level', 'N/A')}")
    print(f"  📉 套牢比例: {trapped.get('trapped_ratio', 'N/A')}%  |  {trapped.get('selling_pressure', 'N/A')}")

    if signals.get("buy_signals"):
        print(f"\n  ✅ 买入信号:")
        for s in signals["buy_signals"]:
            print(f"     - {s}")
    if signals.get("risk_warnings"):
        print(f"\n  ⚠️ 风险预警:")
        for w in signals["risk_warnings"]:
            print(f"     - {w}")
    print()


def run_money(stock_code: str):
    """资金面（游资/龙虎榜/资金流向）"""
    header(f"资金面分析 — {stock_code}")
    tool = PortableHotMoneyTool()
    result = tool.execute(stock_code)
    if result.get("error"):
        print(f"❌ {result['error']}")
        return

    info = result.get("stock_info", {})
    stock_flow = result.get("stock_fund_flow", {})
    dragon = result.get("dragon_tiger", {})
    hot = result.get("hot_sectors", {}).get("data", {})
    signals = result.get("signals", {})

    print(f"  当前价: {info.get('current_price', 'N/A')}  |  股票: {info.get('name', 'N/A')}")
    print()
    print(f"  💸 5日主力净流入: {stock_flow.get('net_flow_5d', 'N/A')}")

    dragon_note = dragon.get("note", "")
    if dragon_note:
        print(f"  📊 龙虎榜: {dragon_note}")
    else:
        net_buy = dragon.get("data", {}).get("净买入额", 0)
        print(f"  📊 龙虎榜净买入: {net_buy:.2f} 万")

    top_conc = hot.get("top_concepts", [])[:3]
    if top_conc:
        names = [c.get("板块名称", "") for c in top_conc]
        print(f"  🔥 热门概念: {', '.join(names)}")

    if signals.get("buy_signals"):
        print(f"\n  ✅ 信号:")
        for s in signals["buy_signals"][:3]:
            print(f"     - {s}")
    if signals.get("risk_warnings"):
        print(f"\n  ⚠️ 预警:")
        for w in signals["risk_warnings"][:3]:
            print(f"     - {w}")
    print()


def run_sentiment(stock_code: str = "000001"):
    """市场情绪"""
    header(f"市场情绪 — 参考指数 {stock_code}")
    tool = PortableSentimentTool()
    result = tool.execute(stock_code)

    zen = result.get("zen_ratio", {})
    breadth = result.get("market_breadth", {})
    hot = result.get("hot_sectors", {}).get("data", {})
    score = result.get("sentiment_score", {})

    print(f"  📊 涨跌比: {zen.get('zen_ratio', 'N/A')}x  "
          f"(涨{zen.get('up_stocks', 0)} / 跌{zen.get('down_stocks', 0)})")
    print(f"  📈 涨停: {zen.get('limit_up', 0)} 只  |  跌停: {zen.get('limit_down', 0)} 只")
    print(f"  🏭 行业广度: {breadth.get('breadth_ratio', 'N/A')}x  "
          f"(↑{breadth.get('up_industries', 0)} / ↓{breadth.get('down_industries', 0)})")

    top_conc = hot.get("top_concepts", [])[:5]
    if top_conc:
        print(f"\n  🔥 热门概念 Top5:")
        for c in top_conc:
            print(f"     {c.get('板块名称', ''):<20s} {c.get('涨跌幅', 0)}%")

    print(f"\n  🎯 情绪评分: {score.get('total_score', 'N/A')}  ({score.get('level', 'N/A')})")
    for k, v in score.get("details", {}).items():
        print(f"     {k}: {v}")


def run_all(stock_code: str):
    """全部分析"""
    run_chip(stock_code)
    run_money(stock_code)
    run_sentiment(stock_code)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python analyze.py <股票代码> [chip|money|sentiment|all]")
        print("示例: python analyze.py 688036 all")
        print("      python analyze.py 688036 chip")
        print("      python analyze.py 000001 sentiment")
        sys.exit(1)

    stock = clean_code(sys.argv[1])
    analysis_type = sys.argv[2] if len(sys.argv) > 2 else "all"

    type_map = {
        "chip": run_chip,
        "money": run_money,
        "sentiment": run_sentiment,
        "all": run_all,
    }

    fn = type_map.get(analysis_type)
    if fn is None:
        print(f"未知类型: {analysis_type}，可选: chip/money/sentiment/all")
        sys.exit(1)

    fn(stock)
