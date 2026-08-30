"""report_generator.py — 缠论日线分析报告生成器

生成HTML可视化报告 + Excel汇总表（指令单、买卖点、回测统计、结构数据）

用法：
  python report_generator.py [股票代码] [--ref 参考价] [--start 开始日期] [--end 结束日期]

输出文件（个股代码子目录）：
  {代码}_chanlun_analysis.html  — 交互式HTML可视化报告
  {代码}_chanlun_report.xlsx    — Excel汇总表

示例：
  python report_generator.py 301498 --ref 58.6
  python report_generator.py 600519 --start 2024-04-01 --end 2026-04-23
"""

from data_manager import DataManager
from date_utils import date_to_str, parse_date_to_datetime
from generate_analysis import ChanLunAnalyzer, RecursiveTimingSystem, HTMLVisualizer
from trading_strategy import FullTradingSystem, TradingStrategy
from excel_report import generate_excel_report
from backtest_engine import run_single
import sys
import os
from datetime import datetime, timedelta


def parse_args():
    """解析命令行参数"""
    args = sys.argv[1:]
    
    symbol = '301498'
    name = '乖宝宠物'
    reference_price = None
    start_date = None
    end_date = None
    
    # 第一个非--参数是股票代码
    positional = [a for a in args if not a.startswith('--')]
    if positional:
        symbol = positional[0]
        name = symbol
    
    if '--ref' in args:
        idx = args.index('--ref')
        if idx + 1 < len(args):
            try:
                reference_price = float(args[idx + 1])
            except ValueError:
                print(f"[Error] 无效参考价: {args[idx+1]}")
                sys.exit(1)
    
    if '--start' in args:
        idx = args.index('--start')
        if idx + 1 < len(args):
            start_date = args[idx + 1]
    
    if '--end' in args:
        idx = args.index('--end')
        if idx + 1 < len(args):
            end_date = args[idx + 1]
    
    return symbol, name, reference_price, start_date, end_date


def main():
    symbol, name, reference_price, start_date, end_date = parse_args()
    
    dm = DataManager()
    
    # 创建个股专属子目录
    base_dir = os.path.dirname(os.path.abspath(__file__))
    stock_dir = os.path.join(base_dir, symbol)
    os.makedirs(stock_dir, exist_ok=True)
    
    date_range_str = ""
    if start_date:
        date_range_str += f" 开始={start_date}"
    if end_date:
        date_range_str += f" 结束={end_date}"
    
    # 计算分析起始日：交易开始日期往前5年（用于缠论结构识别）
    # 但分析数据总跨度不超过5年
    analysis_start = None
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            analysis_start_dt = start_dt - timedelta(days=5*365)
            analysis_start = analysis_start_dt.strftime("%Y-%m-%d")
            print(f"[Config] 分析数据从 {analysis_start} 起（加载5年历史用于结构识别）")
            print(f"[Config] 模拟交易从 {start_date} 起（此前数据仅用于结构识别，不交易）")
        except ValueError:
            analysis_start = None
            print(f"[Config] 分析数据从 {start_date} 起（start_date格式无法计算-5年）")
    else:
        print("[Config] 使用默认数据范围（未指定--start）")
    
    print(f"--- {name}({symbol}) 日线缠论分析报告 ---{date_range_str}")
    print("[Data] 数据源: Baostock(主) → efinance(备) → AkShare(兜底)")
    print(f"[Output] 文件输出至: {stock_dir}")
    
    try:
        # ========== 1. 多级别缠论分析（仅2次API调用） ==========
        # 使用 analysis_start 加载更早的历史数据用于结构识别
        rec_sys = RecursiveTimingSystem(dm)
        daily_analyzer = rec_sys.run_full_analysis(
            symbol, 
            reference_price=reference_price,
            start_date=analysis_start or start_date,
            end_date=end_date
        )
        
        # 输出日线买卖点信号
        buy_points = [p for p in daily_analyzer.buy_sell_points if p.type == 'buy']
        sell_points = [p for p in daily_analyzer.buy_sell_points if p.type == 'sell']
        
        print('\n' + '='*30)
        print('       日线买卖点信号')
        print('='*30)
        if buy_points:
            latest_buy = buy_points[-1]
            print(f'最新买点: {latest_buy.date} ¥{latest_buy.price:.2f} ({latest_buy.reason})')
        else:
            print('最新买点: 无')
        if sell_points:
            latest_sell = sell_points[-1]
            print(f'最新卖点: {latest_sell.date} ¥{latest_sell.price:.2f} ({latest_sell.reason})')
        else:
            print('最新卖点: 无')
        print(f'买点总数: {len(buy_points)}')
        print(f'卖点总数: {len(sell_points)}')
        print('='*30)
        
        # 输出多级别确认信息
        print('\n' + '='*30)
        print('       多级别确认信号')
        print('='*30)
        for point in daily_analyzer.buy_sell_points:
            if hasattr(point, 'multilevel_confirmation'):
                ml = point.multilevel_confirmation
                conf = ml.get('confidence_score', 0)
                high = '⭐' if ml.get('high_confidence') else ''
                m30 = '✓' if ml.get('m30_confirmation') else '✗'
                conf_type = ml.get('confirmation_type', 'none')
                type_tag = {'direct': '直', 'divergence': '背', 'none': '无'}.get(conf_type, '?')
                print(f'{point.type.upper()}{point.level} {point.date} ¥{point.price:.2f} | 置信度: {conf}/5 (30min:{m30}|{type_tag}) {high}')
            else:
                print(f'{point.type.upper()}{point.level} {point.date} ¥{point.price:.2f} | 无多级别确认')
        print('='*30)
        
        # ========== 2. 交易策略执行 → 指令单 ==========
        print('\n[Strategy] 正在生成交易信号...')
        trading_sys = FullTradingSystem(dm)
        signal = trading_sys.execute_for_stock(symbol, reference_price=reference_price,
                                               daily_analyzer=daily_analyzer)
        trading_sys.print_signal_report(symbol, name, signal)
        
        # ========== 3. 生成可视化 HTML 报告 ==========
        print('[Generating] 正在生成可视化 HTML 报告...')
        
        m30_analyzer = rec_sys.analyses.get('30min')
        
        visualizer = HTMLVisualizer(
            symbol, name, daily_analyzer, 
            reference_price=reference_price,
            trade_signal=signal,
            m30_analyzer=m30_analyzer
        )
        output_path = os.path.join(stock_dir, f'{symbol}_chanlun_analysis.html')
        visualizer.generate_html(output_path)
        print(f'[Success] 报告已生成：{output_path}')
        print(f'[Tip] 在浏览器中打开查看交互式图表')
        
        # ========== 4. 生成 Excel 报告 ==========
        print('[Generating] 正在运行回测计算统计指标...')
        try:
            backtest_start = start_date if start_date else "2024-01-01"
            # 复用已加载的数据和分析器，避免重复API调用和重复结构分析
            daily_data = dm.get_klines(symbol, 'daily', start_date=analysis_start or start_date, end_date=end_date)
            m30_data = dm.get_klines(symbol, '30min', start_date=analysis_start or start_date, end_date=end_date)
            daily_analyzer_reuse = rec_sys.analyses.get('daily', daily_analyzer)
            m30_analyzer_reuse = rec_sys.analyses.get('30min')
            backtest_stats = run_single(
                symbol, name, 
                ref_price=reference_price, 
                start_date=backtest_start,
                end_date=end_date,
                analysis_start_date=analysis_start,
                quiet=True,
                daily_data=daily_data, m30_data=m30_data,
                daily_analyzer=daily_analyzer_reuse, m30_analyzer=m30_analyzer_reuse
            )
            print('[Generating] 正在生成 Excel 汇总表...')
        except Exception as e:
            print(f'[Warning] 回测运行异常（不影响主流程）: {e}')
            backtest_stats = None
        
        try:
            excel_path = generate_excel_report(
                symbol, name, daily_analyzer,
                signal=signal,
                backtest_stats=backtest_stats,
                reference_price=reference_price,
                output_dir=stock_dir,
            )
            print(f'[Success] Excel汇总表已生成：{excel_path}')
        except Exception as e:
            print(f'[Warning] Excel生成失败: {e}')
            import traceback
            traceback.print_exc()
        
        # ========== 5. 关键点位摘要 ==========
        print('\n' + '='*30)
        print('       关键点位摘要')
        print('='*30)
        if daily_analyzer.zhongshus:
            zs = daily_analyzer.zhongshus[-1]
            print(f'最新中枢: ¥{zs.zd:.2f} - ¥{zs.zg:.2f}')
        if daily_analyzer.bis:
            bi = daily_analyzer.bis[-1]
            print(f'最新笔: {"上升" if bi.direction=="up" else "下降"} ¥{bi.start_price:.2f} → ¥{bi.end_price:.2f}')
        if daily_analyzer.klines:
            latest = daily_analyzer.klines[-1]
            print(f'当前价格: ¥{latest.close:.2f} ({latest.date})')
        print('='*30)
        
    except Exception as e:
        print(f'\n[Critical Error] 运行中出现异常：{e}')
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
