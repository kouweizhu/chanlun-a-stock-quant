"""
multi_stock_scanner.py
缠论多股一键扫描 — 强共振标的筛选

功能：
1. 对股票池内所有个股运行日线+30分钟缠论分析
2. 筛选满足强共振条件的标的（日线买点 + 30分钟确认 + 高置信度）
3. 输出排序后的汇总表格
4. 支持自定义股票池

使用：
    python multi_stock_scanner.py                          # 扫描默认股票池
    python multi_stock_scanner.py 600519 000858 002415     # 扫描指定股票
    python multi_stock_scanner.py --pool watchlist.txt     # 从文件读取股票池
"""

import sys
from date_utils import date_to_str, parse_date_to_datetime
import os
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from data_manager import DataManager
from generate_analysis import RecursiveTimingSystem
from trading_strategy import FullTradingSystem

# Baostock 登录锁（bs.login/logout 非线程安全，多线程需串行化）
# v5.4.1(M-1): 弃独立 Lock 改用全局 BS_SESSION_LOCK(RLock)——自立山头的
# 锁与本模块内直接 bs.* 调用互不感知，正是审计点名的"孤儿锁漂移"。
from baostock_utils import BS_SESSION_LOCK as _BS_LOCK
_ORIG_LOGIN = DataManager._login_baostock

def _threadsafe_login(self, force=False):
    with _BS_LOCK:
        return _ORIG_LOGIN(self, force=force)

DataManager._login_baostock = _threadsafe_login

# ============================================================
# 默认股票池（含参考价，从公用模块导入）
# ============================================================
from stock_pool import DEFAULT_POOL_WITH_REF as DEFAULT_POOL

DEFAULT_POOL = list(dict.fromkeys(DEFAULT_POOL))  # 去重




class StockScanner:
    def __init__(self):
        self.dm = DataManager()
        self.results = []
        self._results_lock = threading.Lock()
        self._progress_lock = threading.Lock()

    def scan(self, pool: List[Tuple[str, str, float]] = None,
             min_confidence: int = 3, start_date: str = "2020-01-01",
             max_workers: int = 3):
        """
        扫描股票池，筛选强共振标的
        
        参数:
            pool: [(code, name, ref_price), ...]
            min_confidence: 最低置信度过滤 (默认3)
            start_date: 数据起始日期
            max_workers: 并行线程数 (默认3，避免API限流；设为1退化为串行)
        """
        if pool is None:
            pool = DEFAULT_POOL
        
        total = len(pool)
        print(f"\n{'='*70}")
        print(f"  缠论多股扫描 — 强共振标的筛选")
        print(f"  股票池: {total}只 | 最低置信度: {min_confidence}/5 | 并行: {max_workers}线程")
        print(f"  数据起始: {start_date}")
        print(f"{'='*70}\n")
        
        if max_workers <= 1 or total <= 3:
            # 串行模式（小池或单线程）
            for idx, (code, name, ref_price) in enumerate(pool, 1):
                print(f"  [{idx}/{total}] 扫描 {name}({code})...", end=" ", flush=True)
                try:
                    result = self._scan_single(code, name, ref_price, start_date, dm=self.dm)
                    self.results.append(result)
                    if result['status'] == 'OK':
                        print(f"✓ ({len(result['signals'])}个信号)")
                    else:
                        print(f"⚠ {result['message']}")
                except Exception as e:
                    print(f"✗ 异常: {str(e)[:50]}")
                    self.results.append({
                        'code': code, 'name': name,
                        'status': 'ERROR', 'message': str(e)[:80],
                        'signals': [], 'position': 'unknown'
                    })
        else:
            # 并行模式：每个线程独立 DataManager，避免 Baostock 连接冲突
            completed = 0
            
            def _scan_worker(code, name, ref_price):
                """线程工作函数：独立DM"""
                dm = DataManager()
                return self._scan_single(code, name, ref_price, start_date, dm=dm)
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_scan_worker, code, name, ref_price): (idx, code, name)
                    for idx, (code, name, ref_price) in enumerate(pool, 1)
                }
                for future in as_completed(futures):
                    idx, code, name = futures[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        result = {
                            'code': code, 'name': name,
                            'status': 'ERROR', 'message': str(e)[:80],
                            'signals': [], 'position': 'unknown'
                        }
                    
                    with self._results_lock:
                        self.results.append(result)
                        completed += 1
                    
                    with self._progress_lock:
                        if result['status'] == 'OK':
                            print(f"  [{completed}/{total}] {name}({code}) ✓ ({len(result['signals'])}个信号)")
                        else:
                            print(f"  [{completed}/{total}] {name}({code}) ⚠ {result['message']}")
        
        # 打印汇总
        self._print_summary()
        return self.results

    def _scan_single(self, code: str, name: str, ref_price: float,
                     start_date: str, dm=None) -> Dict:
        """扫描单只股票（dm: 可选独立DataManager，并行时每线程独立）"""
        dm = dm or self.dm
        rec_sys = RecursiveTimingSystem(dm)
        daily_analyzer = rec_sys.run_full_analysis(code, reference_price=ref_price,
                                                    start_date=start_date)
        m30_analyzer = rec_sys.analyses.get('30min')
        
        if not daily_analyzer:
            return {
                'code': code, 'name': name, 'status': 'FAIL',
                'message': '分析器创建失败', 'signals': [],
                'position': 'unknown'
            }
        
        # 运行交易策略（传入已构建的分析器，避免重复API调用）
        trading_sys = FullTradingSystem(dm)
        signal = trading_sys.execute_for_stock(code, reference_price=ref_price,
                                                daily_analyzer=daily_analyzer,
                                                m30_analyzer=m30_analyzer)
        
        # 收集带置信度的买卖点
        signals = []
        high_confidence_count = 0
        latest_buy = None
        latest_sell = None
        
        for point in daily_analyzer.buy_sell_points:
            ml = getattr(point, 'multilevel_confirmation', {})
            conf = ml.get('confidence_score', 0)
            m30 = ml.get('m30_confirmation', False)
            hc = ml.get('high_confidence', False)
            conf_type = ml.get('confirmation_type', 'none')
            
            sig = {
                'type': point.type,
                'level': point.level,
                'date': point.date,
                'price': point.price,
                'reason': point.reason,
                'confidence': conf,
                'm30_confirmed': m30,
                'high_confidence': hc,
                'confirmation_type': conf_type
            }
            signals.append(sig)
            
            if hc:
                high_confidence_count += 1
            if point.type == 'buy' and (not latest_buy or point.date > latest_buy['date']):
                latest_buy = sig
            if point.type == 'sell' and (not latest_sell or point.date > latest_sell['date']):
                latest_sell = sig
        
        # 当前价格
        current_price = daily_analyzer.klines[-1].close if daily_analyzer.klines else 0
        latest_date = daily_analyzer.klines[-1].date if daily_analyzer.klines else ""
        
        # 当前交易建议
        action = signal.action if signal else 'HOLD'
        position_pct = signal.position_size if signal else 0
        
        return {
            'code': code, 'name': name,
            'status': 'OK',
            'current_price': current_price,
            'latest_date': latest_date,
            'action': action,
            'position_pct': position_pct,
            'entry_price': signal.entry_price if signal else 0,
            'stop_loss': signal.stop_loss if signal else 0,
            'take_profit': signal.take_profit if signal else 0,
            'reason': signal.reason if signal else '',
            'urgency': signal.urgency if signal else 'LOW',
            'signals': signals,
            'total_signals': len(signals),
            'buy_count': sum(1 for s in signals if s['type'] == 'buy'),
            'sell_count': sum(1 for s in signals if s['type'] == 'sell'),
            'high_confidence_count': high_confidence_count,
            'latest_buy': latest_buy,
            'latest_sell': latest_sell,
            'message': f"{len(signals)}个信号, {high_confidence_count}个高置信度"
        }

    def _print_summary(self):
        """打印扫描汇总表"""
        ok_results = [r for r in self.results if r['status'] == 'OK']
        fail_results = [r for r in self.results if r['status'] != 'OK']
        
        print(f"\n{'='*70}")
        print(f"  扫描汇总")
        print(f"  {'='*70}")
        
        if ok_results:
            # 排序：优先级高的排前面
            def sort_key(r):
                urgency_map = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
                # BUY信号排最前面
                action_priority = {'BUY': 0, 'SELL': 1, 'HOLD': 2}
                return (action_priority.get(r['action'], 2),
                        urgency_map.get(r['urgency'], 2),
                        -r['high_confidence_count'])
            
            ok_results.sort(key=sort_key)
            
            # 严重信号标的（BUY/SELL）
            print(f"\n  📌 交易建议:")
            print(f"  {'代码':<8} {'名称':<10} {'信号':<8} {'价格':<10} {'止损':<10} {'止盈':<10} {'置信':<6}")
            print(f"  {'─'*62}")
            for r in ok_results:
                if r['action'] in ('BUY', 'SELL'):
                    conf_str = f"{r['high_confidence_count']}高" if r['high_confidence_count'] > 0 else "—"
                    action_str = f"🟢买入" if r['action'] == 'BUY' else f"🔴卖出"
                    sl_str = f"¥{r['stop_loss']:<7.2f}" if r['stop_loss'] > 0 else "N/A     "
                    tp_str = f"¥{r['take_profit']:<7.2f}" if r['take_profit'] > 0 else "N/A     "
                    print(f"  {r['code']:<8} {r['name']:<10} {action_str:<8} ¥{r['current_price']:<7.2f} {sl_str} {tp_str} {conf_str:<4}")
                    print(f"  {'':<18}入场¥{r['entry_price']:<7.2f}  {r['urgency']:<10} 理由:{r['reason'][:30]}")
            
            # 无信号标的
            hold_results = [r for r in ok_results if r['action'] == 'HOLD']
            if hold_results:
                print(f"\n  ⏸️  持有/无建议标的 ({len(hold_results)}只):")
                for r in hold_results:
                    print(f"     {r['code']:<8} {r['name']:<10} 信号数:{r['total_signals']:<3} "
                          f"价格:¥{r['current_price']:<7.2f} 最新:{r['latest_date']}")
        else:
            print("  ⚠ 无成功扫描的标的")
        
        # 强共振筛选
        resonance = [r for r in ok_results 
                     if r['action'] == 'BUY' and r['high_confidence_count'] > 0]
        if resonance:
            print(f"\n  ✨ 强共振标的 ({len(resonance)}只):")
            for r in resonance:
                print(f"     {r['code']:<8} {r['name']:<10} 入场¥{r['entry_price']:.2f} 止损¥{r['stop_loss']:.2f}")
        else:
            print(f"\n  ✨ 强共振标的: 无")
        
        if fail_results:
            print(f"\n  ⚠ 扫描失败 ({len(fail_results)}只):")
            for r in fail_results:
                print(f"     {r['code']:<8} {r['name']:<10} {r['message']}")
        
        print(f"\n  {'='*70}")
        print(f"  Summary: {len(ok_results)} OK, {len(fail_results)} FAIL")
        print(f"  {'='*70}\n")


def read_pool_from_file(filepath: str) -> List[Tuple[str, str, float]]:
    """从文件读取股票池
    格式: 每行 代码,名称[,参考价]
    """
    pool = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split(',')]
            code = parts[0]
            name = parts[1] if len(parts) > 1 else code
            ref_price = float(parts[2]) if len(parts) > 2 else None
            pool.append((code, name, ref_price))
    return pool


if __name__ == '__main__':
    args = sys.argv[1:]
    
    if not args:
        # 扫默认池
        scanner = StockScanner()
        scanner.scan()
    
    elif args[0] == '--pool' and len(args) >= 2:
        # 从文件读取
        pool = read_pool_from_file(args[1])
        scanner = StockScanner()
        scanner.scan(pool)
    
    else:
        # 命令行指定的个股
        pool = []
        for sym in args[:20]:  # 最多20只
            pool.append((sym, sym, None))
        scanner = StockScanner()
        scanner.scan(pool)
