"""快速生成缠论HTML可视化报告
用法: python quick_html.py <代码> [代码2 ...]
输出: ~/.hermes/profiles/commander/analysis_reports/reports_html/{代码}_chanlun.html
"""
import sys, os, json
from date_utils import date_to_str, parse_date_to_datetime
sys.path.insert(0, r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core")
import baostock_utils  # noqa: E402 — print redirect
from data_manager import DataManager
from generate_analysis import RecursiveTimingSystem, HTMLVisualizer
from segment_analyzer import SegmentChanLunAnalyzer

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports_html")
os.makedirs(REPORT_DIR, exist_ok=True)

# 股票名称映射（从quick_fundamental获取或默认代码）
STOCK_NAMES = {
    "000001": "平安银行", "000338": "潍柴动力", "002352": "顺丰控股",
    "002415": "海康威视", "600519": "贵州茅台", "000858": "五粮液",
    "300750": "宁德时代", "601899": "紫金矿业", "600036": "招商银行",
    "601318": "中国平安", "600900": "长江电力", "002475": "立讯精密",
    "300059": "东方财富", "688256": "寒武纪", "601166": "兴业银行",
    "000333": "美的集团", "300308": "中际旭创", "600864": "哈投股份",
    "601018": "宁波港", "601866": "中远海发", "688617": "惠泰医疗",
    "002422": "科伦药业", "300502": "新易盛", "301498": "乖宝宠物",
}


def generate_html(symbol, name=None):
    """生成HTML报告"""
    if name is None:
        name = STOCK_NAMES.get(symbol, symbol)
    
    dm = DataManager()
    rec_sys = RecursiveTimingSystem(dm)
    
    daily = rec_sys.run_full_analysis(symbol)
    
    if not daily or not daily.klines:
        return {"symbol": symbol, "error": "Analysis failed - no klines data"}
    
    m30 = rec_sys.analyses.get('30min')
    
    # ── 线段中枢分析 ──
    seg_analyzer = SegmentChanLunAnalyzer()
    seg_analyzer.analyze(daily)
    
    visualizer = HTMLVisualizer(
        symbol, name, daily,
        reference_price=daily.klines[-1].close if daily.klines else None,
        m30_analyzer=m30,
        segment_result=seg_analyzer
    )
    
    output_path = os.path.join(REPORT_DIR, f"{symbol}_chanlun.html")
    visualizer.generate_html(output_path)
    
    return {
        "symbol": symbol,
        "name": name,
        "html_path": output_path,
        "current_price": round(daily.klines[-1].close, 2) if daily.klines else None,
        "bi_count": len(daily.bis) if daily.bis else 0,
        "buy_points": len([p for p in (daily.buy_sell_points or []) if p.type == 'buy']),
        "sell_points": len([p for p in (daily.buy_sell_points or []) if p.type == 'sell']),
    }


if __name__ == "__main__":
    # v5.4(A-4): stderr 用 utf-8+replace；状态标记与 JSON 摘要改 ASCII 安全
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["000001"]
    results = []
    for sym in symbols:
        try:
            r = generate_html(sym)
            results.append(r)
            if r.get("error"):
                print(f"  [FAIL] {sym}: {r['error']}")
            else:
                print(f"  [OK] {sym} ({r.get('name','')}) -> {r['html_path']}")
        except Exception as e:
            import traceback
            results.append({"symbol": sym, "error": str(e)})
            print(f"  [FAIL] {sym}: {e}")

    # 输出结构化摘要（ensure_ascii=True: 任何控制台代码页都不崩溃）
    print("\n---HTML_REPORT_SUMMARY---")
    print(json.dumps(results, ensure_ascii=True, indent=2))
