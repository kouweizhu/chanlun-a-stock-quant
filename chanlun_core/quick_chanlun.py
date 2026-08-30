"""快速缠论分析脚本 - 输出中枢/买卖点/背驰

v5.4(A-4) stdout治理架构:
  旧实现用 builtins.print monkey-patch 做'['开头白名单重定向——既漏掉
  带前导空格/特殊字符的日志(如 [BiExtend])污染 stdout JSON 契约, 也无法
  解决 GBK 控制台下 ¥/↓ 等字符的 UnicodeEncodeError 崩溃。
  新架构: 分析期间 sys.stdout 重定向到内存缓冲(任何模块日志都进不来),
    - stdout 最终只输出 ensure_ascii=True 的纯 ASCII JSON(编码绝对安全)
    - 缓冲中的过程日志在结束时整体转投 stderr(utf-8+replace)
"""
import sys, json, io, os

sys.path.insert(0, r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core")
from data_manager import DataManager
from generate_analysis import RecursiveTimingSystem


# v5.4.1(AUD-A-05): 进程内暂存最近一次分析的 RecursiveTimingSystem——
# 编排器(single_stock_analysis)的 html 线程复用它渲染, 不再对同一股票
# 并行重跑一遍完整技术分析(双倍CPU)并竞态写同一 parquet 缓存。
_LAST_ANALYSES = {}


def analyze_stock(symbol):
    dm = DataManager()
    rec_sys = RecursiveTimingSystem(dm)
    daily = rec_sys.run_full_analysis(symbol)
    _LAST_ANALYSES[symbol] = rec_sys

    result = {
        "symbol": symbol,
        "daily": {
            "current_price": round(daily.klines[-1].close, 2) if daily.klines else None,
            "fenxing_count": len(daily.fenxings) if daily.fenxings else 0,
            "bi_count": len(daily.bis) if daily.bis else 0,
            "buy_sell_points": [],
            "zhongshus": [],
            "last_5_bis": [],
            "macd_status": None
        }
    }

    if hasattr(daily, 'buy_sell_points') and daily.buy_sell_points:
        for p in daily.buy_sell_points:
            pt = {"type": p.type, "level": p.level, "date": date_to_str(p.date),
                  "price": round(p.price, 2), "reason": p.reason}
            if hasattr(p, 'multilevel_confirmation') and p.multilevel_confirmation:
                pt["confidence_score"] = p.multilevel_confirmation.get("confidence_score", 0)
            result["daily"]["buy_sell_points"].append(pt)

    if hasattr(daily, 'zhongshus') and daily.zhongshus:
        for zs in daily.zhongshus:
            result["daily"]["zhongshus"].append({
                "start": str(zs.start_date)[:10], "end": str(zs.end_date)[:10],
                "zg": round(zs.zg, 2), "zd": round(zs.zd, 2), "bi_count": zs.bi_count
            })

    if daily.bis:
        for bi in daily.bis[-5:]:
            result["daily"]["last_5_bis"].append({
                "direction": bi.direction,
                "start_date": str(bi.start_date)[:10], "start_price": round(bi.start_price, 2),
                "end_date": str(bi.end_date)[:10], "end_price": round(bi.end_price, 2),
            })

    if hasattr(daily, 'macd_data') and daily.macd_data and len(daily.macd_data) > 1:
        l, p = daily.macd_data[-1], daily.macd_data[-2]
        result["daily"]["macd_status"] = {
            "date": date_to_str(l.date), "dif": round(l.dif, 3), "dea": round(l.dea, 3),
            "macd": round(l.macd, 3),
            "macd_trend": "up" if l.macd > p.macd else "down",
            "dif_dea": "golden_cross" if l.dif > l.dea else "dead_cross"
        }

    return result


# date_utils 导入放在路径注入后（保持原顺序语义）
from date_utils import date_to_str, parse_date_to_datetime  # noqa: E402

if __name__ == "__main__":
    # stderr 用 utf-8+replace：过程日志含任何字符都不会再触发控制台编码崩溃
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["000830"]
    for sym in symbols:
        _real_stdout = sys.stdout
        _buf = io.StringIO()
        try:
            sys.stdout = _buf  # 分析期全部过程日志进缓冲, 不落 stdout
            result = analyze_stock(sym)
        except Exception as e:
            import traceback
            result = {"symbol": sym, "error": str(e), "traceback": traceback.format_exc()}
        finally:
            sys.stdout = _real_stdout

        _logs = _buf.getvalue()
        if _logs.strip():
            sys.stderr.write(_logs)
            if not _logs.endswith("\n"):
                sys.stderr.write("\n")

        # ensure_ascii=True → 纯 ASCII 输出, GBK/任何代码页控制台都绝不崩溃
        print(json.dumps(result, ensure_ascii=True, indent=2))
