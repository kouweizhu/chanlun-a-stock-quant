"""
run_backtest.py — 批量回测入口
用法：python run_backtest.py [股票代码...] [选项]

示例：
  python run_backtest.py 301498                          # 单只
  python run_backtest.py 301498 600519 688981            # 多只
  python run_backtest.py 301498 --ref 301498 58.6        # 指定参考价
  python run_backtest.py --pool                           # 扫描默认股票池
  python run_backtest.py --help                           # 帮助

选项：
  --ref SYM PRICE   指定某只股票的参考价
  --start YYYY-MM-DD  回测起始日期（默认 2024-01-01）
  --end YYYY-MM-DD   回测截止日期（默认 数据最新日）
  --pool            使用默认股票池回测
  --capital NUM     每只初始资金（默认 2000000）
  --quiet           精简输出
"""

import sys
from date_utils import date_to_str, parse_date_to_datetime
from backtest_engine import run_single, print_batch_summary
from stock_pool import DEFAULT_POOL


def parse_args():
    symbols = []
    ref_prices = {}
    start_date = "2024-01-01"
    end_date = None
    capital = 2000000.0
    quiet = False
    
    args = sys.argv[1:]
    
    if not args or '--help' in args or '-h' in args:
        print(__doc__)
        sys.exit(0)
    
    i = 0
    while i < len(args):
        if args[i] == '--pool':
            symbols.extend(DEFAULT_POOL)
            i += 1
        elif args[i] == '--ref' and i + 2 < len(args):
            try:
                ref_prices[args[i + 1]] = float(args[i + 2])
            except ValueError:
                print(f"[Error] 无效参考价: {args[i+1]} {args[i+2]}")
                sys.exit(1)
            i += 3
        elif args[i] == '--start' and i + 1 < len(args):
            start_date = args[i + 1]
            i += 2
        elif args[i] == '--end' and i + 1 < len(args):
            end_date = args[i + 1]
            i += 2
        elif args[i] == '--capital' and i + 1 < len(args):
            try:
                capital = float(args[i + 1])
            except ValueError:
                print(f"[Error] 无效初始资金: {args[i+1]}")
                sys.exit(1)
            i += 2
        elif args[i] == '--quiet':
            quiet = True
            i += 1
        elif args[i].startswith('--'):
            print(f"[Error] 未知选项: {args[i]}")
            sys.exit(1)
        else:
            symbols.append((args[i], args[i]))
            i += 1
    
    return symbols, ref_prices, start_date, end_date, capital, quiet


def main():
    symbols, ref_prices, start_date, end_date, capital, quiet = parse_args()
    
    # 默认参考价
    ref_prices.setdefault('301498', 58.6)
    
    if not quiet:
        end_info = f", 截止日期: {end_date}" if end_date else ""
        print(f"批量回测启动 — {len(symbols)} 只股票")
        print(f"起始日期: {start_date}{end_info}, 初始资金: ¥{capital:,.0f}/只")
    
    results = []
    for sym, name in symbols:
        ref = ref_prices.get(sym)
        stats = run_single(sym, name, ref_price=ref, start_date=start_date,
                           end_date=end_date,
                           capital=capital, quiet=quiet)
        if stats:
            results.append(stats)
    
    if len(results) > 1:
        print_batch_summary(results)
    
    print("\n完毕。")


if __name__ == '__main__':
    main()
