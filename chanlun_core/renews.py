#!/usr/bin/env python
"""renews.py — 用 Tavily API 重跑消息面评分并更新汇总报告

用法: TAVILY_API_KEY=tvly-dev-xxx python renews.py
"""

import sys, os, json, time
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from file_utils import safe_read_json
from pool_screener import scan_news, W_TECH, W_FUND, W_NEWS
from composite_scorer import compute_3d_score, position_reason

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scanner_cache.json")
OUTPUT_BASE = "D:/常用文件/股票池推荐股"
TOP_N = 30

# 从缓存取 Phase 1 候选股列表
cache = safe_read_json(CACHE)

candidates = cache['candidates']
print(f"加载 {len(candidates)} 只候选股")

# 重新做消息面评分（只跑前 30）
print(f"\nTavily 消息面扫描 (前 {TOP_N} 只)...")
updated = []
for i, c in enumerate(candidates[:TOP_N]):
    print(f"  [{i+1}/{min(TOP_N, len(candidates))}] {c['code']} {c['name']}...", end=" ", flush=True)
    
    news_score, news_detail = scan_news(c['code'], c['name'])
    
    # 估算技术分和基本面分
    tech_score = min(86, c['score'] * 17.2)  # score 5 → 86, score 4 → 69, score 3 → 52
    fund_score = 70  # 默认（无法重新查）
    
    result_3d = compute_3d_score(
        tech_score=tech_score, fund_score=fund_score, news_score=news_score,
        w_tech=W_TECH, w_fund=W_FUND, w_news=W_NEWS, resonance_penalty=True
    )
    
    updated.append({
        'code': c['code'], 'name': c['name'], 'score': c['score'],
        'pattern': c.get('pattern', ''), 'price': c.get('price', 0),
        'zg': c.get('zg', 0), 'zd': c.get('zd', 0),
        'buy_type': c.get('buy_type', ''), 'buy_date': c.get('buy_date', ''),
        'buy_price': c.get('buy_price', 0),
        'total_bis': c.get('total_bis', 0), 'total_zs': c.get('total_zs', 0),
        'buy_count': c.get('buy_count', 0), 'sell_count': c.get('sell_count', 0),
        'tech_score': round(tech_score, 1),
        'fund_score': round(fund_score, 1),
        'news_score': round(news_score, 1),
        'composite': result_3d.composite,
        'grade': result_3d.grade,
        'position': result_3d.position,
        'position_pct': f"{result_3d.position*100:.0f}%",
        'can_buy': result_3d.can_buy,
        'reason': position_reason(result_3d),
        'resonance': result_3d.components.get('resonance_penalty_applied', False),
        'news_detail': news_detail,
    })
    print(f"消息{news_score} → 综合{result_3d.composite:.0f}({result_3d.grade})")

    if i < TOP_N - 1:
        time.sleep(0.8)  # Tavily API 限流

# 排序
updated.sort(key=lambda s: -s['composite'])

# 输出汇总表
date_str = datetime.now().strftime('%Y-%m-%d')
print(f"\n{'='*60}")
print(f"🏆 更新后 Top {min(TOP_N, len(updated))} (带 Tavily 消息面)")
print(f"{'='*60}")
print(f"{'#':>3} {'代码':<8} {'名称':<8} {'综合':>5} {'等级':>4} {'技术':>5} {'基本面':>5} {'消息':>5} {'仓位':>5} {'消息详情'}")
for i, s in enumerate(updated, 1):
    print(f"{i:>3} {s['code']:<8} {s['name']:<8} {s['composite']:>5.1f} {s['grade']:>4} "
          f"{s['tech_score']:>5.1f} {s['fund_score']:>5.1f} {s['news_score']:>5.1f} {s['position_pct']:>5} "
          f"{s['news_detail']}")

# 保存结果
result_path = os.path.join(OUTPUT_BASE, f"消息面重评_{date_str}.json")
with open(result_path, 'w', encoding='utf-8') as f:
    json.dump(updated, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存: {result_path}")
