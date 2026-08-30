"""基本面评分回测系统 v1
验证 quick_fundamental.py 的四维度评分规则（各25分，总分100）的预测力

回测区间: 2024-01 ~ 2026-04-29
评分频率: 每个财报季一次（年报/半年报/季报披露截止日后）
股票池: 同负面消息扫描的18只自选股
输出: fund_backtest_report.html（交互式可视化报告）

核心设计：
- 每个评分时点只使用当时已披露的财报数据，严格避免前视偏差
- 观察窗口取 T+63(3个月) / T+126(6个月) / T+252(12个月)
"""

import sys, json, os, math
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import pandas as pd
import numpy as np

sys.path.insert(0, r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core")

import baostock_utils  # noqa: E402 — print redirect + Baostock session

from data_manager import DataManager
from quick_fundamental import classify_by_industry, calculate_fundamental_score

# ============================================================
# 股票池（与负面消息扫描一致）
# ============================================================

STOCK_POOL = []
try:
    sys.path.insert(0, r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core")
    from check_negative_news import MONITOR_LIST
    for code, name, industry in MONITOR_LIST:
        STOCK_POOL.append({"code": code, "name": name, "style": classify_by_industry(industry)})
    print(f"  ✓ 股票池: {len(STOCK_POOL)} 只")
except Exception as e:
    print(f"  ⚠ 读取失败: {e}")
    STOCK_POOL = [{"code": "000001", "name": "平安银行", "style": "蓝筹"}]

# ============================================================
# 回测时点定义（财报披露截止日）
# ============================================================
# 中国上市公司财报披露规则：
# 年报(4Q): 次年1/1~4/30  半年报(2Q): 7/1~8/31  季报(1Q/3Q): 该季度结束后1个月内
# 我们取披露截止日作为回测时点

TEST_POINTS = [
    # (回测日期, 可用最新财报的year, quarter, 标签)
    ("2021-04-30", 2020, 4, "2020年报"),
    ("2021-08-31", 2021, 2, "2021半年报"),
    ("2021-10-31", 2021, 3, "2021三季报"),
    ("2022-04-30", 2021, 4, "2021年报"),
    ("2022-08-31", 2022, 2, "2022半年报"),
    ("2022-10-31", 2022, 3, "2022三季报"),
    ("2023-04-30", 2022, 4, "2022年报"),
    ("2023-08-31", 2023, 2, "2023半年报"),
    ("2023-10-31", 2023, 3, "2023三季报"),
    ("2024-04-30", 2023, 4, "2023年报"),
    ("2024-08-31", 2024, 2, "2024半年报"),
    ("2024-10-31", 2024, 3, "2024三季报"),
    ("2025-04-30", 2024, 4, "2024年报"),
    ("2025-08-31", 2025, 2, "2025半年报"),
    ("2025-10-31", 2025, 3, "2025三季报"),
    ("2026-04-30", 2025, 4, "2025年报"),
]

OBSERVE_DAYS = [126, 252, 504]  # T+126≈6个月, T+252≈1年, T+504≈2年

OUTPUT_DIR = os.path.expanduser("~/.hermes/profiles/commander/analysis_reports/backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)
HTML_PATH = os.path.join(OUTPUT_DIR, "fund_backtest_report.html")
CSV_PATH = os.path.join(OUTPUT_DIR, "fund_backtest_raw.csv")
# 额外同步到 Windows 常用目录（仅当通过 WSL 挂载时可访问）
_WIN_MNT = "D:/常用文件/回测报告"
WIN_DIR = _WIN_MNT if os.path.exists(_WIN_MNT) else OUTPUT_DIR


# ============================================================
# 获取历史时点的财务数据（关键：控制前视偏差）
# ============================================================

def get_fundamentals_at_date(symbol: str, year: int, quarter: int) -> dict:
    """获取指定历史时点的财务数据——模拟当时可见的信息"""
    bs, _ = baostock_utils.login()

    result = {
        "symbol": symbol, "name": "", "industry": "",
        "data_year": year, "data_quarter": quarter,
        "profitability": {}, "growth": {}, "health": {}, "valuation": {},
        "market_cap": None, "dividend_yield": None,
        "stock_type_hint": "", "confidence": 5,
    }

    bs_code = f"sh.{symbol}" if symbol.startswith('6') or symbol.startswith('5') else f"sz.{symbol}"

    try:
        # 行业
        rs = bs.query_stock_industry(bs_code)
        ind = rs.get_data()
        if not ind.empty:
            result["industry"] = ind["industry"].values[0]

        # 盈利能力
        rs = bs.query_profit_data(bs_code, year=year, quarter=quarter)
        p = rs.get_data()
        if not p.empty:
            p = p.iloc[0]
            result["data_date"] = p.get("statDate", "")
            result["profitability"] = {
                "roeAvg": _sf(p.get("roeAvg")),
                "npMargin": _sf(p.get("npMargin")),
                "gpMargin": _sf(p.get("gpMargin")),
                "netProfit": _sf(p.get("netProfit")),
                "epsTTM": _sf(p.get("epsTTM")),
                "totalRevenue": _sf(p.get("MBRevenue")),
                "totalShare": _sf(p.get("totalShare")),
            }

        # 成长能力
        rs = bs.query_growth_data(bs_code, year=year, quarter=quarter)
        g = rs.get_data()
        if not g.empty:
            g = g.iloc[0]
            result["growth"] = {
                "YOYNI": _sf(g.get("YOYNI")),
                "YOYEquity": _sf(g.get("YOYEquity")),
                "YOYAsset": _sf(g.get("YOYAsset")),
                "YOYEPSBasic": _sf(g.get("YOYEPSBasic")),
            }

        # 财务健康
        rs = bs.query_balance_data(bs_code, year=year, quarter=quarter)
        b = rs.get_data()
        if not b.empty:
            b = b.iloc[0]
            result["health"] = {
                "liabilityToAsset": _sf(b.get("liabilityToAsset")),
                "assetToEquity": _sf(b.get("assetToEquity")),
                "currentRatio": _sf(b.get("currentRatio")),
            }

        rs = bs.query_cash_flow_data(bs_code, year=year, quarter=quarter)
        cf = rs.get_data()
        if not cf.empty:
            cf = cf.iloc[0]
            result["health"]["CFOToOR"] = _sf(cf.get("CFOToOR"))
            result["health"]["CFOToNP"] = _sf(cf.get("CFOToNP"))

        # 估值（用该时点附近的PE/PB）
        # 用回测日期附近的最新日线数据
        baostock_utils.logout()
    except Exception as e:
        baostock_utils.logout()

    return result


def _sf(val):
    """safe float"""
    if val is None or val == '' or (isinstance(val, float) and math.isnan(val)):
        return None
    try: return round(float(val), 4)
    except: return None


# ============================================================
# PE/PB 从当日K线获取（不引入未来数据）
# ============================================================

def get_pe_pb_at_date(df_kline: pd.DataFrame, test_date: str) -> dict:
    """从日线数据中获取某个日期的PE/PB"""
    mask = df_kline["date_str"] == test_date
    if not mask.any():
        return {"peTTM": None, "pbMRQ": None, "close": None}
    row = df_kline[mask].iloc[-1]
    pe = _sf(row.get("peTTM"))
    pb = _sf(row.get("pbMRQ"))
    close = _sf(row.get("close"))
    return {"peTTM": pe, "pbMRQ": pb, "close": close}


# ============================================================
# 后续收益
# ============================================================

def calc_forward_returns(df_kline: pd.DataFrame, test_date: str, n_days: int) -> Optional[float]:
    mask = df_kline["date_str"] == test_date
    if not mask.any(): return None
    pos = df_kline[mask].index[0]
    pos_iloc = df_kline.index.get_loc(pos)
    target = pos_iloc + n_days
    if target >= len(df_kline): return None
    c0 = float(df_kline.iloc[pos_iloc]["close"])
    c1 = float(df_kline.iloc[target]["close"])
    if c0 <= 0: return None
    return round((c1 - c0) / c0, 4)


# ============================================================
# 主回测
# ============================================================

def run_backtest():
    t0 = datetime.now()
    print("=" * 60)
    print("基本面评分回测 v1")
    print(f"区间: {TEST_POINTS[0][0]} ~ {TEST_POINTS[-1][0]} ({len(TEST_POINTS)}个时点)")
    print(f"股票: {len(STOCK_POOL)} 只")
    print("=" * 60)

    # 1. 预加载所有股票的日线数据（用于计算后续收益和PE/PB）
    print("\n[1/4] 加载日线数据...")
    dm = DataManager()
    kline_cache = {}
    for s in STOCK_POOL:
        df = dm.get_klines(s["code"], 'daily', start_date="2020-01-01", end_date="2026-04-29")
        if not df.empty:
            df["date_str"] = df["date"].astype(str).str[:10]
            df = df.sort_values("date").reset_index(drop=True)
            kline_cache[s["code"]] = df

    # 2. 回测循环
    print("\n[2/4] 执行回测（控制前视偏差，每个时点只使用当时已披露的财报）...")
    records = []

    for t_idx, (test_date, year, quarter, label) in enumerate(TEST_POINTS):
        print(f"  [{t_idx+1}/{len(TEST_POINTS)}] {test_date} — 使用 {year}Q{quarter} ({label})")

        for s in STOCK_POOL:
            code = s["code"]

            # 获取当时可见的财务数据
            fund_data = get_fundamentals_at_date(code, year, quarter)
            if not fund_data.get("profitability"):
                continue

            # 获取该时点的PE/PB
            kdf = kline_cache.get(code)
            if kdf is not None:
                v = get_pe_pb_at_date(kdf, test_date)
                fund_data["valuation"]["peTTM"] = v["peTTM"]
                fund_data["valuation"]["pbMRQ"] = v["pbMRQ"]
                fund_data["valuation"]["latest_price"] = v["close"]

            # 计算评分
            score = calculate_fundamental_score(fund_data)

            # 计算后续收益
            returns = {}
            if kdf is not None:
                for n in OBSERVE_DAYS:
                    returns[f"T+{n}"] = calc_forward_returns(kdf, test_date, n)

            records.append({
                "date": test_date,
                "label": label,
                "stock_code": code,
                "stock_name": s["name"],
                "style": s["style"],
                "price": fund_data.get("valuation", {}).get("latest_price"),
                "fund_score": score["total_score"],
                "profitability": score["profitability_score"],
                "growth": score["growth_score"],
                "health": score["health_score"],
                "valuation": score["valuation_score"],
                "roe": fund_data.get("profitability", {}).get("roeAvg"),
                "pe": fund_data.get("valuation", {}).get("peTTM"),
                "industry": fund_data.get("industry", ""),
                **returns,
            })

    df_result = pd.DataFrame(records)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n  共 {len(df_result)} 条评分记录 ({elapsed:.0f}s)")

    # 3. 统计分析
    print("\n[3/4] 统计分析...")
    stats = {}

    if not df_result.empty and 'fund_score' in df_result.columns:
        bins = [-float('inf'), 50, 60, 70, 80, float('inf')]
        labels = ['D(回避)', 'C(观望)', 'B(关注)', 'A(推荐)', 'A+(强推)']
        df_result['group'] = pd.cut(df_result['fund_score'], bins=bins, labels=labels, right=False)

        print("\n  === 分组收益 ===")
        for n in OBSERVE_DAYS:
            col = f"T+{n}"
            if col not in df_result.columns: continue
            print(f"\n  --- {col} (约{n//21:.0f}个月) ---")
            grp = df_result.groupby('group')[col].agg(['mean', 'median', 'count', 'std'])
            for g in labels:
                if g in grp.index:
                    r = grp.loc[g]
                    print(f"  {g:>8}: 均值={r['mean']:+.2%}  中位={r['median']:+.2%}  n={int(r['count'])}")
            stats[f"T+{n}"] = {
                g: {"mean": float(grp.loc[g, 'mean']), "median": float(grp.loc[g, 'median']), "count": int(grp.loc[g, 'count'])}
                for g in labels if g in grp.index
            }

        print("\n  === 个股概览 ===")
        ss = df_result.groupby('stock_name').agg(
            均分=('fund_score', 'mean'), 范围=('fund_score', lambda x: f"{x.min():.0f}~{x.max():.0f}"),
            T126收益=('T+126', 'mean'), 样本=('fund_score', 'count')
        ).round(2)
        print(ss.to_string())

    # 4. 保存
    df_result.to_csv(CSV_PATH, index=False, encoding='utf-8-sig')
    print(f"\n  CSV → {CSV_PATH}")

    generate_html(df_result, stats)
    print(f"  HTML → {HTML_PATH}")

    # 同步到Windows
    try:
        import shutil
        os.makedirs(WIN_DIR, exist_ok=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        shutil.copy2(HTML_PATH, os.path.join(WIN_DIR, f"基本面评分回测报告_{today_str}.html"))
        shutil.copy2(CSV_PATH, os.path.join(WIN_DIR, f"基本面评分回测数据_{today_str}.csv"))
        print(f"  → 同步到 {WIN_DIR}")
    except Exception as e:
        print(f"  ⚠ 同步失败: {e}")

    print(f"\n✅ 回测完成 ({elapsed:.0f}s)")
    return df_result


# ============================================================
# HTML 报告（复用技术面回测的样式）
# ============================================================

def generate_html(df: pd.DataFrame, stats: dict):
    if df.empty:
        with open(HTML_PATH, 'w') as f:
            f.write("<html><body><h1>回测无数据</h1></body></html>")
        return

    # 个股时间序列
    stocks_json = {}
    for name in df['stock_name'].unique():
        sub = df[df['stock_name'] == name].sort_values('date')
        stocks_json[name] = {
            "dates": sub['date'].tolist(),
            "scores": [float(x) if not pd.isna(x) else None for x in sub['fund_score']],
        }

    stats_json = json.dumps(stats, ensure_ascii=False)
    stocks_str = json.dumps(stocks_json, ensure_ascii=False)
    group_colors = {
        "A+(强推)": "#c0392b", "A(推荐)": "#e74c3c",
        "B(关注)": "#e67e22", "C(观望)": "#f39c12", "D(回避)": "#27ae60"
    }

    stats_html = ""
    for period, groups in stats.items():
        label = {"T+63": "3个月", "T+126": "6个月", "T+252": "12个月"}.get(period, period)
        stats_html += f"<h3>{period}（约{label}） 分组统计</h3><table><tr><th>分组</th><th>均值</th><th>中位数</th><th>样本数</th></tr>"
        for g, d in groups.items():
            c = group_colors.get(g, "#999")
            cls = "positive" if d['mean'] > 0 else "negative"
            stats_html += f'<tr><td><span style="color:{c}">●</span> {g}</td><td class="{cls}">{d["mean"]:+.2%}</td><td>{d["median"]:+.2%}</td><td>{d["count"]}</td></tr>'
        stats_html += "</table>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>基本面评分回测报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,'Microsoft YaHei',sans-serif;background:#0f1923;color:#e0e0e0}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);padding:30px;border-bottom:1px solid #2a2a4a}}
.header h1{{font-size:24px;color:#fff}}
.header p{{color:#8899aa;margin-top:8px;font-size:14px}}
.container{{max-width:1300px;margin:0 auto;padding:20px}}
.card{{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:24px;margin-bottom:20px}}
.card h2{{font-size:18px;margin-bottom:16px;color:#ccc}}
.chart-box{{height:400px;position:relative}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{padding:10px 14px;text-align:center;border-bottom:1px solid #2a2a4a}}
th{{color:#8899aa;font-weight:500}}
.positive{{color:#e74c3c!important}}
.negative{{color:#27ae60!important}}
.stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:20px}}
.stat-card{{background:#16213e;border-radius:8px;padding:16px;text-align:center}}
.stat-card .num{{font-size:28px;font-weight:700}}
.stat-card .label{{font-size:12px;color:#8899aa;margin-top:4px}}
.tag{{display:inline-block;padding:2px 8px;border-radius:8px;font-size:11px;margin:2px;background:#2a2a4a}}
</style></head><body>
<div class="header"><h1>基本面评分回测报告</h1>
<p>区间: {TEST_POINTS[0][0]} ~ {TEST_POINTS[-1][0]} | 时点: {len(TEST_POINTS)}个 | 股票: {len(STOCK_POOL)}只 | 采样: {len(df)}条</p></div>
<div class="container">
<div class="stats-grid" id="overview"></div>
<div class="card"><h2>分组收益对比</h2><div class="chart-box"><canvas id="groupChart"></canvas></div></div>
<div class="card"><h2>个股评分时间序列</h2><div class="chart-box"><canvas id="tsChart"></canvas></div></div>
<div class="card"><h2>分组详细统计</h2>{stats_html}</div>
</div>
<script>
const stats={stats_json};
const stocks={stocks_str};
const groupColors={json.dumps(group_colors, ensure_ascii=False)};
const total={len(df)};
const avgScore={float(df['fund_score'].mean()) if not df.empty and 'fund_score' in df.columns else 0};
const periods=Object.keys(stats);
const groups=Object.keys(groupColors).filter(g=>stats[periods[0]]&&stats[periods[0]][g]);
const t126=stats['T+126']||{{}};
document.getElementById('overview').innerHTML=`
  <div class="stat-card"><div class="num" style="color:#fff">${{total}}</div><div class="label">评分记录</div></div>
  <div class="stat-card"><div class="num" style="color:#888">${{avgScore.toFixed(1)}}</div><div class="label">均分</div></div>
  <div class="stat-card"><div class="num" style="color:#e74c3c">${{Object.values(t126).reduce((a,b)=>a+(b.count||0),0)}}</div><div class="label">总样本</div></div>`;
new Chart(document.getElementById('groupChart'),{{type:'bar',data:{{labels:periods,
datasets:groups.map(g=>({{label:g,data:periods.map(p=>stats[p]?.[g]?.mean||0),backgroundColor:groupColors[g],borderRadius:4}}))}},
options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'top',labels:{{color:'#ccc'}}}}}},
scales:{{x:{{ticks:{{color:'#8899aa'}}}},y:{{ticks:{{color:'#8899aa',callback:v=>(v*100).toFixed(0)+'%'}}}}}}}}}});
const tsColors=['#3498db','#e74c3c','#2ecc71','#f39c12','#9b59b6','#1abc9c','#e67e22','#95a5a6','#2980b9','#c0392b','#16a085','#8e44ad','#d35400','#27ae60','#7f8c8d','#34495e','#e84393','#00cec9'];
const names=Object.keys(stocks);
new Chart(document.getElementById('tsChart'),{{type:'line',data:{{labels:stocks[names[0]]?.dates||[],
datasets:names.map((n,i)=>({{label:n,data:stocks[n]?.scores||[],borderColor:tsColors[i%tsColors.length],borderWidth:2,pointRadius:4,pointHoverRadius:6,tension:0.1}}))}},
options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'top',labels:{{color:'#ccc',boxWidth:12}}}}}},
scales:{{x:{{ticks:{{color:'#8899aa',maxTicksLimit:8}}}},y:{{ticks:{{color:'#8899aa'}},min:0,max:100}}}}}}}});
</script></body></html>"""

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == "__main__":
    df = run_backtest()
