#!/usr/bin/env python
"""
沪深300月线缠论分析HTML报告生成器
方案：日线数据 → 按月重采样合成月线 → 缠论分析 → HTML报告
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer, HTMLVisualizer
import pandas as pd

def resample_daily_to_monthly(df):
    """将日线DataFrame重采样为月线"""
    if df.empty:
        return []
    
    # 确保date列为datetime类型
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    
    # 按月重采样
    monthly = df.resample('M').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'amount': 'sum',
        'code': 'first'
    }).dropna()
    
    # 重置索引，转回JSON列表格式
    monthly.reset_index(inplace=True)
    monthly['date'] = monthly['date'].dt.strftime('%Y-%m-%d')
    
    return monthly.to_dict('records')

def main():
    symbol = '000300'
    name = '沪深300'
    start_date = '2019-01-01'
    
    print(f'[{name}月线] 开始：日线→月线合成')
    dm = DataManager()
    
    # 1. 获取日线数据（数据源稳定）
    print('[日线] 获取日线数据...')
    df_daily = dm.get_klines(symbol, level='daily', start_date=start_date)
    
    if df_daily.empty:
        print('❌ 日线数据获取失败')
        return
    
    print(f'[日线] 获取{len(df_daily)}根日K线')
    
    # 2. 合成月线
    print('[月线] 重采样合成月线...')
    monthly_klines = resample_daily_to_monthly(df_daily)
    print(f'[月线] 合成{len(monthly_klines)}根月K线')
    
    if not monthly_klines:
        print('❌ 月线合成失败')
        return
    
    # 3. 笔中枢分析（月线级别）
    print('[月线] 运行缠论分析...')
    analyzer = ChanLunAnalyzer(level='monthly')
    analyzer.analyze(monthly_klines)
    
    # 4. 生成HTML报告
    # v5.4(B-20): 文件名日期硬编码动态化——旧实现每次运行都覆盖写
    # '2026-05-08_沪深300月线缠论分析.html'，历史月度快照被静默顶掉
    from datetime import datetime as _dt
    output_path = os.path.join(
        'D:/常用文件/缠论分析',
        f"{_dt.now().strftime('%Y-%m-%d')}_沪深300月线缠论分析.html")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    print('[月线] 生成HTML报告...')
    viz = HTMLVisualizer(
        symbol=symbol,
        name=name,
        analyzer=analyzer
    )
    viz.generate_html(output_path)
    
    print(f'✅ 报告已保存: {output_path}')
    
    # 5. 打印关键结论（与market_regime.py对齐）
    print('\n--- 月线分析结论 ---')
    if analyzer.bis:
        last_bi = analyzer.bis[-1]
        print(f'末笔方向: {last_bi.direction}')
        print(f'末笔区间: {last_bi.start_date}→{last_bi.end_date}')
        print(f'末笔价格: {last_bi.start_price:.2f}→{last_bi.end_price:.2f}')
    else:
        print('笔: 无')
        
    print(f'笔总数: {len(analyzer.bis)}')
    print(f'中枢总数: {len(analyzer.zhongshus)}')
    
    if analyzer.zhongshus:
        last_zs = analyzer.zhongshus[-1]
        print(f'最后中枢: {last_zs.start_date}~{last_zs.end_date}')
        print(f'  ZG={last_zs.zg:.2f}  ZD={last_zs.zd:.2f}')
    
    if analyzer.buy_sell_points:
        last_bsp = analyzer.buy_sell_points[-1]
        print(f'最后信号: {last_bsp.type}点 L{last_bsp.level} @{last_bsp.date} {last_bsp.price:.2f}')
        if hasattr(last_bsp, 'multilevel_confirmation'):
            conf = last_bsp.multilevel_confirmation
            print(f'  置信度: {conf["confidence_score"]}/5 ({conf["confirmation_type"]})')
    else:
        print('买卖点: 无')

if __name__ == '__main__':
    main()
