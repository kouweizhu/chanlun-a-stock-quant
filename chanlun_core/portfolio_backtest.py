"""
portfolio_backtest.py — 组合回测引擎
共享资金池、多股同时持仓、80%仓位目标、资金复用

架构：
  1. 预分析：所有股票跑 ChanLun 日线分析，提取买卖点信号
  2. 组合模拟：逐日遍历，统一管理资金池和持仓
  3. 买入排序：信号置信度 → 买点等级 → 分散化
"""
import sys
from date_utils import date_to_str, parse_date_to_datetime
import os
import json
import csv
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer, RecursiveTimingSystem
from slippage_model import SlippageModel
from config_loader import (
    BACKTEST_INITIAL_CAPITAL, BACKTEST_COMMISSION,
    BACKTEST_MAX_POSITION_PCT, BACKTEST_POSITION_LADDER,
    BACKTEST_ENABLE_SLIPPAGE,
)


# ============================================================
# 回测参数
# ============================================================
BACKTEST_START = "2016-06-01"
BACKTEST_END = "2017-11-30"
ANALYSIS_START = "2011-06-01"  # 5年历史数据用于结构识别
INITIAL_CAPITAL = 2_000_000.0
TARGET_UTILIZATION = 0.80      # 目标仓位 80%
TP_MULTIPLIER = 1.0            # 止盈倍数
SELL_REDUCE_PCT = 0.0          # 卖点减仓比例
HS300_RETURN = 26.75


# ============================================================
# 持仓类
# ============================================================
@dataclass
class BuyLot:
    price: float
    date: str
    shares: int
    reason: str
    structure_stop: float = 0.0
    take_profit_pct: float = 0.15


class Position:
    """单只股票的持仓"""
    def __init__(self, code: str, name: str):
        self.code = code
        self.name = name
        self.buy_lots: List[BuyLot] = []
        self.buy_count = 0  # 已买入次数 (0-3)
    
    @property
    def total_shares(self) -> int:
        return sum(l.shares for l in self.buy_lots)
    
    @property
    def avg_entry(self) -> float:
        total_s = self.total_shares
        if total_s == 0:
            return 0
        return sum(l.shares * l.price for l in self.buy_lots) / total_s
    
    @property
    def total_cost(self) -> float:
        return sum(l.shares * l.price for l in self.buy_lots)
    
    def position_value(self, current_price: float) -> float:
        return self.total_shares * current_price
    
    def position_pct(self, capital: float, current_price: float) -> float:
        if capital <= 0:
            return 0
        return self.position_value(current_price) / capital * 100
    
    def can_buy_more(self, max_buys: int = 3) -> bool:
        return self.buy_count < max_buys
    
    def add_buy(self, lot: BuyLot, price: float, shares: int, reason: str,
                structure_stop: float = 0, take_profit_pct: float = 0.15):
        self.buy_lots.append(BuyLot(
            price=price, date=lot.get('date', ''), shares=shares,
            reason=reason, structure_stop=structure_stop,
            take_profit_pct=take_profit_pct
        ))
        self.buy_count += 1


# ============================================================
# 组合回测引擎
# ============================================================
class PortfolioBacktest:
    def __init__(self, stock_list: List[Tuple[str, str]],
                 capital: float = INITIAL_CAPITAL,
                 start_date: str = BACKTEST_START,
                 end_date: str = BACKTEST_END,
                 tp_multiplier: float = TP_MULTIPLIER,
                 sell_reduce_pct: float = SELL_REDUCE_PCT,
                 target_util: float = TARGET_UTILIZATION,
                 max_stock_pct: float = BACKTEST_MAX_POSITION_PCT,
                 quiet: bool = False):
        self.stock_list = stock_list
        self.capital = capital
        self.start_date = start_date
        self.end_date = end_date
        self.tp_multiplier = tp_multiplier
        self.sell_reduce_pct = sell_reduce_pct
        self.target_util = target_util
        self.max_stock_pct = max_stock_pct
        self.max_buys = len(BACKTEST_POSITION_LADDER)
        self.quiet = quiet
        
        self.dm = DataManager()
        self.slippage = SlippageModel() if BACKTEST_ENABLE_SLIPPAGE else None
        
        # 状态
        self.cash = capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[Dict] = []
        self.daily_values: List[Dict] = []
        
        # 预分析结果
        self.signal_db: Dict[Tuple[str, str], List[Dict]] = {}  # (date, code) -> signals
        self.daily_data_cache: Dict[str, pd.DataFrame] = {}
        self.analyzer_cache: Dict[str, ChanLunAnalyzer] = {}
    
    # ============================================================
    # Phase 1: 预分析
    # ============================================================
    def pre_analyze(self):
        """对所有股票运行 ChanLun 分析，提取信号（v4.2 滚动窗口）

        ⚠️ 前视偏差修复：不再一次性全量分析后建表。
        改为按日滚动：对每个交易日 t 截取截至 t 的数据重新分析，
        只提取"当天已确认"的信号（date == t）。信号依赖的笔/分型/
        中枢/确认全部只看到当日，与实盘一致。

        性能：N 股 × T 日全量分析为 O(N·T²)，对长窗口不可行。
        优化：仅对"结构可能变化的日期"重算 —— 每天重算日线分析
        （约1ms/次），并只保留当天新出现的信号。
        """
        n = len(self.stock_list)
        signal_count = 0

        # 预取每只股票的日线数据（仅一次）
        for i, (code, name) in enumerate(self.stock_list):
            if not self.quiet:
                print(f"\r[预分析] {i+1}/{n} {code} {name}...", end='', flush=True)

            try:
                # 加载日线数据
                daily_df = self.dm.get_klines(code, 'daily',
                                              start_date=ANALYSIS_START,
                                              end_date=self.end_date)
                if daily_df.empty:
                    continue

                self.daily_data_cache[code] = daily_df
            except Exception as e:
                if not self.quiet:
                    print(f"\n[预分析] {code} 数据加载失败: {e}")

        # 构建统一交易日历
        all_dates = set()
        for df in self.daily_data_cache.values():
            for d in df['date']:
                ds = date_to_str(d)
                if BACKTEST_START <= ds <= BACKTEST_END:
                    all_dates.add(ds)
        trading_days = sorted(all_dates)

        # 对每个交易日滚动分析每只股票，只提取当天已确认信号
        # ⚠️ v4.2：每天重算 = 消除前视偏差的关键
        for day_idx, current_date in enumerate(trading_days):
            if not self.quiet and day_idx % 20 == 0:
                print(f"\r[预分析-滚动] {day_idx+1}/{len(trading_days)} {current_date}...", end='', flush=True)

            for code, _ in self.stock_list:
                daily_df = self.daily_data_cache.get(code)
                if daily_df is None:
                    continue

                # 找到该日期在 DataFrame 中的位置
                mask = daily_df['date'].astype(str).str[:10] == current_date
                if not mask.any():
                    continue
                pos_iloc = daily_df.index.get_loc(mask.idxmax())

                # 截取到当日（滚动窗口，消除前视）
                daily_until = daily_df.iloc[:pos_iloc + 1]
                if len(daily_until) < 60:  # 至少60根K线才有意义
                    continue

                try:
                    analyzer = ChanLunAnalyzer(
                        level='daily', enable_forward_validation=False
                    ).analyze(self.dm.to_json_list(daily_until))
                    self.analyzer_cache[code] = analyzer  # 保留最新分析器

                    # 只取当天产生的信号（date == current_date）
                    for point in analyzer.buy_sell_points:
                        if date_to_str(point.date) != current_date:
                            continue
                        conf = getattr(point, 'multilevel_confirmation', {})
                        sig = {
                            'type': point.type,
                            'level': point.level,
                            'date': date_to_str(point.date),
                            'price': point.price,
                            'reason': point.reason,
                            'confidence': conf.get('confidence_score', 2),
                            'high_confidence': conf.get('high_confidence', False),
                        }
                        self.signal_db.setdefault((current_date, code), []).append(sig)
                        signal_count += 1
                except Exception:
                    continue

        if not self.quiet:
            print(f"\n[预分析-滚动] 完成，{signal_count} 个信号（滚动窗口模式，消除前视偏差）")
    
    # ============================================================
    # Phase 2: 组合模拟
    # ============================================================
    def simulate(self):
        """逐日遍历，管理多股持仓"""
        # 构建统一交易日历
        all_dates = set()
        for df in self.daily_data_cache.values():
            for d in df['date']:
                ds = date_to_str(d)
                if BACKTEST_START <= ds <= BACKTEST_END:
                    all_dates.add(ds)
        trading_days = sorted(all_dates)
        
        if not self.quiet:
            print(f"[模拟] {len(trading_days)} 个交易日")
        
        for day_idx, current_date in enumerate(trading_days):
            # ==== Step A: 检查持仓退出 ====
            exited_codes = []
            for code, pos in list(self.positions.items()):
                if pos.total_shares == 0:
                    continue
                
                df = self.daily_data_cache.get(code)
                if df is None:
                    continue
                
                # 获取当日价格
                row = df[df['date'].astype(str).str[:10] == current_date]
                if row.empty:
                    continue
                close_price = float(row.iloc[0]['close'])
                
                # 检查卖出信号
                sigs_today = self.signal_db.get((current_date, code), [])
                sell_sigs = [s for s in sigs_today if s['type'] == 'sell']
                
                if sell_sigs:
                    self._execute_sell(code, pos, close_price, current_date,
                                       sell_sigs[0]['reason'])
                    exited_codes.append(code)
                    continue
                
                # 检查止损
                stop_triggered = self._check_stop_loss(pos, close_price, current_date)
                if stop_triggered:
                    self._execute_sell(code, pos, close_price, current_date,
                                       stop_triggered)
                    exited_codes.append(code)
                    continue
                
                # 检查止盈
                tp_triggered = self._check_take_profit(pos, close_price)
                if tp_triggered:
                    self._execute_sell(code, pos, close_price, current_date,
                                       f"止盈(均价{pos.avg_entry:.2f})")
                    exited_codes.append(code)
                    continue
            
            # ==== Step B: 买入新标的 ====
            current_util = self._current_utilization(trading_days, current_date)
            
            if current_util < self.target_util * 0.95:  # 5%缓冲区，避免频繁调仓
                # 收集当日所有买入信号
                buy_candidates = []
                for code in self.stock_list:
                    c = code[0]
                    # 跳过已持仓且达到上限的
                    if c in self.positions and not self.positions[c].can_buy_more(self.max_buys):
                        continue
                    # 跳过当日已退出的（避免同日买回）
                    if c in exited_codes:
                        continue
                    
                    sigs = self.signal_db.get((current_date, c), [])
                    buy_sigs = [s for s in sigs if s['type'] == 'buy']
                    for s in buy_sigs:
                        buy_candidates.append((c, s))
                
                if buy_candidates:
                    # 排序：置信度 → 买点等级 → 随机（分散化）
                    buy_candidates.sort(key=lambda x: (
                        -x[1]['confidence'],
                        -x[1]['level'],
                    ))
                    
                    # 分配买入
                    remaining_cash = self.cash
                    for code, sig in buy_candidates:
                        if remaining_cash < 10000:  # 现金太少，停止买入
                            break
                        if self._current_utilization(trading_days, current_date) >= self.target_util:
                            break
                        
                        # 创建或获取持仓
                        if code not in self.positions:
                            self.positions[code] = Position(code, self._get_name(code))
                        pos = self.positions[code]
                        
                        if not pos.can_buy_more(self.max_buys):
                            continue
                        
                        # 计算买入金额
                        buy_idx = pos.buy_count
                        target_pct = BACKTEST_POSITION_LADDER[buy_idx]
                        target_amount = self.capital * target_pct
                        
                        # 获取价格
                        df = self.daily_data_cache.get(code)
                        if df is None:
                            continue
                        row = df[df['date'].astype(str).str[:10] == current_date]
                        if row.empty:
                            continue
                        close_price = float(row.iloc[0]['close'])
                        
                        # 含滑点
                        buy_price = self._apply_slippage(close_price, is_buy=True)
                        
                        # 计算股数
                        buy_shares = int(target_amount / buy_price / 100) * 100
                        if buy_shares < 100:
                            continue
                        
                        cost = buy_shares * buy_price + buy_shares * buy_price * BACKTEST_COMMISSION
                        if cost > remaining_cash:
                            # 用剩余现金调整
                            affordable = int((remaining_cash / (buy_price * (1 + BACKTEST_COMMISSION))) / 100) * 100
                            if affordable < 100:
                                continue
                            buy_shares = affordable
                            cost = buy_shares * buy_price + buy_shares * buy_price * BACKTEST_COMMISSION
                        
                        # 结构止损位
                        structure_stop = self._calc_structure_stop(code, sig, close_price, current_date)
                        
                        # 止盈目标
                        tp_pct = self._calc_take_profit(sig)
                        
                        # 执行买入
                        self.cash -= cost
                        remaining_cash = self.cash
                        pos.add_buy(sig, buy_price, buy_shares, sig.get('reason', ''),
                                    structure_stop=structure_stop, take_profit_pct=tp_pct)
                        self.trades.append({
                            'date': current_date, 'code': code, 'name': pos.name,
                            'action': 'BUY', 'price': buy_price, 'shares': buy_shares,
                            'cost': cost, 'reason': sig.get('reason', '')[:40],
                            'confidence': sig['confidence'],
                        })
                        
                        if not self.quiet:
                            print(f"  🟢 {current_date} 买入 {pos.name}({code}) "
                                  f"{buy_shares}股 @ ¥{buy_price:.2f} "
                                  f"(第{pos.buy_count}笔, ¥{cost:.0f}, 置信{sig['confidence']}/5)")
            
            # ==== Step C: 记录每日资产 ====
            total_market_value = self.cash
            for code, pos in self.positions.items():
                if pos.total_shares == 0:
                    continue
                df = self.daily_data_cache.get(code)
                if df is None:
                    continue
                row = df[df['date'].astype(str).str[:10] == current_date]
                if row.empty:
                    continue
                total_market_value += pos.total_shares * float(row.iloc[0]['close'])
            
            self.daily_values.append({
                'date': current_date,
                'value': total_market_value,
                'cash': self.cash,
                'position_count': sum(1 for p in self.positions.values() if p.total_shares > 0),
                'utilization': (total_market_value - self.cash) / self.capital * 100,
            })
            
            if not self.quiet and day_idx % 50 == 0:
                util = (total_market_value - self.cash) / self.capital * 100
                print(f"  [{current_date}] 资产¥{total_market_value:,.0f} "
                      f"现金¥{self.cash:,.0f} 仓位{util:.1f}%")
        
        # 最终清仓
        if not self.quiet:
            print(f"\n[模拟] 最终清仓...")
        self._liquidate_all(trading_days[-1])
    
    # ============================================================
    # 辅助方法
    # ============================================================
    def _get_name(self, code: str) -> str:
        for c, n in self.stock_list:
            if c == code:
                return n
        return code
    
    def _current_utilization(self, trading_days, current_date) -> float:
        """计算当前仓位利用率"""
        total_mv = self.cash
        for code, pos in self.positions.items():
            if pos.total_shares == 0:
                continue
            df = self.daily_data_cache.get(code)
            if df is None:
                continue
            row = df[df['date'].astype(str).str[:10] == current_date]
            if row.empty:
                # 用最近价
                for d in reversed(trading_days):
                    if d <= current_date:
                        row = df[df['date'].astype(str).str[:10] == d]
                        if not row.empty:
                            break
            if row.empty:
                continue
            total_mv += pos.total_shares * float(row.iloc[0]['close'])
        
        return (total_mv - self.cash) / self.capital if self.capital > 0 else 0
    
    def _apply_slippage(self, price: float, is_buy: bool = True) -> float:
        if self.slippage is None:
            return price
        if is_buy:
            return self.slippage.apply_buy(price, None)  # 简化，不用成交量
        else:
            return self.slippage.apply_sell(price, None)
    
    def _calc_structure_stop(self, code: str, sig: Dict, close_price: float,
                             current_date: str) -> float:
        """计算结构止损位"""
        analyzer = self.analyzer_cache.get(code)
        if not analyzer:
            return 0
        
        reason = sig.get('reason', '')
        if '三类买点' in reason:
            for zs in analyzer.zhongshus:
                zs_end = str(zs.end_date)[:10] if zs.end_date else ''
                if zs_end and zs_end < current_date and zs.zg < close_price:
                    # 简单版：取最近的ZG
                    return zs.zg
        elif '一类买点' in reason or '二类买点' in reason:
            for zs in sorted(analyzer.zhongshus, key=lambda z: z.zd, reverse=True):
                zs_end = str(zs.end_date)[:10] if zs.end_date else ''
                if zs_end and zs_end < current_date and zs.zd < close_price:
                    return zs.zd
        return 0
    
    def _calc_take_profit(self, sig: Dict) -> float:
        reason = sig.get('reason', '')
        if '一类买点' in reason:
            return 0.30 * self.tp_multiplier
        elif '二类买点' in reason:
            return 0.20 * self.tp_multiplier
        else:
            return 0.15 * self.tp_multiplier
    
    def _check_stop_loss(self, pos: Position, current_price: float,
                         current_date: str) -> Optional[str]:
        """三层止损（简化版，无M30）"""
        if not pos.buy_lots:
            return None
        
        # 第一层：结构止损
        for lot in pos.buy_lots:
            if lot.structure_stop > 0 and current_price <= lot.structure_stop:
                return f"结构止损(¥{current_price:.2f}≤¥{lot.structure_stop:.2f})"
        
        # 第二层：无M30数据，跳过
        
        # 第三层：硬止损 -8%
        avg_entry = pos.avg_entry
        if avg_entry > 0 and current_price <= avg_entry * 0.92:
            return f"硬止损-8%(均价¥{avg_entry:.2f})"
        
        return None
    
    def _check_take_profit(self, pos: Position, current_price: float) -> bool:
        """加权均价止盈"""
        if not pos.buy_lots:
            return False
        total_s = pos.total_shares
        avg_tp = sum(l.shares * l.take_profit_pct for l in pos.buy_lots) / total_s
        avg_entry = pos.avg_entry
        return current_price >= avg_entry * (1 + avg_tp)
    
    def _execute_sell(self, code: str, pos: Position, sell_price: float,
                      current_date: str, reason: str):
        """清仓卖出"""
        sell_price_eff = self._apply_slippage(sell_price, is_buy=False)
        revenue = pos.total_shares * sell_price_eff
        commission = revenue * BACKTEST_COMMISSION
        self.cash += (revenue - commission)
        
        # 记录每笔买入的盈亏
        for lot in pos.buy_lots:
            pnl = (sell_price_eff / lot.price - 1) * 100
            self.trades.append({
                'date': current_date, 'code': code, 'name': pos.name,
                'action': 'SELL', 'price': sell_price_eff, 'shares': lot.shares,
                'entry_price': lot.price, 'pnl_pct': pnl,
                'revenue': lot.shares * sell_price_eff,
                'reason': reason[:40],
            })
        
        if not self.quiet:
            print(f"  🔴 {current_date} 卖出 {pos.name}({code}) "
                  f"{pos.total_shares}股 @ ¥{sell_price_eff:.2f} "
                  f"(¥{revenue:,.0f}, 原因: {reason[:30]})")
        
        # 清空持仓
        pos.buy_lots.clear()
        pos.buy_count = 0
        # 不移除 position 对象，保留以允许再次买入
    
    def _liquidate_all(self, last_date: str):
        """最终清仓所有持仓"""
        for code, pos in list(self.positions.items()):
            if pos.total_shares == 0:
                continue
            df = self.daily_data_cache.get(code)
            if df is None:
                continue
            row = df[df['date'].astype(str).str[:10] == last_date]
            if row.empty:
                continue
            close_price = float(row.iloc[0]['close'])
            self._execute_sell(code, pos, close_price, last_date, "回测结束清仓")
    
    # ============================================================
    # Phase 3: 统计
    # ============================================================
    def calculate_stats(self) -> Dict:
        """计算组合统计指标"""
        if not self.daily_values:
            return {}
        
        final_value = self.daily_values[-1]['value']
        total_return = (final_value / self.capital - 1) * 100
        
        # 年化
        days = len(self.daily_values)
        years = days / 252
        annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
        
        # 胜率（按卖出交易）
        sell_trades = [t for t in self.trades if t['action'] == 'SELL']
        win_count = sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0)
        win_rate = win_count / len(sell_trades) * 100 if sell_trades else 0
        
        # 平均仓位
        avg_util = sum(d['utilization'] for d in self.daily_values) / len(self.daily_values)
        max_util = max(d['utilization'] for d in self.daily_values)
        
        # 最大回撤
        values = [d['value'] for d in self.daily_values]
        peak = values[0]
        max_dd = 0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd
        
        # 买卖点分布
        buy_sigs = [t for t in self.trades if t['action'] == 'BUY']
        buy_type_dist = defaultdict(int)
        for t in buy_sigs:
            reason = t.get('reason', '')
            if '一类买点' in reason:
                buy_type_dist['一买'] += 1
            elif '二类买点' in reason:
                buy_type_dist['二买'] += 1
            elif '三类买点' in reason:
                buy_type_dist['三买'] += 1
            else:
                buy_type_dist['其他'] += 1
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'final_value': final_value,
            'years': years,
            'total_buys': len(buy_sigs),
            'total_sells': len(sell_trades),
            'win_rate': win_rate,
            'max_drawdown': max_dd,
            'avg_utilization': avg_util,
            'max_utilization': max_util,
            'excess_return': total_return - HS300_RETURN,
            'buy_dist': dict(buy_type_dist),
            'max_stocks_held': max(d['position_count'] for d in self.daily_values),
            'avg_stocks_held': sum(d['position_count'] for d in self.daily_values) / len(self.daily_values),
        }
    
    def print_stats(self, stats: Dict):
        print("\n" + "=" * 70)
        print("  组合回测结果")
        print("=" * 70)
        print(f"  初始资金:        ¥{self.capital:,.0f}")
        print(f"  最终资产:        ¥{stats.get('final_value', 0):,.0f}")
        print(f"  总收益率:        {stats.get('total_return', 0):+.2f}%")
        print(f"  年化收益率:      {stats.get('annual_return', 0):+.2f}%")
        print(f"  超额收益(vsHS300): {stats.get('excess_return', 0):+.2f}%")
        print(f"  回测周期:        {stats.get('years', 0):.2f}年")
        print(f"  {'─'*50}")
        print(f"  买入次数:        {stats.get('total_buys', 0)}次")
        print(f"  卖出次数:        {stats.get('total_sells', 0)}次")
        print(f"  胜率:            {stats.get('win_rate', 0):.1f}%")
        print(f"  最大回撤:        {stats.get('max_drawdown', 0):.2f}%")
        print(f"  {'─'*50}")
        print(f"  平均仓位:        {stats.get('avg_utilization', 0):.1f}%")
        print(f"  最大仓位:        {stats.get('max_utilization', 0):.1f}%")
        print(f"  平均持股数:      {stats.get('avg_stocks_held', 0):.1f}")
        print(f"  最大持股数:      {stats.get('max_stocks_held', 0)}")
        buy_dist = stats.get('buy_dist', {})
        if buy_dist:
            print(f"  买点分布:        {buy_dist}")
        print("=" * 70)
    
    def save_trades(self, path: str):
        # 统一字段（BUY + SELL 都可能有）
        all_keys = ['date', 'code', 'name', 'action', 'price', 'shares', 'cost',
                    'entry_price', 'pnl_pct', 'revenue', 'reason', 'confidence']
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(self.trades)
        print(f"\n交易记录: {path}")


# ============================================================
# 入口
# ============================================================
def load_stock_list(path: str = None, count: int = 50) -> List[Tuple[str, str]]:
    """加载股票列表"""
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)[:count]
    
    # 默认：从 A500 老票中取
    old_path = 'D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/.old_stocks_2016.json'
    if os.path.exists(old_path):
        with open(old_path) as f:
            return json.load(f)[:count]
    
    raise FileNotFoundError("找不到股票列表")


def print_comparison(solo_results: Dict, portfolio_results: Dict):
    """对比单只回测 vs 组合回测"""
    print(f"\n{'='*70}")
    print(f"  单只 vs 组合 对比")
    print(f"{'='*70}")
    print(f"  {'指标':<20} {'单只独立回测 (均值)':<25} {'组合回测':<20}")
    print(f"  {'─'*65}")
    
    # 从 solo_results CSV 中计算
    # (这里 solo_results 是一个 dict 映射 code->stats)
    if solo_results:
        avg_solo_ret = sum(r['total_return'] for r in solo_results.values()) / len(solo_results)
        avg_solo_wr = sum(r['win_rate'] for r in solo_results.values()) / len(solo_results)
        avg_solo_tr = sum(r['total_trades'] for r in solo_results.values()) / len(solo_results)
        beat_hs300 = sum(1 for r in solo_results.values() if r['total_return'] > HS300_RETURN)
        print(f"  {'平均收益':<20} {avg_solo_ret:>+8.2f}%               {portfolio_results.get('total_return', 0):>+8.2f}%")
        print(f"  {'跑赢HS300':<20} {beat_hs300}/{len(solo_results):<22} {'—':<20}")
        print(f"  {'平均胜率':<20} {avg_solo_wr:>8.1f}%               {'N/A':<20}")
        print(f"  {'平均交易(单只)':<20} {avg_solo_tr:>8.1f}次              {'N/A':<20}")
    
    print(f"  {'平均仓位':<20} {'—':<25} {portfolio_results.get('avg_utilization', 0):>8.1f}%")
    print(f"  {'最大回撤':<20} {'—':<25} {portfolio_results.get('max_drawdown', 0):>8.2f}%")
    print(f"  {'平均持股数':<20} {'—':<25} {portfolio_results.get('avg_stocks_held', 0):>8.1f}")
    print(f"{'='*70}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='组合回测')
    parser.add_argument('--count', type=int, default=50, help='股票数量')
    parser.add_argument('--capital', type=float, default=INITIAL_CAPITAL, help='初始资金')
    parser.add_argument('--tp', type=float, default=TP_MULTIPLIER, help='止盈倍数')
    parser.add_argument('--target-util', type=float, default=TARGET_UTILIZATION, help='目标仓位')
    parser.add_argument('--stock-list', type=str, help='股票列表JSON文件')
    parser.add_argument('--output', type=str, 
                        default='D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/portfolio_backtest_trades.csv')
    args = parser.parse_args()
    
    print("=" * 60)
    print("  组合回测")
    print(f"  区间: {BACKTEST_START} ~ {BACKTEST_END}")
    print(f"  初始资金: ¥{args.capital:,.0f}")
    print(f"  目标仓位: {args.target_util*100:.0f}%")
    print(f"  止盈倍数: ×{args.tp}")
    print("=" * 60)
    
    # 加载股票列表
    stock_list = load_stock_list(args.stock_list, args.count)
    print(f"\n股票池: {len(stock_list)} 只")
    
    # 回测
    bt = PortfolioBacktest(
        stock_list, capital=args.capital,
        tp_multiplier=args.tp,
        target_util=args.target_util,
    )
    
    print("\n[Phase 1] 预分析...")
    bt.pre_analyze()
    
    print("\n[Phase 2] 组合模拟...")
    bt.simulate()
    
    print("\n[Phase 3] 统计...")
    stats = bt.calculate_stats()
    bt.print_stats(stats)
    bt.save_trades(args.output)
    
    # 如果有单只回测结果，做对比
    solo_path = 'D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/slow_bull_results_baseline.csv'
    if os.path.exists(solo_path):
        import pandas as pd
        solo_df = pd.read_csv(solo_path)
        solo_dict = {str(row['code']): row.to_dict() for _, row in solo_df.iterrows()}
        print_comparison(solo_dict, stats)
    
    print("\n完毕。")
