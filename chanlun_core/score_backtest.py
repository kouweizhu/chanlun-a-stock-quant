"""技术面评分回测系统 v1
验证 v3.0 技术面评分规则 [-30, 100] 的预测力

回测区间: 2025-08-04 ~ 2026-04-27（每周一采样）
股票池: 8只（蓝筹/成长/周期覆盖）
输出: score_backtest_report.html（交互式可视化报告）

核心设计：
- 每个回测日期截取到该日的 K 线数据，模拟"当时可见信息"
- 严格避免前视偏差
- T+N 收益用收盘价计算
"""

import sys, json, os, math, io
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
import numpy as np

sys.path.insert(0, r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core")

import baostock_utils  # noqa: E402 — print redirect + Baostock session

from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer
from quick_fundamental import classify_by_industry

# ============================================================
# 配置
# ============================================================

# 股票池：优先从 check_negative_news.py 的监控列表读取（与负面扫描一致）
STOCK_POOL = []
_IMPORT_PATH = r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core"

try:
    sys.path.insert(0, _IMPORT_PATH)
    from check_negative_news import MONITOR_LIST
    for code, name, industry in MONITOR_LIST:
        STOCK_POOL.append({"code": code, "name": name, "style": classify_by_industry(industry)})
    print(f"  ✓ 从 check_negative_news 读取股票池: {len(STOCK_POOL)} 只")
except Exception as e:
    print(f"  ⚠ 读取 check_negative_news 失败: {e}")

# 如果读不到，兜底用之前回测用的8只
if not STOCK_POOL:
    STOCK_POOL = [
        {"code": "000001", "name": "平安银行", "style": "蓝筹"},
        {"code": "002415", "name": "海康威视", "style": "蓝筹"},
        {"code": "300750", "name": "宁德时代", "style": "成长"},
        {"code": "601899", "name": "紫金矿业", "style": "周期"},
        {"code": "300059", "name": "东方财富", "style": "成长"},
        {"code": "000002", "name": "万科A",   "style": "周期"},
        {"code": "601318", "name": "中国平安", "style": "蓝筹"},
        {"code": "600309", "name": "万华化学", "style": "周期"},
    ]

START_DATE = "2025-08-04"
END_DATE = "2026-04-27"
DATA_START = "2024-01-01"
OBSERVE_DAYS = [5, 20, 60]

OUTPUT_DIR = os.path.expanduser("~/.hermes/profiles/commander/analysis_reports/backtest")
os.makedirs(OUTPUT_DIR, exist_ok=True)
HTML_PATH = os.path.join(OUTPUT_DIR, "score_backtest_report.html")
CSV_PATH = os.path.join(OUTPUT_DIR, "score_backtest_raw.csv")


# ============================================================
# 数据准备
# ============================================================

def load_all_data() -> Dict[str, pd.DataFrame]:
    dm = DataManager()
    all_data = {}
    for s in STOCK_POOL:
        code = s["code"]
        print(f"  拉取 {code} {s['name']}...")
        df = dm.get_klines(code, 'daily', start_date=DATA_START, end_date=END_DATE)
        if df.empty:
            print(f"  ⚠ {code} 数据为空")
            continue
        df["date_str"] = df["date"].astype(str).str[:10]
        # 确保按日期排序
        df = df.sort_values("date").reset_index(drop=True)
        all_data[code] = df
    return all_data


def get_test_dates(all_data: Dict[str, pd.DataFrame]) -> List[str]:
    codes = list(all_data.keys())
    if not codes:
        return []
    # 从数据中提取所有交易日
    all_dates = sorted(all_data[codes[0]]["date_str"].unique())
    date_set = set(all_dates)

    test_dates = []
    current = datetime.strptime(START_DATE, "%Y-%m-%d")
    end = datetime.strptime(END_DATE, "%Y-%m-%d")

    while current <= end:
        d_str = current.strftime("%Y-%m-%d")
        if d_str in date_set:
            test_dates.append(d_str)
        else:
            # 往后找最近交易日（最多5天）
            for offset in range(1, 6):
                la = (current + timedelta(days=offset)).strftime("%Y-%m-%d")
                if la in date_set:
                    test_dates.append(la)
                    break
        current += timedelta(days=7)

    return test_dates


# ============================================================
# 技术面评分（与 SKILL.md 2.4 节一致）
# ============================================================

def calc_tech_score(analyzer: ChanLunAnalyzer) -> dict:
    """技术面当前状态评分 [-30, 100]
    
    优先使用 per-signal 精细评分（如果有最近的买点），
    否则退回到当前结构位置快照评分。
    
    与 validate_tech_score.compute_technical_score 共享评分逻辑：
    - 有买点 → 调用 compute_technical_score 做9档中枢位置+信号质量评分
    - 无买点 → 简化为中枢位置+MACD+笔方向快照
    """
    from validate_tech_score import compute_technical_score
    
    bis = analyzer.bis or []
    zhongshus = analyzer.zhongshus or []
    buy_sell_points = analyzer.buy_sell_points or []
    macd = analyzer.macd_data or []
    klines = analyzer.klines or []
    current_price = float(klines[-1].close) if klines else None
    
    # 尝试找到最近的买点 → 使用精细评分
    if buy_sell_points and klines:
        last_date_str = date_to_str(klines[-1].date)
        try:
            last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
            recent_buys = []
            for p in buy_sell_points:
                if p.type == "buy":
                    p_dt = datetime.strptime(date_to_str(p.date), "%Y-%m-%d")
                    days = (last_dt - p_dt).days
                    if 0 <= days <= 66:  # 3个月内的买点
                        recent_buys.append((p, days))
            if recent_buys:
                # 取最近的买点做精细评分
                recent_buys.sort(key=lambda x: x[1])
                bp, _ = recent_buys[0]
                result = compute_technical_score(analyzer, None, bp)
                return {
                    "final_score": result['tech_score'],
                    "score_breakdown": result.get('breakdown', ''),
                    "current_price": current_price,
                    "bi_count": len(bis),
                    "zhongshu_count": len(zhongshus),
                    "buy_point_count": len([p for p in buy_sell_points if p.type == "buy"]),
                    "sell_point_count": len([p for p in buy_sell_points if p.type == "sell"]),
                    "macd_status": "golden" if len(macd) >= 2 and float(macd[-1].dif) > float(macd[-1].dea) else "dead",
                }
        except:
            pass
    
    # ──────────────────────────────────────────────
    # 无近期买点 → 结构快照评分（临时 fallback）
    # 
    # TODO(v3.1): 将此 fallback 收敛到 compute_technical_score 的结构位置评分逻辑。
    # 当前这是独立的简化评分器，与主评分器使用不同的加权方案。
    # 长期应让 compute_technical_score 支持 bp=None 模式，
    # 基于中枢位置+MACD+笔方向给出结构评分。
    # ──────────────────────────────────────────────
    score = 0
    details = {}
    
    if bis:
        last_bi = bis[-1]
        if last_bi.direction == "up":
            score += 15; details["latest_bi"] = "up +15"
        elif last_bi.direction == "down":
            if len(bis) >= 2 and float(last_bi.end_price) >= float(bis[-2].end_price) * 0.98:
                score += 10; details["latest_bi"] = "down_benign +10"
            else:
                score -= 3; details["latest_bi"] = "down_break -3"
    
    if len(bis) >= 3 and all(b.direction == "down" for b in bis[-3:]):
        score -= 5; details["three_down"] = "-5"
    
    if zhongshus and current_price:
        zs = zhongshus[-1]
        if current_price > float(zs.zg):
            score += 20; details["zs_position"] = "above +20"
        elif current_price >= float(zs.zd):
            score += 10; details["zs_position"] = "inside +10"
        else:
            score -= 8; details["zs_position"] = "below -8"
    else:
        score -= 5; details["zs_position"] = "none -5"
    
    if len(macd) >= 2:
        if float(macd[-1].dif) > float(macd[-1].dea):
            score += 15; details["macd"] = "golden_cross +15"
        elif float(macd[-1].macd) > float(macd[-2].macd):
            score += 10; details["macd_trend"] = "up +10"
    
    # 买卖点时间距离
    if buy_sell_points and klines:
        last_kline_date = date_to_str(klines[-1].date)
        try:
            ld = datetime.strptime(last_kline_date, "%Y-%m-%d")
            recent_buy_dates = [(p, (ld - datetime.strptime(date_to_str(p.date), "%Y-%m-%d")).days)
                               for p in buy_sell_points if p.type == "buy"]
            if recent_buy_dates:
                min_days = min(d for _, d in recent_buy_dates)
                if min_days <= 22: score += 25; details["buy_point"] = "1m +25"
                elif min_days <= 66: score += 15; details["buy_point"] = "3m +15"
        except: pass
    
    # 卖点未突破、顶背驰（与之前逻辑一致）
    recent_sells = [p for p in buy_sell_points if p.type == "sell"]
    if recent_sells and current_price and zhongshus and current_price < float(zhongshus[-1].zg):
        score -= 8; details["sell_unresolved"] = "-8"
    
    sells = [p for p in buy_sell_points if p.type == "sell"]
    if sells and len(macd) >= 10:
        score -= 8; details["divergence"] = "-8"
    
    final_score = max(-30, min(100, score))
    
    return {
        "final_score": final_score,
        "score_breakdown": json.dumps(details, ensure_ascii=False),
        "current_price": current_price,
        "bi_count": len(bis),
        "zhongshu_count": len(zhongshus),
        "buy_point_count": len([p for p in buy_sell_points if p.type == "buy"]),
        "sell_point_count": len([p for p in buy_sell_points if p.type == "sell"]),
        "macd_status": "golden" if len(macd) >= 2 and float(macd[-1].dif) > float(macd[-1].dea) else "dead",
    }


# ============================================================
# 主回测
# ============================================================

def run_backtest():
    t0 = datetime.now()
    print("=" * 60)
    print("技术面评分回测 v1")
    print(f"区间: {START_DATE} ~ {END_DATE}")
    print(f"股票: {len(STOCK_POOL)} 只")
    print("=" * 60)

    # 1. 加载数据
    print("\n[1/4] 加载历史数据...")
    all_data = load_all_data()

    # 2. 日期
    print("\n[2/4] 生成回测日期...")
    test_dates = get_test_dates(all_data)
    print(f"  共 {len(test_dates)} 个采样点")

    # 3. 回测循环
    print("\n[3/4] 执行回测...")
    records = []

    for t_idx, t_date in enumerate(test_dates):
        if t_idx % 5 == 0:
            print(f"  [{t_idx+1}/{len(test_dates)}] {t_date}...")
        
        for s in STOCK_POOL:
            code = s["code"]
            if code not in all_data:
                continue

            df = all_data[code]
            mask = df["date_str"] == t_date
            if not mask.any():
                continue
            
            pos = df[mask].index[0]
            pos_iloc = df.index.get_loc(pos)
            
            # 截取到该日期为止（至少60根K线）
            if pos_iloc < 60:
                continue
            
            df_until = df.iloc[:pos_iloc + 1]

            kline_dicts = []
            for _, row in df_until.iterrows():
                kline_dicts.append({
                    "date": str(row["date"])[:10],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })

            try:
                analyzer = ChanLunAnalyzer()
                analyzer.analyze(kline_dicts)
                score_result = calc_tech_score(analyzer)
            except Exception as e:
                continue

            # 计算后续收益
            returns = {}
            for n in OBSERVE_DAYS:
                target_iloc = pos_iloc + n
                if target_iloc < len(df):
                    c0 = float(df.iloc[pos_iloc]["close"])
                    c1 = float(df.iloc[target_iloc]["close"])
                    returns[f"T+{n}"] = round((c1 - c0) / c0, 4) if c0 > 0 else None
                else:
                    returns[f"T+{n}"] = None

            records.append({
                "date": t_date,
                "stock_code": code,
                "stock_name": s["name"],
                "style": s["style"],
                "price": score_result["current_price"],
                "tech_score": score_result["final_score"],
                "bi_count": score_result["bi_count"],
                "zhongshu_count": score_result["zhongshu_count"],
                "buy_count": score_result["buy_point_count"],
                "sell_count": score_result["sell_point_count"],
                "macd": score_result["macd_status"],
                "breakdown": score_result["score_breakdown"],
                "decision": classify_decision(score_result["final_score"]),
                "position": classify_position(score_result["final_score"]),
                **returns,
            })

    df_result = pd.DataFrame(records)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n  共 {len(df_result)} 条评分记录 ({elapsed:.0f}s)")

    # 4. 统计分析
    print("\n[4/4] 统计分析...")
    stats = {}
    
    if not df_result.empty and 'tech_score' in df_result.columns:
        bins = [-float('inf'), 50, 60, 70, 80, float('inf')]
        labels = ['D(回避)', 'C(观望)', 'B(关注)', 'A(推荐)', 'A+(强推)']
        df_result['group'] = pd.cut(df_result['tech_score'], bins=bins, labels=labels, right=False)

        # 分组收益
        print("\n  === 分组收益 ===")
        for n in OBSERVE_DAYS:
            col = f"T+{n}"
            if col not in df_result.columns:
                continue
            print(f"\n  --- {col} ---")
            grp = df_result.groupby('group')[col].agg(['mean', 'median', 'count', 'std'])
            for g in labels:
                if g in grp.index:
                    r = grp.loc[g]
                    print(f"  {g:>8}: 均值={r['mean']:+.2%}  中位={r['median']:+.2%}  n={int(r['count'])}")
            stats[f"T+{n}"] = {g: {"mean": float(grp.loc[g, 'mean']), "median": float(grp.loc[g, 'median']), "count": int(grp.loc[g, 'count'])} for g in labels if g in grp.index}

        # 区分度
        print("\n  === 区分度 (A组 vs D组) ===")
        for n in OBSERVE_DAYS:
            col = f"T+{n}"
            if col not in df_result.columns:
                continue
            a = df_result[df_result['group'].isin(['A(推荐)', 'A+(强推)'])][col].dropna()
            d = df_result[df_result['group'] == 'D(回避)'][col].dropna()
            if len(a) > 0 and len(d) > 0:
                diff = a.mean() - d.mean()
                print(f"  {col}: A={a.mean():+.2%}  D={d.mean():+.2%}  差={diff:+.2%}")

        # 个股
        print("\n  === 个股概览 ===")
        ss = df_result.groupby('stock_name').agg(
            均分=('tech_score', 'mean'), 范围=('tech_score', lambda x: f"{x.min():.0f}~{x.max():.0f}"),
            T20收益=('T+20', 'mean'), 样本=('tech_score', 'count')
        ).round(2)
        print(ss.to_string())

    # 5. 保存
    df_result.to_csv(CSV_PATH, index=False, encoding='utf-8-sig')
    print(f"\n  CSV → {CSV_PATH}")

    generate_html(df_result, stats)
    print(f"  HTML → {HTML_PATH}")

    print(f"\n✅ 回测完成 ({elapsed:.0f}s)")
    return df_result


def classify_decision(s):
    if s >= 80: return "强力推荐"
    if s >= 70: return "推荐"
    if s >= 60: return "关注"
    if s >= 50: return "观望"
    return "回避"

def classify_position(s):
    if s >= 80: return "30%-50%"
    if s >= 70: return "20%-30%"
    if s >= 60: return "10%-20%"
    return "0%"


# ============================================================
# HTML 报告
# ============================================================

def generate_html(df: pd.DataFrame, stats: dict):
    # 准备个股时间序列数据
    stocks_json = {}
    if 'stock_name' in df.columns:
        for name in df['stock_name'].unique():
            sub = df[df['stock_name'] == name].sort_values('date')
            stocks_json[name] = {
                "dates": sub['date'].tolist(),
                "scores": [float(x) if not pd.isna(x) else None for x in sub['tech_score']],
                "prices": [float(x) if not pd.isna(x) else None for x in sub['price']],
            }

    # 散点数据 (T+20)
    scatter = []
    if 'T+20' in df.columns and 'tech_score' in df.columns:
        for _, r in df.iterrows():
            t20 = r.get('T+20')
            if pd.notna(t20):
                scatter.append({
                    "x": float(r['tech_score']),
                    "y": float(t20),
                    "label": f"{r['stock_name']} ({r['date']})",
                    "style": r.get('style', ''),
                })

    stats_json = json.dumps(stats, ensure_ascii=False)
    stocks_json_str = json.dumps(stocks_json, ensure_ascii=False)
    scatter_json = json.dumps(scatter, ensure_ascii=False)

    # 分组颜色
    group_colors = {
        "A+(强推)": "#c0392b", "A(推荐)": "#e74c3c",
        "B(关注)": "#e67e22", "C(观望)": "#f39c12", "D(回避)": "#27ae60"
    }
    
    # 生成统计表格 HTML
    stats_html = ""
    for period, groups in stats.items():
        stats_html += f"<h3>{period} 分组统计</h3><table><tr><th>分组</th><th>均值</th><th>中位数</th><th>样本数</th></tr>"
        for g, d in groups.items():
            color = group_colors.get(g, "#999")
            cls = "positive" if d['mean'] > 0 else "negative"
            stats_html += f'<tr><td><span style="color:{color}">●</span> {g}</td><td class="{cls}">{d["mean"]:+.2%}</td><td>{d["median"]:+.2%}</td><td>{d["count"]}</td></tr>'
        stats_html += "</table>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>技术面评分回测报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: system-ui, -apple-system, 'Microsoft YaHei', sans-serif; background: #0f1923; color: #e0e0e0; }}
.header {{ background: linear-gradient(135deg, #1a1a2e, #16213e); padding: 30px; border-bottom: 1px solid #2a2a4a; }}
.header h1 {{ font-size: 24px; color: #fff; }}
.header p {{ color: #8899aa; margin-top: 8px; font-size: 14px; }}
.container {{ max-width: 1300px; margin: 0 auto; padding: 20px; }}
.card {{ background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px; padding: 24px; margin-bottom: 20px; }}
.card h2 {{ font-size: 18px; margin-bottom: 16px; color: #ccc; }}
.chart-box {{ height: 400px; position: relative; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th, td {{ padding: 10px 14px; text-align: center; border-bottom: 1px solid #2a2a4a; }}
th {{ color: #8899aa; font-weight: 500; }}
.positive {{ color: #e74c3c !important; }}
.negative {{ color: #27ae60 !important; }}
.badge {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }}
.stat-card {{ background: #16213e; border-radius: 8px; padding: 16px; text-align: center; }}
.stat-card .num {{ font-size: 28px; font-weight: 700; }}
.stat-card .label {{ font-size: 12px; color: #8899aa; margin-top: 4px; }}
</style>
</head>
<body>

<div class="header">
  <h1>📊 技术面评分回测报告</h1>
  <p>回测区间: {START_DATE} ~ {END_DATE} | 股票池: {len(STOCK_POOL)} 只 | 总采样: {len(df)} 条</p>
</div>

<div class="container">

<!-- 概览卡片 -->
<div class="stats-grid" id="overview"></div>

<!-- 分组收益对比 -->
<div class="card">
  <h2>分组收益对比</h2>
  <div class="chart-box"><canvas id="groupChart"></canvas></div>
</div>

<!-- 评分 vs 收益散点 -->
<div class="card">
  <h2>评分 vs T+20收益</h2>
  <div class="chart-box"><canvas id="scatterChart"></canvas></div>
</div>

<!-- 个股评分时间序列 -->
<div class="card">
  <h2>个股评分时间序列</h2>
  <div class="chart-box"><canvas id="tsChart"></canvas></div>
</div>

<!-- 统计表格 -->
<div class="card">
  <h2>分组详细统计</h2>
  {stats_html}
</div>

</div>

<script>
const groupColors = {json.dumps(group_colors, ensure_ascii=False)};
const stats = {stats_json};
const stocks = {stocks_json_str};
const scatterData = {scatter_json};

// 概览
const overview = document.getElementById('overview');
const totalScored = {len(df)};
const avgScore = {float(df['tech_score'].mean()) if not df.empty and 'tech_score' in df.columns else 0};
const t20Groups = stats['T+20'] || {{}};
const aCount = (t20Groups['A(推荐)']?.count || 0) + (t20Groups['A+(强推)']?.count || 0);
const dCount = t20Groups['D(回避)']?.count || 0;
const aMean = t20Groups['A(推荐)']?.mean || 0;
const dMean = t20Groups['D(回避)']?.mean || 0;

overview.innerHTML = `
  <div class="stat-card"><div class="num" style="color:#fff">${{totalScored}}</div><div class="label">评分记录</div></div>
  <div class="stat-card"><div class="num" style="color:#888">${{avgScore.toFixed(1)}}</div><div class="label">平均评分</div></div>
  <div class="stat-card"><div class="num" style="color:#e74c3c">${{aCount}}</div><div class="label">推荐次数</div></div>
  <div class="stat-card"><div class="num" style="color:#27ae60">${{dCount}}</div><div class="label">回避次数</div></div>
  <div class="stat-card"><div class="num" style="color:${{aMean > 0 ? '#e74c3c' : '#27ae60'}}">${{(aMean*100).toFixed(1)}}%</div><div class="label">推荐组T+20均收益</div></div>
  <div class="stat-card"><div class="num" style="color:${{dMean > 0 ? '#e74c3c' : '#27ae60'}}">${{(dMean*100).toFixed(1)}}%</div><div class="label">回避组T+20均收益</div></div>
`;

// 1. 分组收益对比图
const groupCtx = document.getElementById('groupChart').getContext('2d');
const periods = Object.keys(stats);
const groups = ['A+(强推)', 'A(推荐)', 'B(关注)', 'C(观望)', 'D(回避)'];

new Chart(groupCtx, {{
  type: 'bar',
  data: {{
    labels: periods,
    datasets: groups.filter(g => stats[periods[0]] && stats[periods[0]][g]).map(g => ({{
      label: g,
      data: periods.map(p => stats[p]?.[g]?.mean || 0),
      backgroundColor: groupColors[g] || '#666',
      borderRadius: 4,
    }})),
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'top', labels: {{ color: '#ccc' }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#8899aa' }} }},
      y: {{ ticks: {{ color: '#8899aa', callback: v => (v*100).toFixed(0)+'%' }} }},
    }},
  }},
}});

// 2. 散点图
const scCtx = document.getElementById('scatterChart').getContext('2d');
const styleColors = {{'蓝筹':'#3498db','成长':'#e74c3c','周期':'#2ecc71'}};
new Chart(scCtx, {{
  type: 'scatter',
  data: {{
    datasets: [{{
      label: '评分 vs T+20收益',
      data: scatterData,
      backgroundColor: scatterData.map(d => styleColors[d.style] || '#666'),
      radius: 5,
    }}],
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: ctx => `${{ctx.raw.label}}: 评分=${{ctx.raw.x}} T+20=${{(ctx.raw.y*100).toFixed(1)}}%` }} }},
    }},
    scales: {{
      x: {{ title: {{ display: true, text: '技术面评分', color: '#8899aa' }}, ticks: {{ color: '#8899aa' }} }},
      y: {{ title: {{ display: true, text: 'T+20收益', color: '#8899aa' }}, ticks: {{ color: '#8899aa', callback: v => (v*100).toFixed(0)+'%' }} }},
    }},
  }},
}});

// 3. 时间序列
const tsCtx = document.getElementById('tsChart').getContext('2d');
const stockNames = Object.keys(stocks);
const tsColors = ['#3498db','#e74c3c','#2ecc71','#f39c12','#9b59b6','#1abc9c','#e67e22','#95a5a6'];

new Chart(tsCtx, {{
  type: 'line',
  data: {{
    labels: stocks[stockNames[0]]?.dates || [],
    datasets: stockNames.map((name, i) => ({{
      label: name,
      data: stocks[name]?.scores || [],
      borderColor: tsColors[i % tsColors.length],
      backgroundColor: tsColors[i % tsColors.length] + '20',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.1,
    }})),
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'top', labels: {{ color: '#ccc', boxWidth: 12 }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#8899aa', maxTicksLimit: 15 }} }},
      y: {{ ticks: {{ color: '#8899aa' }}, min: -35, max: 105 }},
    }},
  }},
}});
</script>
</body>
</html>"""

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == "__main__":
    df = run_backtest()
