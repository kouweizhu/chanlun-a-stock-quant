"""
slow_bull_backtest.py — 慢牛行情回测验证（2016.06-2017.11）
对 A500 中 2016 年前上市的老票批量回测，收集买点分布+收益+技术评分
"""
import json
from date_utils import date_to_str, parse_date_to_datetime
import sys
import os
import csv
from datetime import datetime, timedelta
from collections import Counter
from backtest_engine import run_single

# ============================================================
# 回测参数
# ============================================================
BACKTEST_START = "2016-06-01"
BACKTEST_END = "2017-11-30"
INITIAL_CAPITAL = 2000000.0
HS300_REFERENCE_RETURN = 26.75  # 沪深300同期涨幅 (2016.06.01→2017.11.30)

# ============================================================
# Step 1: 加载老票 + 选取50只代表票
# ============================================================
def load_old_stocks():
    with open('D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/.old_stocks_2016.json') as f:
        return json.load(f)


def pick_representative_stocks(old_stocks, n=50):
    """
    从老票中选取 n 只代表票，按行业分散
    用手工精选的 50 只，覆盖金融/消费/科技/周期/医药/制造
    确保 TIER1 老票全部包含
    """
    # TIER1 中的老票（2016年前上市）
    tier1_old = [
        '000001', '000333', '600036', '600519', '601166',
        '601318', '600900', '300059', '002475', '600030',
    ]
    
    # 各行业代表
    financial = [
        '601398', '601288', '601328', '600000', '601601',
        '000002', '601211',  # 银行+保险+券商+地产
    ]
    
    consumer = [
        '000858', '600887', '000651', '000568', '000423',
        '600690', '000895', '002714', '600104',  # 白酒+乳业+家电+猪肉+汽车
    ]
    
    energy_materials = [
        '601088', '600028', '600309', '601857', '000792',
        '601899', '600111', '601225', '603993',  # 煤炭+石油+化工+稀土+有色
    ]
    
    tech_hardware = [
        '000063', '002415', '002230', '000725', '000988',
        '002028', '002050', '600183', '002384', '600584',  # 通信+安防+AI+面板+PCB
    ]
    
    industrial = [
        '600031', '000338', '601766', '601668', '000425',
        '000157', '600660', '000100',  # 机械+建筑+汽车+玻璃
    ]
    
    healthcare = [
        '600276', '000538', '002001', '300003', '000513',  # 医药
    ]
    
    infrastructure = [
        '600050', '600406', '601728', '600150', '601816',
        '601012', '600660',  # 电信+电力+船舶+高铁+光伏
    ]
    
    # 检查是否都在 old_stocks 中
    old_codes = {s[0] for s in old_stocks}
    all_candidates = (tier1_old + financial + consumer + energy_materials +
                      tech_hardware + industrial + healthcare + infrastructure)
    
    selected = []
    seen = set()
    for code in all_candidates:
        if code in old_codes and code not in seen:
            selected.append(code)
            seen.add(code)
        if len(selected) >= n:
            break
    
    # 补到 n 只
    if len(selected) < n:
        for code, _ in old_stocks:
            if code not in seen:
                selected.append(code)
                seen.add(code)
                if len(selected) >= n:
                    break
    
    # 转成 (code, name) 格式
    name_map = dict(old_stocks)
    result = [(code, name_map.get(code, code)) for code in selected]
    
    print(f"选取 {len(result)} 只代表票:")
    for code, name in result:
        print(f"  {code} {name}")
    
    return result


# ============================================================
# Step 2: 批量回测
# ============================================================
def classify_buy_type(reason: str) -> str:
    """从买入理由中提取买点类型"""
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
    if '反转后' in reason:
        return '反转后'
    if '类二买' in reason:
        return '类二买'
    return '其他'


def run_batch_backtest(stock_list, quiet=True, tp_multiplier=1.0, sell_reduce_pct=0.0):
    """批量回测，返回详细统计"""
    results = []
    errors = []
    
    n = len(stock_list)
    parts = []
    if tp_multiplier != 1.0:
        parts.append(f"止盈×{tp_multiplier:.2f}")
    if sell_reduce_pct > 0:
        parts.append(f"卖点减{sell_reduce_pct*100:.0f}%")
    tp_label = "(" + ", ".join(parts) + ")" if parts else "(默认)"
    for i, (code, name) in enumerate(stock_list):
        print(f"\r[{i+1}/{n}] {code} {name}... {tp_label}", end='', flush=True)
        
        try:
            stats = run_single(
                code, name,
                start_date=BACKTEST_START,
                end_date=BACKTEST_END,
                capital=INITIAL_CAPITAL,
                quiet=quiet,
                tp_multiplier=tp_multiplier,
                sell_reduce_pct=sell_reduce_pct,
            )
            
            if stats:
                # 提取买点分布
                buy_types = Counter()
                if 'trades' in stats and stats['trades']:
                    for t in stats['trades']:
                        bt = classify_buy_type(t.get('reason_entry', ''))
                        buy_types[bt] += 1
                
                # 构建结果行
                result = {
                    'code': code,
                    'name': name,
                    'total_return': stats.get('total_return', 0),
                    'annual_return': stats.get('annual_return', 0),
                    'total_trades': stats.get('total_trades', 0),
                    'win_rate': stats.get('win_rate', 0),
                    'avg_win': stats.get('avg_win', 0),
                    'avg_loss': stats.get('avg_loss', 0),
                    'profit_factor': stats.get('profit_factor', 0),
                    'profit_loss_ratio': stats.get('profit_loss_ratio', 0),
                    'max_drawdown': stats.get('max_drawdown', 0),
                    'sharpe_ratio': stats.get('sharpe_ratio', 0),
                    'final_value': stats.get('final_value', 0),
                    'years': stats.get('years', 1.0),
                    'excess_return': stats.get('total_return', 0) - HS300_REFERENCE_RETURN,
                    'buy_1st': buy_types.get('一买', 0),
                    'buy_2nd': buy_types.get('二买', 0),
                    'buy_3rd': buy_types.get('三买', 0),
                    'buy_panbei': buy_types.get('类二买', 0),
                    'buy_reversal': buy_types.get('反转后', 0),
                    'buy_other': buy_types.get('其他', 0) + buy_types.get('中枢兜底', 0) + buy_types.get('未知', 0),
                }
                results.append(result)
            else:
                errors.append(f"{code} {name}: 回测无结果")
                
        except Exception as e:
            errors.append(f"{code} {name}: {str(e)[:80]}")
    
    print()  # newline after progress
    
    if errors:
        print(f"\n错误 ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
    
    return results


# ============================================================
# Step 3: 输出报告
# ============================================================
def print_summary(results):
    """打印汇总表"""
    valid = [r for r in results if r['total_trades'] > 0]
    all_stocks = results
    
    print("\n" + "=" * 110)
    print("  慢牛回测汇总 (2016.06-2017.11)")
    print("=" * 110)
    
    # 整体统计
    if valid:
        avg_ret = sum(r['total_return'] for r in valid) / len(valid)
        avg_ann = sum(r['annual_return'] for r in valid) / len(valid)
        avg_wr = sum(r['win_rate'] for r in valid) / len(valid)
        avg_sharpe = sum(r['sharpe_ratio'] for r in valid) / len(valid)
        avg_mdd = sum(r['max_drawdown'] for r in valid) / len(valid)
        
        pos_count = sum(1 for r in valid if r['total_return'] > 0)
        beat_hs300 = sum(1 for r in valid if r['total_return'] > HS300_REFERENCE_RETURN)
        
        print(f"\n  整体统计 ({len(valid)}只有交易):")
        print(f"    平均总收益:     {avg_ret:+.2f}%")
        print(f"    平均年化收益:   {avg_ann:+.2f}%")
        print(f"    平均胜率:       {avg_wr:.1f}%")
        print(f"    平均夏普:       {avg_sharpe:.2f}")
        print(f"    平均最大回撤:   {avg_mdd:.2f}%")
        print(f"    正收益:         {pos_count}/{len(valid)} ({pos_count*100/len(valid):.1f}%)")
        print(f"    跑赢沪深300:    {beat_hs300}/{len(valid)} ({beat_hs300*100/len(valid):.1f}%)")
        
        # 买点分布
        total_1st = sum(r['buy_1st'] for r in valid)
        total_2nd = sum(r['buy_2nd'] for r in valid)
        total_3rd = sum(r['buy_3rd'] for r in valid)
        total_other = sum(r['buy_other'] for r in valid) + sum(r['buy_reversal'] for r in valid) + sum(r['buy_panbei'] for r in valid)
        total_buys = total_1st + total_2nd + total_3rd + total_other
        
        print(f"\n  买点分布 (总 {total_buys} 次买入):")
        if total_buys > 0:
            print(f"    一买:           {total_1st}次 ({total_1st*100/total_buys:.1f}%)")
            print(f"    二买:           {total_2nd}次 ({total_2nd*100/total_buys:.1f}%)")
            print(f"    三买:           {total_3rd}次 ({total_3rd*100/total_buys:.1f}%)")
            print(f"    其他:           {total_other}次 ({total_other*100/total_buys:.1f}%)")
        
        # 信号充足性
        no_trade = [r for r in all_stocks if r['total_trades'] == 0]
        few_trade = [r for r in all_stocks if 0 < r['total_trades'] <= 2]
        print(f"\n  信号充足性:")
        print(f"    零交易(票荒):   {len(no_trade)}/{len(all_stocks)} ({len(no_trade)*100/len(all_stocks):.1f}%)")
        print(f"    交易≤2次:        {len(few_trade)}/{len(all_stocks)} ({len(few_trade)*100/len(all_stocks):.1f}%)")
    
    # Top 10 和 Bottom 10
    print(f"\n  {'─' * 100}")
    print(f"  Top 10 (按总收益):")
    sorted_ret = sorted(results, key=lambda r: r['total_return'], reverse=True)
    for r in sorted_ret[:10]:
        print(f"    {r['code']} {r['name']:<10} {r['total_return']:>+7.2f}%  "
              f"胜率{r['win_rate']:>5.1f}%  交易{r['total_trades']:>3}次  "
              f"一买{r['buy_1st']} 二买{r['buy_2nd']} 三买{r['buy_3rd']}")
    
    print(f"\n  Bottom 10 (按总收益):")
    for r in sorted_ret[-10:]:
        print(f"    {r['code']} {r['name']:<10} {r['total_return']:>+7.2f}%  "
              f"胜率{r['win_rate']:>5.1f}%  交易{r['total_trades']:>3}次  "
              f"一买{r['buy_1st']} 二买{r['buy_2nd']} 三买{r['buy_3rd']}")
    
    print(f"\n  {'─' * 100}")
    print(f"  零交易股票 ({len(no_trade)}只):")
    for r in no_trade[:20]:
        print(f"    {r['code']} {r['name']}")
    if len(no_trade) > 20:
        print(f"    ... 共 {len(no_trade)} 只")
    
    print("\n" + "=" * 110)


def save_csv(results, path):
    """保存详细结果到 CSV"""
    fieldnames = [
        'code', 'name', 'total_return', 'annual_return', 'excess_return',
        'total_trades', 'win_rate', 'avg_win', 'avg_loss',
        'profit_factor', 'profit_loss_ratio', 'max_drawdown', 'sharpe_ratio',
        'buy_1st', 'buy_2nd', 'buy_3rd', 'buy_panbei', 'buy_reversal', 'buy_other',
    ]
    
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\n详细结果已保存: {path}")


# ============================================================
# Main
# ============================================================
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='慢牛行情回测验证')
    parser.add_argument('--count', type=int, default=50, help='回测股票数量 (默认50)')
    parser.add_argument('--output', type=str, 
                        default='D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/slow_bull_results.csv',
                        help='输出CSV路径')
    parser.add_argument('--all', action='store_true', help='全部老票 (388只)')
    parser.add_argument('--tp-multiplier', type=float, default=1.0,
                        help='止盈倍数 (默认1.0, slow_bull实验推荐1.67)')
    parser.add_argument('--sell-reduce', type=float, default=0.0,
                        help='卖点减仓比例 (默认0=全清, slow_bull实验推荐0.5)')
    parser.add_argument('--compare', action='store_true',
                        help='对比模式：跑三组 (默认 / 止盈放宽 / 止盈+卖点降级)')
    args = parser.parse_args()
    
    print("=" * 60)
    print(f"  慢牛行情回测验证")
    print(f"  回测区间: {BACKTEST_START} ~ {BACKTEST_END}")
    print(f"  基准: 沪深300 +{HS300_REFERENCE_RETURN}%")
    print("=" * 60)
    
    # 加载老票
    old_stocks = load_old_stocks()
    print(f"\nA500中2016年前上市: {len(old_stocks)} 只")
    
    # 选股
    if args.all:
        stock_list = old_stocks
        print(f"\n使用全部 {len(stock_list)} 只老票")
    else:
        stock_list = pick_representative_stocks(old_stocks, args.count)
    
    # 回测
    if args.compare:
        # 三组对比模式
        print(f"\n{'='*60}")
        print(f"  对比模式：默认 → 止盈放宽 → 止盈+卖点降级")
        print(f"{'='*60}")
        
        groups = [
            ("A-基准", 1.0, 0.0, "默认止盈+全清"),
            ("B-止盈放宽", 1.67, 0.0, "三买15%→25%"),
            ("C-止盈+卖点降级", 1.67, 0.5, "止盈放宽+卖点只减半仓"),
        ]
        
        all_results = {}
        for label, tp, sr, desc in groups:
            print(f"\n--- {label}: {desc} ---")
            results = run_batch_backtest(stock_list, quiet=True, tp_multiplier=tp, sell_reduce_pct=sr)
            all_results[label] = {r['code']: r for r in results if r}
        
        # 对比分析
        print(f"\n{'='*90}")
        print(f"  三组对比结果")
        print(f"{'='*90}")
        
        common = set.intersection(*[set(d.keys()) for d in all_results.values()])
        n = len(common)
        
        print(f"\n  {'组别':<20} {'平均收益':<12} {'跑赢HS300':<12} {'交易次数':<10} {'胜率':<10} {'最高':<10}")
        print(f"  {'─'*70}")
        
        for label, tp, sr, desc in groups:
            d = all_results[label]
            avg_ret = sum(d[c]['total_return'] for c in common) / n
            beat = sum(1 for c in common if d[c]['total_return'] > HS300_REFERENCE_RETURN)
            avg_trades = sum(d[c]['total_trades'] for c in common) / n
            avg_wr = sum(d[c]['win_rate'] for c in common) / n
            best_ret = max(d[c]['total_return'] for c in common)
            print(f"  {label:<20} {avg_ret:>+7.2f}%   {beat:>3}/{n:<3}      {avg_trades:>4.1f}次   {avg_wr:>5.1f}%   {best_ret:>+7.2f}%")
        
        # 各组 vs 基准的改善
        base_d = all_results["A-基准"]
        for label, tp, sr, desc in groups[1:]:
            d = all_results[label]
            better = sum(1 for c in common if d[c]['total_return'] > base_d[c]['total_return'])
            worse = sum(1 for c in common if d[c]['total_return'] < base_d[c]['total_return'])
            avg_diff = sum(d[c]['total_return'] - base_d[c]['total_return'] for c in common) / n
            print(f"\n  {label} vs 基准:")
            print(f"    收益改善: {better}/{n}, 下降: {worse}/{n}")
            print(f"    平均收益差: {avg_diff:+.2f}%")
            
            # Top 5 改善
            diffs = [(c, d[c]['total_return'] - base_d[c]['total_return']) for c in common]
            diffs.sort(key=lambda x: x[1], reverse=True)
            print(f"    改善 Top 5:")
            for code, diff in diffs[:5]:
                print(f"      {code} {d[code]['name']:<10} {base_d[code]['total_return']:>+6.2f}% → {d[code]['total_return']:>+6.2f}%  ({diff:+.2f}%)")
        
        # 保存各组
        for label, tp, sr, desc in groups:
            suffix = {'A-基准': 'baseline', 'B-止盈放宽': 'tp167', 'C-止盈+卖点降级': 'tp167_sell50'}
            results_list = [all_results[label][c] for c in all_results[label]]
            save_csv(results_list, args.output.replace('.csv', f'_{suffix[label]}.csv'))
            
    else:
        # 单模式
        print(f"\n止盈倍数: {args.tp_multiplier}, 卖点减仓: {args.sell_reduce_pct}")
        print(f"\n开始批量回测...")
        results = run_batch_backtest(stock_list, quiet=True, 
                                     tp_multiplier=args.tp_multiplier,
                                     sell_reduce_pct=args.sell_reduce_pct)
        
        # 输出
        print_summary(results)
        save_csv(results, args.output)
    
    print("\n完毕。")
