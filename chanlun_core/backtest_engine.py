"""
backtest_engine.py
基于缠论的"漏斗过滤法"回测引擎

策略逻辑：
1. 日线出现买点 + 30分钟确认（直接确认2分/背驰确认1分）→ 建仓30%
2. 30分钟三类买点 → 加仓40%
3. 日线出现卖点 / 30分钟卖点 → 清仓

统计指标：
- 总交易次数、胜率
- 平均盈亏、盈亏比
- 最大回撤
- 夏普比率
- 总收益率、年化收益率

用法：
  python backtest_engine.py 301498                     # 单只
  python backtest_engine.py 301498 600519 688981       # 批量
  python backtest_engine.py 301498 --ref 58.6          # 指定参考价
"""

import pandas as pd
from date_utils import date_to_str, parse_date_to_datetime
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from data_manager import DataManager
from generate_analysis import RecursiveTimingSystem, ChanLunAnalyzer
from trading_strategy import TradingStrategy, TradeSignal
from slippage_model import SlippageModel
from config_loader import (
    BACKTEST_INITIAL_CAPITAL, BACKTEST_COMMISSION, BACKTEST_STAMP_DUTY,
    BACKTEST_MAX_POSITION_PCT, BACKTEST_POSITION_LADDER,
    BACKTEST_ENABLE_SLIPPAGE,
    THRESHOLD_BACKTEST_TP_FIRST, THRESHOLD_BACKTEST_TP_SECOND,
    THRESHOLD_BACKTEST_TP_THIRD, THRESHOLD_BACKTEST_M30_FILTER,
    THRESHOLD_BACKTEST_RISK_FREE_RATE,
)


@dataclass
class TradeRecord:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    direction: str  # 'long'
    position_pct: float  # 仓位比例
    pnl_pct: float       # 该笔收益率
    reason_entry: str
    reason_exit: str
    hold_days: int
    confidence: float    # 信号置信度
    shares: int = 0      # 该笔股数


class BacktestEngine:
    def __init__(self, symbol: str, name: str = "", reference_price: float = None,
                 initial_capital: float = None,
                 enable_slippage: bool = None,
                 tp_multiplier: float = 1.0,
                 sell_reduce_pct: float = 0.0):
        """
        tp_multiplier: 止盈倍数（默认1.0, slow_bull建议1.67→三买15%→25%）
        sell_reduce_pct: 卖点减仓比例（默认0=全清, slow_bull建议0.5→卖点只减半仓）
        """
        self.symbol = symbol
        self.name = name or symbol
        self.reference_price = reference_price
        self.initial_capital = initial_capital if initial_capital is not None else BACKTEST_INITIAL_CAPITAL
        self.dm = DataManager()
        self.strategy = TradingStrategy()
        self.trades: List[TradeRecord] = []
        self.enable_slippage = enable_slippage if enable_slippage is not None else BACKTEST_ENABLE_SLIPPAGE
        self.slippage_model = SlippageModel() if self.enable_slippage else None
        self._avg_daily_volume = None
        self.tp_multiplier = tp_multiplier
        self.sell_reduce_pct = sell_reduce_pct
        
        # 漏斗过滤法仓位管理
        # 仓位规则：单一个股占总资金不超过30%
        # 建仓梯度：按单股最大分配资金的 30%(建仓)+20%(加仓)+10%(加仓)，加仓最多2次
        # 换算为总资金比例: 30%*30%=9%, 20%*30%=6%, 10%*30%=3%
        self.max_position_pct = BACKTEST_MAX_POSITION_PCT
        self.position_ladder = list(BACKTEST_POSITION_LADDER)
        self.max_buys = len(self.position_ladder)

    def _record_trade(self, lot: dict, sell_shares: int, sell_price: float,
                      current_date, reason: str, confidence: float):
        """记录一笔卖出（部分 or 全部）"""
        lot_hold_days = 0
        try:
            d1 = parse_date_to_datetime(lot['date'])
            d2 = datetime.strptime(date_to_str(current_date), '%Y-%m-%d')
            lot_hold_days = (d2 - d1).days
        except:
            pass
        lot_pnl = (sell_price / lot['price'] - 1) * 100
        self.trades.append(TradeRecord(
            entry_date=str(lot['date'])[:10],
            exit_date=date_to_str(current_date),
            entry_price=lot['price'],
            exit_price=sell_price,
            direction='long',
            position_pct=round(sell_shares * lot['price'] / self.initial_capital * 100, 1) if self.initial_capital > 0 else 0,
            pnl_pct=lot_pnl,
            reason_entry=lot.get('reason', ''),
            reason_exit=reason,
            hold_days=lot_hold_days,
            confidence=confidence,
            shares=sell_shares,
        ))

    def _effective_price(self, close_price: float, is_buy: bool = True) -> float:
        """计算含滑点的有效成交价"""
        if not self.enable_slippage or self.slippage_model is None:
            return close_price
        if is_buy:
            return self.slippage_model.apply_buy(close_price, self._avg_daily_volume)
        else:
            return self.slippage_model.apply_sell(close_price, self._avg_daily_volume)

    def load_all_data(self, start_date: str = "2024-01-01", end_date: str = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """加载全部日线和30分钟数据（仅获取一次）
        
        30分钟数据仅2020年起Baostock有覆盖。此前年份跳过fallback链，
        直接返回空DataFrame，由 run_single_analysis 的 daily-only 降级模式接管。
        """
        print(f"[Backtest] 加载数据 {self.symbol}...")
        daily_data = self.dm.get_klines(self.symbol, 'daily', start_date=start_date, end_date=end_date)
        
        # 30分钟数据：2020年前无免费数据源，跳过避免无效fallback消耗时间
        MIN30_AVAILABLE_SINCE = "2020-01-01"
        if start_date >= MIN30_AVAILABLE_SINCE:
            m30_data = self.dm.get_klines(self.symbol, '30min', start_date=start_date, end_date=end_date)
        else:
            m30_data = pd.DataFrame()
            print(f"[Backtest] 跳过30分钟数据（{start_date} 早于{MIN30_AVAILABLE_SINCE}，无分钟级数据源）")
        
        print(f"[Backtest] 日线: {len(daily_data)}行, 30分钟: {len(m30_data)}行")
        return daily_data, m30_data

    def _check_m30_downtrend(self, m30_analyzer, buy_date):
        """检查30分钟级别在买入日期附近是否有持续下跌段
        三买时如果30分钟连续≥2支完结下跌笔（构成下跌段），说明30分钟下跌趋势未反转，
        此时不执行买入，避免在半山腰接盘
        
        注：单支30分钟下跌笔是正常波动，不构成过滤条件
        """
        if not m30_analyzer or not m30_analyzer.bis:
            return False
        
        # 找到买入日期前完结的30分钟笔
        recent_bis = []
        for bi in m30_analyzer.bis:
            bi_end = str(bi.end_date)[:10] if bi.end_date else ''
            buy_date_str = str(buy_date)
            if bi_end and bi_end <= buy_date_str:
                recent_bis.append(bi)
        
        # 按结束日期降序排列
        recent_bis.sort(key=lambda b: str(b.end_date)[:10], reverse=True)
        
        # 检查最近完结的连续笔方向：连续≥2支下跌笔才视为下跌段未完结
        down_count = 0
        for bi in recent_bis:
            if bi.direction == 'down':
                down_count += 1
            else:
                break  # 遇到上升笔就停止计数
        
        return down_count >= THRESHOLD_BACKTEST_M30_FILTER  # 可配置（原为>=2，误杀标准三买回调）

    def _find_zs_for_third_buy(self, buy_price, buy_date, zhongshus, bis=None):
        """找到三买对应的中枢：通过笔路径追踪精确匹配，含未来函数防护

        路径追踪逻辑：
          买入笔(Up) ← 回踩笔(Down,低点>ZG) ← 突破笔(Up,高点>ZG) ← 中枢

          通过"突破笔→回踩笔→买入笔"的笔序列，找到那个被突破的具体中枢。
          只追踪紧邻的笔序列（买入笔的前一笔是回踩笔，再前一笔是突破笔），
          避免误匹配到更早时期也有突破的其他中枢。

        回退方案：取已形成中枢中 ZG < buy_price 且 ZG 最大的"""
        buy_date_str = date_to_str(buy_date)

        # ===== 方案1：笔路径追踪 =====
        if bis and len(bis) >= 3:
            # 找到买入笔（在买入日开始的向上笔）
            buy_idx = None
            for i, bi in enumerate(bis):
                if str(bi.start_date)[:10] == buy_date_str and bi.direction == 'up':
                    buy_idx = i
                    break

            if buy_idx is not None and buy_idx >= 2:
                buy_bi = bis[buy_idx]
                pullback_bi = bis[buy_idx - 1]
                breakout_bi = bis[buy_idx - 2]

                if (pullback_bi.direction == 'down'
                        and breakout_bi.direction == 'up'):
                    pullback_low = min(pullback_bi.start_price, pullback_bi.end_price)
                    breakout_high = max(breakout_bi.start_price, breakout_bi.end_price)
                    breakout_low = min(breakout_bi.start_price, breakout_bi.end_price)

                    # 找中枢：1)已形成 2)ZG<回踩低点 3)被这根突破笔从下方穿过ZG
                    best = None
                    for zs in zhongshus:
                        zs_end = str(zs.end_date)[:10] if zs.end_date else ''
                        if not zs_end or zs_end >= buy_date_str:
                            continue
                        if zs.zg >= pullback_low:
                            continue
                        # 突破笔必须从ZG下方穿过（breakout_low < ZG < breakout_high）
                        # 避免"已在上方运行"的延续笔被误认为突破
                        if breakout_low < zs.zg < breakout_high:
                            if best is None or zs.zg > best.zg:
                                best = zs

                    if best:
                        return best

        # ===== 方案2：回退启发式 =====
        candidate = None
        for zs in zhongshus:
            zs_end = str(zs.end_date)[:10] if zs.end_date else ''
            if zs_end and zs_end >= buy_date_str:
                continue
            if zs.zg < buy_price:
                if candidate is None or zs.zg > candidate.zg:
                    candidate = zs
        if candidate:
            return candidate

    def _check_stop_loss(self, current_price, current_date, buy_lots,
                          daily_analyzer, m30_analyzer):
        """三层止损检查
        返回: (是否触发止损, 止损原因, 止损类型)
        
        第一层 - 结构止损：跌破结构位（中枢ZG/ZD）
        第二层 - 次级别卖点：30分钟出现卖点
        第三层 - 硬止损：加权均价 -8%
        """
        if not buy_lots:
            return False, "", ""
        
        # 第一层：结构止损 - 检查是否跌破结构位（最紧，通常ZG在入场价下方3-6%）
        for lot in buy_lots:
            structure_stop = lot.get('structure_stop', 0)
            if structure_stop > 0 and current_price <= structure_stop:
                stop_label = f"结构止损: 跌回结构位(¥{current_price:.2f} ≤ ¥{structure_stop:.2f})"
                return True, stop_label, "structure"
        
        # 第二层：次级别卖点 - 30分钟出现卖点（v4.2 只允许过去，不允许未来）
        if m30_analyzer and m30_analyzer.buy_sell_points:
            for m30_point in m30_analyzer.buy_sell_points:
                if m30_point.type == 'sell':
                    m30_date = date_to_str(m30_point.date) if m30_point.date else ''
                    try:
                        m30_dt = datetime.strptime(m30_date, '%Y-%m-%d')
                        cur_dt = datetime.strptime(date_to_str(current_date), '%Y-%m-%d')
                        # 只允许过去的30分钟卖点触发止损（0 <= cur - m30 <= 1天）
                        # 原实现 abs() <= 1 允许"明天"的卖点今天触发止损（前视偏差）
                        if 0 <= (cur_dt - m30_dt).days <= 1:
                            return True, f"次级别卖点: 30分钟{m30_point.reason}", "m30_sell"
                    except:
                        if m30_date == date_to_str(current_date):
                            return True, f"次级别卖点: 30分钟{m30_point.reason}", "m30_sell"
        
        # 第三层：硬止损 -8%（最后防线，基于加权均价）
        total_shares = sum(lot['shares'] for lot in buy_lots)
        total_cost = sum(lot['shares'] * lot['price'] for lot in buy_lots)
        avg_price = total_cost / total_shares if total_shares > 0 else buy_lots[0]['price']
        hard_stop_price = avg_price * 0.92
        if current_price <= hard_stop_price:
            return True, f"硬止损(-8%): ¥{current_price:.2f} ≤ ¥{hard_stop_price:.2f}(均价¥{avg_price:.2f})", "hard"
        
        return False, "", ""

    def run_single_analysis(self, daily_data: pd.DataFrame, m30_data: pd.DataFrame,
                            reference_price: float = None) -> Tuple:
        """基于已加载的数据执行分析，不重复API调用"""
        rec_sys = RecursiveTimingSystem(self.dm)
        
        # ========== 日线分析 ==========
        daily_analyzer = ChanLunAnalyzer(level='daily').analyze(
            self.dm.to_json_list(daily_data)
        )
        
        # 价格校准（统一方法）
        if reference_price and not daily_data.empty:
            ChanLunAnalyzer.calibrate_prices(
                daily_analyzer,
                float(daily_data.iloc[-1]['close']),
                reference_price,
                caller_tag="Backtest"
            )
        
        # ========== 30分钟分析 + 多级别验证 ==========
        m30_analyzer = None
        m30_available = not m30_data.empty
        if m30_available:
            m30_analyzer = ChanLunAnalyzer(level='30min').analyze(
                self.dm.to_json_list(m30_data)
            )
            rec_sys.analyses['30min'] = m30_analyzer
            rec_sys._perform_multilevel_validation(daily_analyzer, m30_analyzer)
        else:
            print(f"[Backtest] ⚠ 30分钟数据不可用，降级为日线独立模式"
                  f"（买入信号不要求30分钟确认，置信度降低）")
        
        rec_sys.analyses['daily'] = daily_analyzer
        
        # ========== 收集带置信度的买卖点 ==========
        signal_points = []
        for point in daily_analyzer.buy_sell_points:
            conf = getattr(point, 'multilevel_confirmation', {})
            if m30_available:
                signal_points.append({
                    'type': point.type,
                    'level': point.level,
                    'date': point.date,
                    'price': point.price,
                    'reason': point.reason,
                    'confidence': conf.get('confidence_score', 0),
                    'm30_confirmed': conf.get('m30_confirmation', False),
                    'high_confidence': conf.get('high_confidence', False)
                })
            else:
                # 无30分钟数据时的降级置信度：
                # 一类买点=2, 二类买点=2, 三类买点=2, 兜底信号=1
                base_confidence = 2 if point.level in (1, 2, 3) else 1
                signal_points.append({
                    'type': point.type,
                    'level': point.level,
                    'date': point.date,
                    'price': point.price,
                    'reason': point.reason,
                    'confidence': base_confidence,
                    'm30_confirmed': True,   # 降级模式下放行（不要求30分钟确认）
                    'high_confidence': False,
                    '_m30_degraded': True    # 标记为降级信号
                })
        
        return daily_analyzer, m30_analyzer, signal_points

    def run_backtest(self, start_date="2024-01-01", end_date=None, analysis_start_date=None,
                     daily_data=None, m30_data=None, daily_analyzer=None, m30_analyzer=None):
        """执行回测
        
        参数:
            daily_data, m30_data: 可选，已加载的 DataFrame（跳过 load_all_data）
            daily_analyzer, m30_analyzer: 可选，已分析的 ChanLunAnalyzer（跳过 run_single_analysis）
            若四个都提供则完全跳过数据加载和分析阶段
        """
        print(f"\n{'='*50}")
        print(f"  漏斗过滤法回测 — {self.name}({self.symbol})")
        print(f"  初始资金: ¥{self.initial_capital:,.2f}")
        print(f"  数据范围: {analysis_start_date if analysis_start_date else start_date} 起")
        if self.reference_price:
            print(f"  参考价:   ¥{self.reference_price:.2f}")
        print(f"{'='*50}\n")
        
        # 1. 加载数据（若外部已提供则跳过）
        reuse_data = (daily_data is not None and m30_data is not None 
                      and daily_analyzer is not None)
        if reuse_data:
            print(f"[Backtest] 复用外部数据（跳过加载）: 日线{len(daily_data)}行, 30分钟{len(m30_data)}行")
        else:
            data_start = analysis_start_date or start_date
            daily_data, m30_data = self.load_all_data(data_start, end_date=end_date)
        if daily_data.empty:
            print("[Backtest] ⚠ 无数据，退出")
            return None
        
        # 找出数据中最早的交易日和实际回测起始日
        first_data_date = str(daily_data.iloc[0]['date'])[:10]
        print(f"[Backtest] 数据起止: {first_data_date} → {str(daily_data.iloc[-1]['date'])[:10]}")
        if analysis_start_date:
            print(f"[Backtest] 模拟交易从 {start_date} 开始（此前数据仅用于结构识别）")
        
        # 估算日均成交额（用于滑点模型）
        if self.enable_slippage and self.slippage_model:
            self._avg_daily_volume = SlippageModel.estimate_avg_daily_volume(daily_data)
            if self._avg_daily_volume:
                slippage = self.slippage_model.get_slippage(self._avg_daily_volume)
                print(f"[Backtest] 滑点模型: 日均成交额 ¥{self._avg_daily_volume/1e8:.1f}亿, 滑点 {slippage*100:.2f}%")
            else:
                print(f"[Backtest] 滑点模型: 无成交额数据, 使用默认滑点 {self.slippage_model.base_slippage*100:.2f}%")
        
        # 2. 30分钟分析（仅一次，消费端用日期过滤保证无前视）
        # ⚠️ v4.2 滚动窗口改造：
        #    - 日线信号每天滚动重算（见主循环），不复用全量分析结果
        #    - 30分钟分析一次，但所有消费点（多级别确认 A3、止损 A4、
        #      _check_m30_downtrend）均已按日期过滤，只使用截止当日的
        #      30分钟信号，等价于滚动且性能可接受
        #    - enable_forward_validation=False：30分钟买卖点不做未来清洗
        if reuse_data and m30_analyzer is not None:
            print(f"[Backtest] 复用外部30分钟分析器（跳过30分钟结构分析）")
        else:
            m30_analyzer = None
            if not m30_data.empty:
                m30_analyzer = ChanLunAnalyzer(
                    level='30min', enable_forward_validation=False
                ).analyze(self.dm.to_json_list(m30_data))
            print(f"[Backtest] 30分钟分析: {'完成(' + str(len(m30_analyzer.bis)) + '笔)' if m30_analyzer else '无(降级为日线独立模式)'}")

        # 3. 模拟交易（滚动窗口漏斗过滤法）
        cash = self.initial_capital
        shares = 0
        buy_count = 0  # 当前买入次数（0=未买, 1=已建仓, 2=已加仓1次, 3=已加仓2次，已达上限）
        portfolio_values = []
        buy_lots = []  # 记录每笔买入明细 [{price, date, reason, shares}]
        rec_sys = RecursiveTimingSystem(self.dm)

        # ═══ v4.2 信号可见性跟踪 ═══
        # 滚动窗口下，信号的结构日期（如三买突破日 10-17）与确认可见日
        # （回踩笔完成后 10-21）不同。回测必须在"首次可见日"交易，
        # 而非信号结构日（结构日在滚动分析中尚未确认）。
        # 维护已见信号集合：point.date <= current_date 且 key 首次出现
        # → 视为新信号（该日才被确认可见）
        _seen_signal_keys = set()

        print(f"  仓位规则: 单股最大 {self.max_position_pct*100:.0f}% 总资金")
        print(f"  买入阶梯: {[f'{p*100:.0f}%' for p in self.position_ladder]} → 共{self.max_buys}笔")
        print(f"  ⚡ v4.2 滚动窗口模式: 日线信号每日重算（消除前视偏差）")
        print()

        for i in range(len(daily_data)):
            row = daily_data.iloc[i]
            current_date = row['date']

            # 跳过start_date之前的日期（它们仅用于结构识别，不模拟交易）
            if date_to_str(current_date) < start_date:
                continue
            close_price = float(row['close'])

            # ════════════════════════════════════════════════════════
            # v4.2 滚动窗口日线分析：只用截至当天的数据重新计算
            # 消除前视偏差：信号产生依赖的笔/分型/中枢/确认全部只看到当日
            # ════════════════════════════════════════════════════════
            daily_until = daily_data.iloc[:i + 1]
            daily_analyzer = ChanLunAnalyzer(
                level='daily', enable_forward_validation=False
            ).analyze(self.dm.to_json_list(daily_until))

            # 取当日"首次可见"的信号（v4.2）
            # 信号 date <= current_date 且 key 未被见过 → 该日才被确认可见
            day_signals = []
            for point in daily_analyzer.buy_sell_points:
                point_date_str = str(point.date)[:10]
                if point_date_str > str(current_date)[:10]:
                    continue  # 尚未发生的信号（理论不应出现，防御）
                sig_key = (point.type, point.level, point_date_str)
                if sig_key in _seen_signal_keys:
                    continue  # 之前已见过（非首次可见）
                # ⚠️ v4.2 修复（批次C1补漏）：confirmed=False 的"潜在一买"不可建仓
                # 潜在一买只是背驰条件满足，尚无向上一笔确认，买入=接飞刀
                if point.type == 'buy' and getattr(point, 'confirmed', False) is not True:
                    continue
                _seen_signal_keys.add(sig_key)
                if m30_analyzer is not None:
                    # 多级别确认（只允许30分钟信号在日线点之前，A3已修复）
                    conf_info = rec_sys._check_m30_confirmation(point, m30_analyzer)
                    m30_confirmed = conf_info['confirmed']
                    base_score = {1: 2, 2: 2, 3: 4}.get(point.level, 1)
                    confirmation_score = {'direct': 2, 'divergence': 1, 'macd': 1}.get(conf_info['type'], 0)
                    confidence_score = base_score + confirmation_score
                    day_signals.append({
                        'type': point.type,
                        'level': point.level,
                        'date': point.date,
                        'price': point.price,
                        'reason': point.reason,
                        'confidence': confidence_score,
                        'm30_confirmed': m30_confirmed,
                        'high_confidence': confidence_score >= 4
                    })
                else:
                    # 无30分钟数据时的降级置信度：
                    # 一类买点=2, 二类买点=2, 三类买点=2, 兜底信号=1
                    base_confidence = 2 if point.level in (1, 2, 3) else 1
                    day_signals.append({
                        'type': point.type,
                        'level': point.level,
                        'date': point.date,
                        'price': point.price,
                        'reason': point.reason,
                        'confidence': base_confidence,
                        'm30_confirmed': True,   # 降级模式下放行（不要求30分钟确认）
                        'high_confidence': False,
                        '_m30_degraded': True    # 标记为降级信号
                    })

            for sig in day_signals:
                if sig['type'] == 'buy':
                    if buy_count >= self.max_buys:
                        print(f"  ⏭ {current_date} 跳过买入(已达{self.max_buys}笔上限)")
                        continue
                    if not sig['m30_confirmed']:
                        continue
                    
                    # ===== 30分钟下跌笔过滤（仅三类买点需要）=====
                    # 一类/二类买点本身就处在下跌末端，不应被此过滤拦截
                    if ('三类买点' in sig.get('reason', '')
                            and m30_analyzer
                            and self._check_m30_downtrend(m30_analyzer, current_date)):
                        print(f"  ⛔ {current_date} 跳过买入(30分钟下跌笔未完结): {sig['reason'][:40]}")
                        continue
                    
                    # 按阶梯计算目标投入金额（基于初始总资金）
                    target_pct = self.position_ladder[buy_count]
                    target_cost = self.initial_capital * target_pct
                    buy_price = self._effective_price(close_price, is_buy=True)
                    buy_shares = int(target_cost / buy_price / 100) * 100
                    
                    if buy_shares < 100:
                        print(f"  ⏭ {current_date} 跳过买入(目标¥{target_cost:.0f}不够100股，需至少¥{buy_price*100:.0f})")
                        continue
                    
                    cost = buy_shares * buy_price
                    if cost > cash:
                        # 现金不够，用剩余现金重新算
                        buy_shares = int(cash / buy_price / 100) * 100
                        if buy_shares < 100:
                            print(f"  ⏭ {current_date} 跳过买入(现金不足¥{cash:.0f})")
                            continue
                        cost = buy_shares * buy_price
                    
                    # ===== 结构止损位（所有类型买点都设置）=====
                    structure_stop = 0
                    if '三类买点' in sig.get('reason', ''):
                        zs_for_buy = self._find_zs_for_third_buy(close_price, current_date, daily_analyzer.zhongshus, daily_analyzer.bis)
                        if zs_for_buy:
                            structure_stop = zs_for_buy.zg
                    elif '一类买点' in sig.get('reason', '') or '二类买点' in sig.get('reason', ''):
                        # 一类/二类：取买入日前、价格低于入场价的中枢中 ZD 最高的作为支撑
                        cur_date_str = date_to_str(current_date)
                        for zs in sorted(daily_analyzer.zhongshus, key=lambda z: z.zd, reverse=True):
                            zs_end = str(zs.end_date)[:10] if zs.end_date else ''
                            if zs_end and zs_end < cur_date_str and zs.zd < close_price:
                                structure_stop = zs.zd
                                break
                    
                    # ===== 止盈目标（按买点类型分级）=====
                    if '一类买点' in sig.get('reason', ''):
                        take_profit_pct = THRESHOLD_BACKTEST_TP_FIRST * self.tp_multiplier
                    elif '二类买点' in sig.get('reason', ''):
                        take_profit_pct = THRESHOLD_BACKTEST_TP_SECOND * self.tp_multiplier
                    else:
                        take_profit_pct = THRESHOLD_BACKTEST_TP_THIRD * self.tp_multiplier
                    
                    # ===== 手续费 0.03% =====
                    commission = cost * BACKTEST_COMMISSION
                    cash -= (cost + commission)
                    shares += buy_shares
                    buy_lots.append({
                        'price': buy_price, 'date': current_date,
                        'reason': sig['reason'], 'shares': buy_shares,
                        'structure_stop': structure_stop,
                        'take_profit_pct': take_profit_pct
                    })
                    buy_count += 1
                    position_pct = (shares * buy_price) / self.initial_capital * 100
                    stop_info = f", 结构止损¥{structure_stop:.2f}" if structure_stop > 0 else ""
                    tp_info = f", 止盈+{take_profit_pct*100:.0f}%"
                    slippage_info = f", 滑点+{(buy_price/close_price-1)*100:.2f}%" if self.enable_slippage else ""
                    print(f"  🟢 {current_date} 买入 {buy_shares}股 @ ¥{buy_price:.2f} "
                          f"(第{buy_count}笔/{self.max_buys}, 仓位{position_pct:.1f}%, "
                          f"投入¥{cost:.0f}, 置信度{sig['confidence']}/5{stop_info}{tp_info}{slippage_info})")
                
                elif sig['type'] == 'sell' and shares > 0:
                    sell_price = self._effective_price(close_price, is_buy=False)
                    
                    # 计算卖出股数（部分减仓 or 全清）
                    if 0 < self.sell_reduce_pct < 1.0:
                        sell_shares = int(shares * self.sell_reduce_pct / 100) * 100
                        if sell_shares < 100:
                            sell_shares = shares  # 太少则全卖
                    else:
                        sell_shares = shares  # 全卖
                    
                    revenue = sell_shares * sell_price
                    commission = revenue * BACKTEST_COMMISSION
                    stamp_duty = revenue * BACKTEST_STAMP_DUTY  # v4.2 卖出印花税 0.05%
                    cash += (revenue - commission - stamp_duty)
                    
                    # FIFO：从最早的 buy_lot 开始卖出
                    remaining = sell_shares
                    new_lots = []
                    for lot in buy_lots:
                        if remaining <= 0:
                            new_lots.append(lot)
                            continue
                        if lot['shares'] <= remaining:
                            # 该批全部卖出
                            self._record_trade(lot, lot['shares'], sell_price, current_date, sig['reason'], sig['confidence'])
                            remaining -= lot['shares']
                        else:
                            # 部分卖出
                            self._record_trade(lot, remaining, sell_price, current_date, sig['reason'], sig['confidence'])
                            new_lots.append({**lot, 'date': lot['date'],
                                           'shares': lot['shares'] - remaining})
                            remaining = 0
                    
                    shares -= sell_shares
                    buy_lots = new_lots
                    buy_count = len(buy_lots)
                    action = "减仓" if shares > 0 else "清仓"
                    print(f"  🔴 {current_date} {action} {sell_shares}股 @ ¥{sell_price:.2f}"
                          f" (剩余{shares}股, {buy_count}批, 原因: {sig['reason'][:30]})")
                    if shares == 0:
                        buy_count = 0
            
            # ===== 三层止损检查 =====
            if shares > 0 and buy_lots:
                stop_triggered, stop_reason, stop_type = self._check_stop_loss(
                    close_price, current_date, buy_lots,
                    daily_analyzer, m30_analyzer
                )
                if stop_triggered:
                    sell_price = self._effective_price(close_price, is_buy=False)
                    revenue = shares * sell_price
                    commission = revenue * BACKTEST_COMMISSION
                    stamp_duty = revenue * BACKTEST_STAMP_DUTY  # v4.2 卖出印花税
                    cash += (revenue - commission - stamp_duty)
                    
                    # 逐笔记录盈亏
                    for lot in buy_lots:
                        lot_hold_days = 0
                        try:
                            d1 = parse_date_to_datetime(lot['date'])
                            d2 = datetime.strptime(date_to_str(current_date), '%Y-%m-%d')
                            lot_hold_days = (d2 - d1).days
                        except:
                            pass
                        lot_pnl = (sell_price / lot['price'] - 1) * 100
                        self.trades.append(TradeRecord(
                            entry_date=str(lot['date'])[:10],
                            exit_date=date_to_str(current_date),
                            entry_price=lot['price'],
                            exit_price=sell_price,
                            direction='long',
                            position_pct=round(lot.get('shares', 0) * lot['price'] / self.initial_capital * 100, 1) if self.initial_capital > 0 else 0,
                            pnl_pct=lot_pnl,
                            reason_entry=lot.get('reason', ''),
                            reason_exit=stop_reason,
                            hold_days=lot_hold_days,
                            confidence=0,
                            shares=lot.get('shares', 0),
                        ))
                    print(f"  🛑 {current_date} 止损清仓 {shares}股 @ ¥{sell_price:.2f} ({stop_reason})")
                    shares = 0
                    buy_count = 0
                    buy_lots = []
            
            # ===== 止盈检查（加权均价止盈）=====
            if shares > 0 and buy_lots:
                total_s = sum(lot['shares'] for lot in buy_lots)
                total_c = sum(lot['shares'] * lot['price'] for lot in buy_lots)
                avg_entry = total_c / total_s if total_s > 0 else 0
                avg_tp_pct = sum(lot['shares'] * lot.get('take_profit_pct', 0.15) for lot in buy_lots) / total_s if total_s > 0 else 0.15
                tp_price = avg_entry * (1 + avg_tp_pct)
                
                if close_price >= tp_price:
                    sell_price = self._effective_price(close_price, is_buy=False)
                    revenue = shares * sell_price
                    commission = revenue * BACKTEST_COMMISSION
                    stamp_duty = revenue * BACKTEST_STAMP_DUTY  # v4.2 卖出印花税
                    cash += (revenue - commission - stamp_duty)
                    
                    for lot in buy_lots:
                        lot_hold_days = 0
                        try:
                            d1 = parse_date_to_datetime(lot['date'])
                            d2 = datetime.strptime(date_to_str(current_date), '%Y-%m-%d')
                            lot_hold_days = (d2 - d1).days
                        except:
                            pass
                        lot_pnl = (sell_price / lot['price'] - 1) * 100
                        self.trades.append(TradeRecord(
                            entry_date=str(lot['date'])[:10],
                            exit_date=date_to_str(current_date),
                            entry_price=lot['price'],
                            exit_price=sell_price,
                            direction='long',
                            position_pct=round(lot.get('shares', 0) * lot['price'] / self.initial_capital * 100, 1) if self.initial_capital > 0 else 0,
                            pnl_pct=lot_pnl,
                            reason_entry=lot.get('reason', ''),
                            reason_exit=f"止盈(+{avg_tp_pct*100:.0f}%): ¥{close_price:.2f} ≥ ¥{tp_price:.2f}",
                            hold_days=lot_hold_days,
                            confidence=0,
                            shares=lot.get('shares', 0),
                        ))
                    print(f"  🎯 {current_date} 止盈清仓 {shares}股 @ ¥{sell_price:.2f} "
                          f"(均价¥{avg_entry:.2f} +{avg_tp_pct*100:.0f}% = ¥{tp_price:.2f})")
                    shares = 0
                    buy_count = 0
                    buy_lots = []
            
            # 记录每日资产
            current_value = cash + shares * close_price
            portfolio_values.append({
                'date': current_date,
                'value': current_value,
                'shares': shares,
                'cash': cash,
                'position_pct': (shares * close_price) / self.initial_capital if self.initial_capital > 0 else 0
            })
        
        # 4. 最终清仓（按每笔买入分别计算盈亏）
        if shares > 0:
            last_price = self._effective_price(float(daily_data.iloc[-1]['close']), is_buy=False)
            revenue = shares * last_price
            commission = revenue * BACKTEST_COMMISSION
            stamp_duty = revenue * BACKTEST_STAMP_DUTY  # v4.2 卖出印花税
            cash += (revenue - commission - stamp_duty)
            
            # 每笔买入分别记录（含持仓天数计算）
            for lot in buy_lots:
                lot_pnl = (last_price / lot['price'] - 1) * 100
                lot_hold_days = 0
                try:
                    d1 = parse_date_to_datetime(lot['date'])
                    d2 = parse_date_to_datetime(daily_data.iloc[-1]['date'])
                    lot_hold_days = (d2 - d1).days
                except:
                    pass
                self.trades.append(TradeRecord(
                    entry_date=str(lot['date'])[:10],
                    exit_date=str(daily_data.iloc[-1]['date'])[:10],
                    entry_price=lot['price'],
                    exit_price=last_price,
                    direction='long',
                    position_pct=round(lot.get('shares', 0) * lot['price'] / self.initial_capital * 100, 1) if self.initial_capital > 0 else 0,
                    pnl_pct=lot_pnl,
                    reason_entry=lot.get('reason', ''),
                    reason_exit="回测结束平仓",
                    hold_days=lot_hold_days,
                    confidence=0,
                    shares=lot.get('shares', 0),
                ))
            print(f"  🔴 回测结束强制平仓 {shares}股 @ ¥{last_price:.2f}"
                  f" ({len(buy_lots)}笔买入分别计算盈亏，详见表)")
            shares = 0
            buy_count = 0
            buy_lots = []
        
        final_value = cash
        
        # 5. 统计指标
        stats = self._calculate_stats(portfolio_values, final_value)
        self._print_stats(stats)
        
        return stats

    def _calculate_stats(self, daily_values: List[Dict], final_value: float):
        """计算回测统计指标"""
        total_return = (final_value / self.initial_capital - 1) * 100
        
        if daily_values:
            first_date = daily_values[0]['date']
            last_date = daily_values[-1]['date']
            try:
                d1 = datetime.strptime(date_to_str(first_date), '%Y-%m-%d')
                d2 = datetime.strptime(date_to_str(last_date), '%Y-%m-%d')
                days = max((d2 - d1).days, 1)
                years = days / 365.0
                annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
            except:
                years = 1
                annual_return = total_return
        else:
            years = 1
            annual_return = total_return
        
        total_trades = len(self.trades)
        wins = [t for t in self.trades if t.pnl_pct > 0]
        losses = [t for t in self.trades if t.pnl_pct <= 0]
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0

        # v4.2 修复：统计按仓位加权（原实现每笔简单平均）
        # 问题：一笔 9% 仓位赚 10% 与一笔 30% 仓位赚 1% 对组合的贡献
        # 完全不同，简单平均会高估小额交易的影响。
        # 加权：weight = position_pct（每笔占总资金比例）
        def _wmean(items):
            if not items:
                return 0
            wsum = sum(max(t.position_pct, 0.1) for t in items)  # 仓位下限0.1%防除零
            if wsum <= 0:
                return np.mean([t.pnl_pct for t in items])
            return sum(t.pnl_pct * max(t.position_pct, 0.1) for t in items) / wsum

        avg_win = _wmean(wins)
        avg_loss = _wmean(losses)
        
        # 利润因子（按仓位加权：盈亏金额而非百分比简单加总）
        total_win = sum(t.pnl_pct * max(t.position_pct, 0.1) for t in wins) if wins else 0
        total_loss = sum(abs(t.pnl_pct) * max(t.position_pct, 0.1) for t in losses) if losses else 0
        profit_factor = abs(total_win / total_loss) if total_loss != 0 else float('inf')
        
        # 盈亏比 (期望值)
        if total_trades > 0:
            if avg_loss != 0:
                profit_loss_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else float('inf')
            else:
                profit_loss_ratio = float('inf')
        else:
            profit_loss_ratio = 0
        
        # 最大回撤
        max_drawdown = 0
        if daily_values:
            values = [v['value'] for v in daily_values]
            peak = values[0]
            for v in values:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100
                max_drawdown = max(max_drawdown, dd)
        
        # 夏普比率
        if daily_values and len(daily_values) > 1:
            daily_returns = []
            for i in range(1, len(daily_values)):
                r = daily_values[i]['value'] / daily_values[i-1]['value'] - 1
                daily_returns.append(r)
            if daily_returns:
                mean_daily = np.mean(daily_returns)
                std_daily = np.std(daily_returns)
                sharpe = ((mean_daily - THRESHOLD_BACKTEST_RISK_FREE_RATE/252) / std_daily * np.sqrt(252)) if std_daily > 0 else 0
            else:
                sharpe = 0
        else:
            sharpe = 0
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'profit_loss_ratio': profit_loss_ratio,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe,
            'final_value': final_value,
            'initial_capital': self.initial_capital,
            'years': years,
            'trades': [
                {
                    'entry_date': t.entry_date,
                    'exit_date': t.exit_date,
                    'entry_price': t.entry_price,
                    'exit_price': t.exit_price,
                    'direction': t.direction,
                    'pnl_pct': t.pnl_pct,
                    'hold_days': t.hold_days,
                    'reason_entry': t.reason_entry,
                    'reason_exit': t.reason_exit,
                    'shares': t.shares,
                }
                for t in self.trades
            ],
        }

    def _print_stats(self, stats: Dict):
        """打印回测结果"""
        print(f"\n{'='*50}")
        print(f"  回测结果汇总")
        print(f"{'='*50}")
        print(f"  总收益率:        {stats['total_return']:+.2f}%")
        print(f"  年化收益率:      {stats['annual_return']:+.2f}%")
        print(f"  最终资产:        ¥{stats['final_value']:,.2f}")
        print(f"  回测周期:        {stats['years']:.2f}年")
        print(f"  {'─'*40}")
        print(f"  总交易次数:      {stats['total_trades']}次")
        print(f"  胜率:            {stats['win_rate']:.1f}%")
        print(f"  平均盈利:        {stats['avg_win']:+.2f}%")
        print(f"  平均亏损:        {stats['avg_loss']:+.2f}%")
        print(f"  盈亏比:          {stats['profit_loss_ratio']:.2f}")
        print(f"  利润因子:        {stats['profit_factor']:.2f}")
        print(f"  {'─'*40}")
        print(f"  最大回撤:        {stats['max_drawdown']:.2f}%")
        print(f"  夏普比率:        {stats['sharpe_ratio']:.2f}")
        print(f"{'='*50}")
        
        if self.trades:
            print(f"\n  交易明细(每笔买入分别列出):")
            print(f"  {'买入日':<12} {'卖出日':<12} {'方向':<4} {'买入价':<10} {'卖出价':<10} {'盈亏%':<10} {'股数':<6} {'持仓天':<6} {'理由'}")
            print(f"  {'─'*90}")
            for t in self.trades:
                short_reason = t.reason_entry[:15] if t.reason_entry else '-'
                print(f"  {t.entry_date:<12} {t.exit_date:<12} {'多':<4} ¥{t.entry_price:<7.2f} ¥{t.exit_price:<7.2f} {t.pnl_pct:+.2f}%   {t.shares:<4} {t.hold_days:<5} {short_reason}")


def run_single(symbol: str, name: str, ref_price: float = None,
               start_date: str = "2024-01-01", end_date: str = None,
               capital: float = None,
               quiet: bool = False, analysis_start_date: str = None,
               daily_data=None, m30_data=None,
               daily_analyzer=None, m30_analyzer=None,
               enable_slippage: bool = True,
               tp_multiplier: float = 1.0,
               sell_reduce_pct: float = 0.0) -> Dict:
    """运行单只股票回测，返回统计结果
    
    参数:
        tp_multiplier: 止盈倍数 (默认1.0, slow_bull建议1.67)
        sell_reduce_pct: 卖点减仓比例 (默认0=全清, slow_bull建议0.5)
        daily_data, m30_data: 可选预加载DataFrame（跳过API调用）
        daily_analyzer, m30_analyzer: 可选已分析的ChanLunAnalyzer（跳过结构分析）
        四个都提供时：完全跳过数据加载和分析
    """
    engine = BacktestEngine(symbol, name=name, reference_price=ref_price,
                            initial_capital=capital if capital is not None else BACKTEST_INITIAL_CAPITAL,
                            enable_slippage=enable_slippage,
                            tp_multiplier=tp_multiplier,
                            sell_reduce_pct=sell_reduce_pct)
    stats = engine.run_backtest(start_date=start_date, end_date=end_date,
                                analysis_start_date=analysis_start_date,
                                daily_data=daily_data, m30_data=m30_data,
                                daily_analyzer=daily_analyzer, m30_analyzer=m30_analyzer)
    if stats:
        stats['symbol'] = symbol
        stats['name'] = name
        stats['trade_count'] = len(engine.trades)
        if not quiet:
            print(f"\n[Summary] {name}({symbol}): "
                  f"收益率{stats['total_return']:+.2f}%, "
                  f"胜率{stats['win_rate']:.1f}%, "
                  f"夏普{stats['sharpe_ratio']:.2f}, "
                  f"交易{stats['total_trades']}次")
    return stats


def print_batch_summary(results: List[Dict]):
    """打印批量回测汇总表"""
    print("\n" + "="*90)
    print("  批量回测汇总")
    print("="*90)
    print(f"  {'代码':<8} {'名称':<12} {'总收益率':<12} {'年化':<10} {'胜率':<8} "
          f"{'交易':<6} {'盈亏比':<8} {'夏普':<8} {'最大回撤':<8}")
    print(f"  {'─'*80}")
    
    total_capital = 0
    total_final = 0
    for r in results:
        if not r:
            continue
        ret = r.get('total_return', 0)
        ann = r.get('annual_return', 0)
        wr = r.get('win_rate', 0)
        trades = r.get('total_trades', 0)
        plr = r.get('profit_loss_ratio', 0)
        sharpe = r.get('sharpe_ratio', 0)
        mdd = r.get('max_drawdown', 0)
        print(f"  {r['symbol']:<8} {r['name']:<12} {ret:>+8.2f}%  {ann:>+7.2f}%  "
              f"{wr:>5.1f}%  {trades:>4}次  {plr:>5.2f}  {sharpe:>5.2f}  {mdd:>6.2f}%")
        total_capital += r.get('initial_capital', 2000000)
        total_final += r.get('final_value', 0) or 0
    
    if len(results) > 1:
        total_ret = (total_final / total_capital - 1) * 100 if total_capital > 0 else 0
        print(f"  {'─'*80}")
        print(f"  {'汇总':<20} {total_ret:>+8.2f}%  (总投入¥{total_capital:,.0f} → ¥{total_final:,.0f})")
    print("="*90)


if __name__ == '__main__':
    import sys
    
    # 解析参数
    symbols = []
    ref_prices = {}
    start_date = "2024-01-01"
    quiet = False
    
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--ref' and i + 2 < len(args):
            sym = args[i + 1]
            try:
                ref_prices[sym] = float(args[i + 2])
            except ValueError:
                print(f"[Error] 无效参考价: {args[i + 2]}")
                sys.exit(1)
            i += 3
        elif args[i] == '--start' and i + 1 < len(args):
            start_date = args[i + 1]
            i += 2
        elif args[i] == '--quiet':
            quiet = True
            i += 1
        else:
            symbols.append(args[i])
            i += 1
    
    if not symbols:
        symbols = ['301498']
    
    ref_prices.setdefault('301498', 58.6)
    
    all_results = []
    for sym in symbols:
        ref = ref_prices.get(sym)
        stats = run_single(sym, name=sym, ref_price=ref,
                           start_date=start_date, quiet=quiet)
        if stats:
            all_results.append(stats)
    
    if len(all_results) > 1:
        print_batch_summary(all_results)
