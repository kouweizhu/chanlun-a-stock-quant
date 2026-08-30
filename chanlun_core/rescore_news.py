#!/usr/bin/env python
# ⛔⛔⛔ 已废弃归档 — 勿运行 (v5.4, 终审 C-08, 2026-08-24) ⛔⛔⛔
#
# 废弃原因：
#   1. 模块级直接执行（无 if __name__ 保护 + 无 argparse），import 即跑全流程；
#   2. OUTPUT_MD 硬编码"扫描汇总_2026-05-01.md"（伪造元数据/死日期），
#      误运行会用旧四维口径覆盖真实汇总报告，污染 JSON 与报告口径；
#   3. 职责已被完全承接：
#        - 消息面补扫 → pool_screener Phase2 内 news_scanner（含 LLM 语义评分）
#        - 五维重算+报告重生成 → fund_factor_rescore.py --top N --report
#
# 本文件仅作历史参考保留。确需查阅旧实现请读 git 历史，
# 不要在本文件上打补丁。运行防护见上方第16-18行 banner+sys.exit(2)。
"""(历史docstring) 批量消息面补扫 — 读取 Phase 2 缓存，对 Top 30 跑 scan_news()，更新 MD 报告"""
import sys
print("[rescore_news] ⛔ 此脚本已废弃归档：消息面补扫在 pool_screener Phase2 内执行，"
      "五维重算用 fund_factor_rescore --report。本次调用已拦截退出。", file=sys.stderr)
sys.exit(2)
raise SystemExit  # pragma: no cover —— 上方 exit(2) 已终止；以下旧代码仅供阅读

import os, json, time  # noqa: E402  (DEAD CODE below this line)
from date_utils import date_to_str, parse_date_to_datetime  # noqa: F401
from datetime import datetime  # noqa: F401
from dotenv import load_dotenv  # noqa: F401

# ── 加载 .env ──
_hermes_home = os.environ.get("HERMES_HOME", "")
if _hermes_home:
    _parent = os.path.dirname(os.path.dirname(_hermes_home.rstrip("/")))
    load_dotenv(os.path.join(_parent, ".env"))
else:
    load_dotenv(os.path.expanduser("~/.hermes/.env"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pool_screener import scan_news
from composite_scorer import compute_3d_score, Score3D

# ── 配置 ──
PHASE2_JSON = ".phase2_results.json"
OUTPUT_MD = "D:/常用文件/股票池推荐股/扫描汇总_2026-05-01.md"
TOP_N = 30

# ── 加载缓存 ──
with open(PHASE2_JSON) as f:
    all_stocks = json.load(f)

print(f"[News补扫] 共 {len(all_stocks)} 只候选，补扫 Top {TOP_N}")
print(f"  消息源: 全量采集（东财新闻/涨停池/雪球/同花顺/新浪/CCTV/Tavily）")
print()

top30 = all_stocks[:TOP_N]
updates = []

for i, s in enumerate(top30):
    code, name = s["code"], s["name"]
    old_news = s.get("news_score", 50)
    old_composite = s.get("composite", 0)

    print(f"  [{i+1:2d}/{TOP_N}] {code} {name:<6s} ", end="", flush=True)

    try:
        news_score, news_detail = scan_news(code, name)
    except Exception as e:
        print(f"✗ 异常: {str(e)[:40]}")
        news_score, news_detail = 50, f"[Fallback] 异常({str(e)[:30]})"

    # 重算综合分（含 alpha + veto）
    result = compute_3d_score(
        tech_score=s["tech_score"],
        fund_score=s["fund_score"],
        alpha_score=s.get("alpha_score", 50.0),
        news_score=news_score,
        code=code, name=name,
        news_detail=news_detail,
        resonance_penalty=True,
    )
    new_composite = result.composite

    delta = new_composite - old_composite
    delta_str = f"{'+' if delta >= 0 else ''}{delta:.1f}"

    s["tech_score"] = round(s.get("tech_score", 0), 1)
    s["fund_score"] = round(s.get("fund_score", 0), 1)
    s["news_score"] = round(news_score, 1)
    s["alpha_score"] = round(s.get("alpha_score", 50.0), 1)
    s["news_detail"] = news_detail
    s["composite"] = round(new_composite, 1)

    # 综合等级
    # 使用 compute_3d_score 返回的等级和仓位（与主系统一致）
    s["grade"] = result.grade
    s["position"] = f"{result.position*100:.0f}%" if result.position > 0 else "0%"
    updates.append({
        "code": code, "name": name,
        "old_news": old_news, "new_news": news_score,
        "old_composite": old_composite, "new_composite": new_composite,
        "detail": news_detail,
    })

    time.sleep(0.5)  # 搜索间隔，避免被限流

# ── 重新排序（按新综合分降序） ──
top30.sort(key=lambda s: s["composite"], reverse=True)

# ── 保存回 phase2_results ──
all_stocks[:TOP_N] = top30
with open(PHASE2_JSON, "w") as f:
    json.dump(all_stocks, f, ensure_ascii=False, indent=2)
print(f"\n[保存] 已更新 {PHASE2_JSON}")

# ── 生成 MD 报告 ──
def fmt_num(v):
    """格式化数字：统一一位小数，与原始报告一致"""
    if isinstance(v, (int, float)):
        return f"{v:.1f}"
    return str(v)

lines = []
lines.append("# A500 股票池智能筛选汇总")
lines.append(f"**扫描日期**: 2026-05-01 13:49 | **消息面补扫**: {datetime.now().strftime('%H:%M')}")
lines.append(f"**候选股**: 86 只 | **推荐**: 86 只")
lines.append("")
lines.append("## 🏆 推荐列表（按综合分降序）")
lines.append("")
lines.append("| # | 代码 | 名称 | 综合 | 等级 | 技术 | Alpha | 基本面 | 消息 | 仓位 | 模式 | 行业 |")
lines.append("|---|------|------|:----:|:----:|:----:|:-----:|:------:|:----:|:----:|------|------|")

for i, s in enumerate(top30):
    # Veto/降级标记
    veto = s.get("veto_reasons", [])
    severe = s.get("severe_reasons", [])
    marker = ""
    if veto:
        marker = " ⛔"
    elif severe:
        marker = " ⚠"

    alpha = fmt_num(s.get("alpha_score", 50))
    grade_val = s.get("grade", "🟡B")
    if veto:
        grade_val = "⛔D" + marker

    line = (
        f"| {i+1}{marker} | {s['code']} | {s['name']} "
        f"| **{fmt_num(s['composite'])}** | {grade_val} "
        f"| {fmt_num(s['tech_score'])} | {alpha} "
        f"| {fmt_num(s['fund_score'])} "
        f"| {fmt_num(s['news_score'])} | {s.get('position', '30%')} "
        f"| {s.get('pattern', '')} | {s.get('industry', '').strip()[:10]} |"
    )
    lines.append(line)

# 补充 bottom 56 的信息（在 .phase2_results 中已有但不需要逐行输出）
remaining = all_stocks[TOP_N:]
lines.append("")
lines.append(f"*注：另有 {len(remaining)} 只候选股，消息面未补扫（综合分均低于 Top {TOP_N}）*")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 📁 详细报告")
lines.append("")

# 生成 Top 30 文件链接
file_links = []
for s in top30:
    code, name = s["code"], s["name"]
    file_links.append(f"- [{name}({code})]({name}_{code}/{code}_score_report.md) — HTML技术分析 + 评分报告")

lines.extend(file_links)
lines.append("")
lines.append("---")
lines.append("*报告由 Alpha Zoo 四维分析系统自动生成（tech=0.35 fund=0.30 alpha=0.25 news=0.10），消息面来自 Tavily/Metaso，Alpha因子来自 GTJA 191 幸存因子*")
lines.append("*⛔=风控否决  ⚠=严重降级*")

content = "\n".join(lines) + "\n"

with open(OUTPUT_MD, "w", encoding="utf-8") as f:
    f.write(content)
print(f"[报告] 已更新 {OUTPUT_MD}")

# ── 输出变更摘要 ──
print()
print("=" * 50)
print("变更摘要（综合分变化 Top 5）")
print("=" * 50)
changes = sorted(updates, key=lambda u: abs(u["new_composite"] - u["old_composite"]), reverse=True)
for u in changes[:5]:
    delta = u["new_composite"] - u["old_composite"]
    sign = "+" if delta >= 0 else ""
    print(f"  {u['code']} {u['name']:<6s} 综合 {u['old_composite']:.0f}→{u['new_composite']:.0f} ({sign}{delta:.1f}) news {u['old_news']:.0f}→{u['new_news']:.0f}")
print(f"\n综合分↑: {sum(1 for u in updates if u['new_composite'] > u['old_composite'])} 只")
print(f"综合分↓: {sum(1 for u in updates if u['new_composite'] < u['old_composite'])} 只")
print(f"综合分=: {sum(1 for u in updates if u['new_composite'] == u['old_composite'])} 只")
