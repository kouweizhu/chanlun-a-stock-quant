#!/usr/bin/env python
"""grid_search.py — 参数网格搜索

对 weight_schemes（权重方案）、confidence_anchoring（置信度锚定开关）
做网格搜索，按 Sharpe 比率排序输出结果。

核心逻辑：
  1. 先对每只股票运行技术评分验证，获取所有买入信号
  2. 对每种权重+锚定组合，计算每个信号的复合评分
  3. 仅保留复合评分 >= SCORE_THRESHOLD 的信号
  4. 计算保留信号集的平均收益率、胜率、Sharpe
  5. 按 Sharpe 排序输出

用法：
  python grid_search.py
"""

import sys, os, json, time
from date_utils import date_to_str, parse_date_to_datetime
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 确保能找到同级模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_tech_score
from quick_fundamental import classify_by_industry

# 股票池：与负面消息扫描共享同一清单
try:
    from check_negative_news import MONITOR_LIST
    STOCK_POOL = [(c, n) for c, n, _ in MONITOR_LIST]
except ImportError:
    from stock_pool import DEFAULT_POOL
    STOCK_POOL = DEFAULT_POOL[:18]
import quick_fundamental

# ============================================================
# 基本面评分缓存（key: (symbol, year, quarter)）
# ============================================================
_fund_score_cache = {}

def get_quarter_for_date(date_str: str):
    """返回给定日期时可用的最新财务数据 (year, quarter)
    
    中国财报披露截止日：
    - 一季报(Q1): 4/30
    - 半年报(Q2): 8/31
    - 三季报(Q3): 10/31
    - 年报(Q4): 次年4/30
    """
    dt = datetime.strptime(date_to_str(date_str), '%Y-%m-%d')
    y, m = dt.year, dt.month
    
    if m >= 11:      return (y, 3)     # 11月后可用三季报（10/31截止）
    elif m >= 9:     return (y, 2)     # 9-10月可用半年报（8/31截止）
    elif m >= 5:     return (y, 1)     # 5-8月可用一季报（4/30截止）
    elif m >= 1:     return (y - 1, 4) # 1-4月只有上年年报


def get_financial_data(symbol: str, date_str: str) -> dict:
    """获取给定日期可用的历史财务数据（不含估值，估值按股价实时算）
    
    返回: {'profitability': {...}, 'growth': {...}, 'health': {...}, 'year': y, 'quarter': q}
    缓存于 _fund_data_cache[(symbol, year, quarter)]
    """
    year, quarter = get_quarter_for_date(date_str)
    cache_key = (symbol, year, quarter)
    
    if cache_key in _fund_score_cache:
        return _fund_score_cache[cache_key]
    
    try:
        import baostock_utils
        bs_code = quick_fundamental.to_bs_code(symbol)
        bs, _ = baostock_utils.ensure_login()
        
        data = {'profitability': {}, 'growth': {}, 'health': {}, 'year': year, 'quarter': quarter}
        
        # 盈利能力
        rs = bs.query_profit_data(bs_code, year=year, quarter=quarter)
        if rs.error_code == '0':
            while rs.next():
                row = rs.get_row_data()
                fields = [f.strip() for f in rs.fields]
                vals = dict(zip(fields, row))
                if vals.get('code') == bs_code:
                    data['profitability'] = {
                        'roeAvg': quick_fundamental.safe_float(vals.get('roeAvg')),
                        'npMargin': quick_fundamental.safe_float(vals.get('npMargin')),
                        'gpMargin': quick_fundamental.safe_float(vals.get('gpMargin')),
                        'epsTTM': quick_fundamental.safe_float(vals.get('epsTTM')),
                        'totalRevenue': quick_fundamental.safe_float(vals.get('MBRevenue')),
                    }
                    break
        
        # 成长性
        rs = bs.query_growth_data(bs_code, year=year, quarter=quarter)
        if rs.error_code == '0':
            while rs.next():
                row = rs.get_row_data()
                fields = [f.strip() for f in rs.fields]
                vals = dict(zip(fields, row))
                if vals.get('code') == bs_code:
                    data['growth'] = {
                        'YOYNI': quick_fundamental.safe_float(vals.get('YOYNI')),
                        'YOYEquity': quick_fundamental.safe_float(vals.get('YOYEquity')),
                        'YOYEPSBasic': quick_fundamental.safe_float(vals.get('YOYEPSBasic')),
                    }
                    break
        
        # 资产负债
        rs = bs.query_balance_data(bs_code, year=year, quarter=quarter)
        if rs.error_code == '0':
            while rs.next():
                row = rs.get_row_data()
                fields = [f.strip() for f in rs.fields]
                vals = dict(zip(fields, row))
                if vals.get('code') == bs_code:
                    data['health'] = {
                        'liabilityToAsset': quick_fundamental.safe_float(vals.get('liabilityToAsset')),
                        'assetToEquity': quick_fundamental.safe_float(vals.get('assetToEquity')),
                    }
                    break
        
        # 现金流
        rs = bs.query_cash_flow_data(bs_code, year=year, quarter=quarter)
        if rs.error_code == '0':
            while rs.next():
                row = rs.get_row_data()
                fields = [f.strip() for f in rs.fields]
                vals = dict(zip(fields, row))
                if vals.get('code') == bs_code:
                    data['health']['CFOToOR'] = quick_fundamental.safe_float(vals.get('CFOToOR'))
                    break
        
    except Exception as e:
        print(f"    [fund_data] {symbol} @ {date_str}: 查询失败({e})", flush=True)
        data = None
    
    _fund_score_cache[cache_key] = data
    return data


def get_fund_score(symbol: str, date_str: str, stock_price: float = None) -> float:
    """获取给定日期的历史基本面评分 [0,100]
    
    财务数据（盈利/成长/健康）缓存复用，PE估值按信号日股价实时计算。
    """
    data = get_financial_data(symbol, date_str)
    if data is None:
        return 50.0
    
    scores = {
        'profitability': data['profitability'],
        'growth': data['growth'],
        'health': data['health'],
        'valuation': {},
    }
    
    # 估值维度：用信号日股价 + 已获取的EPS数据计算PE
    eps_val = data['profitability'].get('epsTTM')
    if stock_price and stock_price > 0 and eps_val and eps_val > 0:
        pe_val = stock_price / eps_val
        scores['valuation'] = {'peTTM': pe_val, 'pbMRQ': None}
    
    scores['industry'] = ''
    scores['market_cap'] = None
    scores['dividend_yield'] = None
    
    result = quick_fundamental.calculate_fundamental_score(scores)
    return result['total_score']

# ============================================================
# 网格搜索参数
# ============================================================

# 权重方案: (W_tech, W_fund, W_news)
#   W_tech — 技术评分权重（tech_score, 0-100）
#   W_fund — 基本面评分权重（fund_score from quick_fundamental, 0-100）
#   W_news — 消息面权重（固定 50 分中性）
weight_schemes = [
    (0.40, 0.30, 0.30),  # 默认（技术面为主）
    (0.35, 0.35, 0.30),  # 均衡
    (0.30, 0.40, 0.30),  # 基本面优先
    (0.25, 0.50, 0.25),  # 基本面优先(极端)
]

# 置信度锚定开关
confidence_anchorings = [True, False]

# 评分阈值（固定使用当前决策矩阵 B 级 = 60 分）
# 仅保留复合评分 >= 60 的信号
SCORE_THRESHOLD = 60.0

# 待验证股票数量（取前 N 只）
NUM_STOCKS = 30

# ============================================================
# 置信度锚定函数
# ============================================================

def compute_composite_score(tech_score, fund_score, confidence, w_tech, w_fund, w_news, use_anchoring):
    """计算综合评分
    
    tech_score: 0-100（技术评分）
    fund_score: 0-100（基本面评分，来自 quick_fundamental.py）
    confidence: 0-5（缠论置信度，仅用于锚定权重缩放）
    news_score = 50（固定中性）
    
    当 use_anchoring=True:
      conf_norm = confidence / 5.0 (0~1)
      scale_factor = 0.5 + conf_norm (低置信→0.5, 高置信→1.5)
      tech/fund 权重按 scale_factor 缩放, news 权重按 (2.0 - scale_factor) 缩放
      最后归一化使权重之和仍为 1
      
      注意：anchoring 使用 confidence（缠论多级别确认强度）来控制技术面和
      基本面的权重——高置信度时更相信技术和基本面，低置信度时给消息面更多权重。
      这不是基本面评分本身，而是对三个维度可信度的动态调节。
    
    共振惩罚（v3.0 新增）：当 tech_score < 60 且 fund_score < 60 时，
    综合评分中的负分部分减半，避免过度惩罚双弱信号。
    """
    news_score = 50.0

    if use_anchoring:
        conf_norm = min(confidence / 5.0, 1.0)
        scale = 0.5 + conf_norm  # [0.5, 1.5]
        w_tech_eff = w_tech * scale
        w_fund_eff = w_fund * scale
        w_news_eff = w_news * (2.0 - scale)  # [1.5, 0.5]
        # 归一化
        total = w_tech_eff + w_fund_eff + w_news_eff
        w_tech_eff /= total
        w_fund_eff /= total
        w_news_eff /= total
        composite = tech_score * w_tech_eff + fund_score * w_fund_eff + news_score * w_news_eff
        
        # 共振惩罚（使用有效权重）
        if tech_score < 60 and fund_score < 60:
            weak_tech = max(0, (60 - tech_score) * w_tech_eff)
            weak_fund = max(0, (60 - fund_score) * w_fund_eff)
            composite += (weak_tech + weak_fund) * 0.5
    else:
        composite = tech_score * w_tech + fund_score * w_fund + news_score * w_news
        
        # 共振惩罚
        if tech_score < 60 and fund_score < 60:
            weak_tech = max(0, (60 - tech_score) * w_tech)
            weak_fund = max(0, (60 - fund_score) * w_fund)
            composite += (weak_tech + weak_fund) * 0.5

    return composite


# ============================================================
# 指标计算
# ============================================================

def calc_metrics(returns_20d, returns_60d):
    """计算一组信号的平均收益率、胜率、盈亏比、Sharpe
    
    参数:
      returns_20d: list[float], 20日收益率列表（允许 None/NaN）
      returns_60d: list[float], 60日收益率列表（允许 None/NaN）
    """
    # 过滤无效值
    r20 = [r for r in returns_20d if r is not None and not (isinstance(r, float) and np.isnan(r))]
    r60 = [r for r in returns_60d if r is not None and not (isinstance(r, float) and np.isnan(r))]

    n = len(r20)
    if n == 0:
        return {
            'avg_20d_return': 0.0,
            'avg_60d_return': 0.0,
            'win_rate_20d': 0.0,
            'profit_loss_ratio': 0.0,
            'sharpe_20d': 0.0,
            'signal_count': 0,
        }

    returns_20d_arr = np.array(r20)
    returns_60d_arr = np.array(r60) if r60 else np.array([])

    avg_20d = float(np.mean(returns_20d_arr))
    avg_60d = float(np.mean(returns_60d_arr)) if len(r60) > 0 else 0.0
    win_rate = float((returns_20d_arr > 0).mean()) * 100

    # 盈亏比 = 平均盈利 / 平均亏损的绝对值
    wins = returns_20d_arr[returns_20d_arr > 0]
    losses = returns_20d_arr[returns_20d_arr <= 0]
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
    profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

    # Sharpe = mean / std（非年化, 20日收益的均值/标准差）
    std_20d = float(np.std(returns_20d_arr, ddof=1))
    sharpe = (avg_20d / std_20d) if std_20d > 0 and n >= 2 else 0.0

    return {
        'avg_20d_return': avg_20d,
        'avg_60d_return': avg_60d,
        'win_rate_20d': win_rate,
        'profit_loss_ratio': profit_loss_ratio,
        'sharpe_20d': sharpe,
        'signal_count': n,
    }


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 55)
    print("  参数网格搜索")
    print("=" * 55)

    # 1. 获取待验证股票列表（前 N 只）
    stocks = STOCK_POOL
    NUM_STOCKS = len(stocks)
    print(f"\n  股票池: {NUM_STOCKS} 只（来源: check_negative_news.MONITOR_LIST）")
    print(f"  权重方案: {len(weight_schemes)} 种")
    print(f"  置信度锚定: {len(confidence_anchorings)} 种")
    print(f"  评分阈值: >= {SCORE_THRESHOLD:.0f} 分")
    print(f"  总组合数: {len(weight_schemes) * len(confidence_anchorings)}")
    print()

    # 2. 运行验证（只跑一次，所有组合复用同一批信号）
    all_signals = []
    error_stocks = []

    print(f"  [1/2] 运行技术评分验证...")
    for i, (code, name) in enumerate(stocks):
        print(f"    [{i+1}/{NUM_STOCKS}] {code} {name}...", end=" ", flush=True)
        try:
            results = validate_tech_score.run_single_validation(code, name)
            if results:
                all_signals.extend(results)
                print(f"✅ {len(results)} 个信号")
            else:
                print("⚠ 无数据/无买点")
        except Exception as e:
            print(f"❌ {e}")
            error_stocks.append(code)

    print(f"\n  共获取 {len(all_signals)} 个买入信号", end="")
    if error_stocks:
        print(f"（{len(error_stocks)} 只股票失败: {', '.join(error_stocks)}）")
    else:
        print()

    if not all_signals:
        print("\n  ⚠ 无信号数据，无法执行网格搜索。请检查缓存数据。")
        return

    # 2.5 为每个信号附加历史基本面评分
    print(f"\n  [1.5/2] 计算基本面评分...")
    fund_cache_hits = 0
    for sig in all_signals:
        symbol = sig['symbol']
        date_str = sig['date']
        price = sig.get('price')  # 信号日股价，用于计算PE估值
        # 利用缓存：同一(symbol, quarter)只查一次Baostock
        sig['fund_score'] = get_fund_score(symbol, date_str, stock_price=price)
        if (symbol, get_quarter_for_date(date_str)) in _fund_score_cache:
            fund_cache_hits += 1
    print(f"    基本面评分完成（{len(_fund_score_cache)}个季度快照, {len(all_signals)}个信号）")
    fund_scores = [s['fund_score'] for s in all_signals]
    print(f"    分布: min={min(fund_scores):.0f} median={np.median(fund_scores):.0f} max={max(fund_scores):.0f}")

    # 3. 网格搜索
    print(f"\n  [2/2] 执行网格搜索（阈值 >= {SCORE_THRESHOLD:.0f} 分）...")
    grid_results = []

    for w_tech, w_fund, w_news in weight_schemes:
        for use_anchor in confidence_anchorings:
            label_anchor = "是" if use_anchor else "否"
            scheme_label = f"({w_tech:.2f},{w_fund:.2f},{w_news:.2f})"

            fwd_20d_list = []
            fwd_60d_list = []
            filtered_count = 0

            for sig in all_signals:
                # 计算该组合下的复合评分（使用实际基本面评分）
                composite = compute_composite_score(
                    sig['tech_score'], sig['fund_score'], sig['confidence'],
                    w_tech, w_fund, w_news, use_anchor
                )
                # 评分阈值过滤
                if composite < SCORE_THRESHOLD:
                    continue

                filtered_count += 1
                fwd_20d = sig.get('fwd_20d')
                fwd_60d = sig.get('fwd_60d')
                if fwd_20d is not None:
                    fwd_20d_list.append(fwd_20d)
                    fwd_60d_list.append(fwd_60d if fwd_60d is not None else np.nan)

            metrics = calc_metrics(fwd_20d_list, fwd_60d_list)
            metrics['signal_count'] = filtered_count  # 显示通过阈值筛选的数量

            grid_results.append({
                'weight_scheme': scheme_label,
                'w_tech': w_tech,
                'w_fund': w_fund,
                'w_news': w_news,
                'use_anchoring': use_anchor,
                'anchoring_label': label_anchor,
                **metrics,
            })

    # 4. 按 Sharpe 排序
    grid_results.sort(key=lambda r: r['sharpe_20d'], reverse=True)

    # 5. 输出表格
    print()
    print("=" * 90)
    print("  参数网格搜索结果（按 Sharpe 降序）")
    print("=" * 90)
    header = (f"  {'Rank':<5} {'权重方案':<18} {'锚定':<6} "
              f"{'20日收益':<10} {'60日收益':<10} {'胜率':<8} {'盈亏比':<8} "
              f"{'Sharpe':<8} {'信号':<6}")
    print(header)
    print("  " + "-" * 90)

    for rank, r in enumerate(grid_results, 1):
        avg_20_str = f"{r['avg_20d_return']:+.2f}%"
        avg_60_str = f"{r['avg_60d_return']:+.2f}%" if r['avg_60d_return'] != 0 else "N/A"
        win_str = f"{r['win_rate_20d']:.1f}%"
        plr_str = f"{r['profit_loss_ratio']:.2f}" if r['profit_loss_ratio'] != float('inf') else "∞"
        sharpe_str = f"{r['sharpe_20d']:.4f}"
        print(f"  {rank:<5} {r['weight_scheme']:<18} {r['anchoring_label']:<6} "
              f"{avg_20_str:<10} {avg_60_str:<10} {win_str:<8} {plr_str:<8} "
              f"{sharpe_str:<8} {r['signal_count']:<6}")

    # 6. 最佳组合信息
    print()
    best = grid_results[0]
    print(f"  🏆 最佳组合: 权重={best['weight_scheme']}, 锚定={best['anchoring_label']}")
    print(f"     Sharpe={best['sharpe_20d']:.4f}, 20日收益={best['avg_20d_return']:+.2f}%, "
          f"胜率={best['win_rate_20d']:.1f}%, 信号数={best['signal_count']}")

    # 7. 保存结果
    output_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "grid_search_results.json"
    )

    output_data = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'params': {
            'num_stocks': NUM_STOCKS,
            'score_threshold': SCORE_THRESHOLD,
            'weight_schemes': weight_schemes,
            'confidence_anchorings': confidence_anchorings,
        },
        'stats': {
            'total_signals': len(all_signals),
            'failed_stocks': error_stocks,
        },
        'results': [
            {
                'rank': i + 1,
                'weight_scheme': r['weight_scheme'],
                'w_tech': r['w_tech'],
                'w_fund': r['w_fund'],
                'w_news': r['w_news'],
                'use_anchoring': r['use_anchoring'],
                'avg_20d_return': round(r['avg_20d_return'], 4),
                'avg_60d_return': round(r['avg_60d_return'], 4),
                'win_rate_20d': round(r['win_rate_20d'], 2),
                'profit_loss_ratio': round(r['profit_loss_ratio'], 4) if r['profit_loss_ratio'] != float('inf') else None,
                'sharpe_20d': round(r['sharpe_20d'], 6),
                'signal_count': r['signal_count'],
            }
            for i, r in enumerate(grid_results)
        ],
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 结果已保存: {output_path}")
    print(f"  📊 共评估 {len(grid_results)} 种参数组合")
    print()


if __name__ == "__main__":
    main()
