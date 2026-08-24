"""快速缠论分析脚本 - 输出中枢/买卖点/背驰
使用：主Agent在 Step 2 中通过 terminal 工具运行此脚本获取真实缠论数据
注意：此脚本必须在 C:/Users/13120/WorkBuddy/Claw/生活/缠论/ 目录下执行
"""
import sys, json, os
sys.path.insert(0, "C:/Users/13120/WorkBuddy/Claw/生活/缠论")
from data_manager import DataManager
from generate_analysis import RecursiveTimingSystem


def analyze_stock(symbol):
    dm = DataManager()
    rec_sys = RecursiveTimingSystem(dm)
    daily = rec_sys.run_full_analysis(symbol)

    result = {
        "symbol": symbol,
        "daily": {
            "current_price": round(daily.klines[-1].close, 2) if daily.klines else None,
            "fenxing_count": len(daily.fenxings) if daily.fenxings else 0,
            "bi_count": len(daily.bis) if daily.bis else 0,
            "buy_sell_points": [],
            "zhongshus": [],
            "last_5_bis": [],
            "macd_status": None,
        }
    }

    # 买卖点
    if hasattr(daily, "buy_sell_points") and daily.buy_sell_points:
        for p in daily.buy_sell_points:
            pt = {
                "type": p.type,
                "level": p.level,
                "date": str(p.date)[:10],
                "price": round(p.price, 2),
                "reason": p.reason,
            }
            if hasattr(p, "multilevel_confirmation") and p.multilevel_confirmation:
                pt["confidence_score"] = p.multilevel_confirmation.get(
                    "confidence_score", 0
                )
                pt["high_confidence"] = p.multilevel_confirmation.get(
                    "high_confidence", False
                )
                pt["confirmation_type"] = p.multilevel_confirmation.get(
                    "confirmation_type", ""
                )
            result["daily"]["buy_sell_points"].append(pt)

    # 中枢
    if hasattr(daily, "zhongshus") and daily.zhongshus:
        for zs in daily.zhongshus:
            result["daily"]["zhongshus"].append(
                {
                    "start": str(zs.start_date)[:10],
                    "end": str(zs.end_date)[:10],
                    "zg": round(zs.zg, 2),
                    "zd": round(zs.zd, 2),
                    "bi_count": zs.bi_count,
                }
            )

    # 最近5笔
    if daily.bis:
        for bi in daily.bis[-5:]:
            result["daily"]["last_5_bis"].append(
                {
                    "direction": bi.direction,
                    "start_date": str(bi.start_date)[:10],
                    "start_price": round(bi.start_price, 2),
                    "end_date": str(bi.end_date)[:10],
                    "end_price": round(bi.end_price, 2),
                }
            )

    # MACD
    if hasattr(daily, "macd_data") and daily.macd_data and len(daily.macd_data) > 1:
        l, p = daily.macd_data[-1], daily.macd_data[-2]
        result["daily"]["macd_status"] = {
            "date": str(l.date)[:10],
            "dif": round(l.dif, 3),
            "dea": round(l.dea, 3),
            "macd": round(l.macd, 3),
            "macd_trend": "up" if l.macd > p.macd else "down",
            "dif_dea": "golden_cross" if l.dif > l.dea else "dead_cross",
        }

    return result


if __name__ == "__main__":
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["000830"]
    for sym in symbols:
        try:
            result = analyze_stock(sym)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            import traceback

            print(
                json.dumps(
                    {
                        "symbol": sym,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                )
            )
