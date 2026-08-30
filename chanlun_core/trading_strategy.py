import pandas as pd
from date_utils import date_to_str, parse_date_to_datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class TradeSignal:
    action: str  # 'BUY', 'SELL', 'HOLD'
    position_size: float  # 建议仓位 (0.0 to 1.0)
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    urgency: str  # 'HIGH', 'MEDIUM', 'LOW'

class TradingStrategy:
    def __init__(self):
        # v4.2: 仓位参数从 config 读取（原硬编码 0.3/0.4/0.7，且 CONF_MAX_POSITION 从未使用）
        from config_loader import POSITION_HEAVY, POSITION_NORMAL, POSITION_LIGHT
        self.CONF_BASE_POSITION = POSITION_NORMAL   # 基础建仓（强共振）
        self.CONF_ADD_POSITION = POSITION_LIGHT     # 加仓
        self.CONF_MAX_POSITION = POSITION_HEAVY     # 最大仓位（上限约束，供调用方使用）
        self.CONF_BUY_ZONE_DAYS = 120               # 买点时效（自然日）
        self.CONF_SELL_ZONE_DAYS = 30               # 卖点时效（自然日）
        self.CONF_SELL_REDUCE_PCT = 0.5             # 30分钟卖点减仓比例（v4.2 从全清改为减半）
        # v4.2: 市场环境仓位上限（regime cap，读取 market_regime.py 输出）
        # 原实现完全不读 → 防御态（熊市仓位上限低）下实盘仍按满额建仓
        self.regime_cap = self._load_regime_cap()

    def _load_regime_cap(self) -> float:
        """读取大盘仓位上限（防御态下降低实盘建仓比例）"""
        try:
            from pool_screener import get_regime_position_cap
            return get_regime_position_cap()
        except Exception:
            return 1.0  # 读取失败不限制

    def _cap_position(self, size: float) -> float:
        """应用 regime cap：单次建仓不超过 max_position × regime_cap"""
        capped = min(size, self.CONF_MAX_POSITION * self.regime_cap)
        return max(capped, 0.0)

    def _point_age_days(self, point) -> Optional[int]:
        """计算买卖点距今自然日数；解析失败返回 None"""
        try:
            bp_date = datetime.strptime(date_to_str(point.date), '%Y-%m-%d')
            return (datetime.now() - bp_date).days
        except Exception:
            return None

    def _is_valid_buy_point(self, point) -> bool:
        """校验日线买点是否可执行：
        1. 必须是一/二/三类买点（level 1/2/3）
        2. confirmed 必须为 True（潜在一买/等待确认的买点不可执行，v4.2）
        3. 时效内（≤ CONF_BUY_ZONE_DAYS）
        """
        if not point or point.type != 'buy':
            return False
        if point.level not in (1, 2, 3):
            return False
        # ⚠️ v4.2 修复：confirmed=False 的"潜在一买"不可建仓
        # 潜在一买只是背驰条件满足，尚无向上一笔确认，实盘买入=接飞刀
        if getattr(point, 'confirmed', False) is not True:
            return False
        age = self._point_age_days(point)
        if age is None:
            return False  # 日期解析失败 → 不执行（保守）
        return age <= self.CONF_BUY_ZONE_DAYS

    def _is_valid_sell_point(self, point) -> bool:
        """校验卖点是否可执行：时效内（≤ CONF_SELL_ZONE_DAYS）"""
        if not point or point.type != 'sell':
            return False
        age = self._point_age_days(point)
        if age is None:
            return False
        return age <= self.CONF_SELL_ZONE_DAYS

    def generate_signal(self, daily_analyzer, m30_analyzer, current_price):
        daily_bps = daily_analyzer.buy_sell_points
        # 取最近的、可执行的日线买点（v4.2：跳过潜在一买和超时买点）
        latest_daily_buy = None
        for bp in reversed(daily_bps):
            if self._is_valid_buy_point(bp):
                latest_daily_buy = bp
                break
        is_daily_buy_zone = latest_daily_buy is not None

        m30_bps = m30_analyzer.buy_sell_points if m30_analyzer else []
        # 取最近的、可执行的30分钟买点/卖点
        latest_m30_buy = None
        latest_m30_sell = None
        for bp in reversed(m30_bps):
            if bp.type == 'buy' and latest_m30_buy is None and self._is_valid_buy_point(bp):
                latest_m30_buy = bp
            elif bp.type == 'sell' and latest_m30_sell is None and self._is_valid_sell_point(bp):
                latest_m30_sell = bp
            if latest_m30_buy is not None and latest_m30_sell is not None:
                break

        # 条件1: 强共振 — 日线买点(已确认) + 30分钟买点(已确认)同时出现
        if is_daily_buy_zone and latest_m30_buy:
            # 止损：最新30分钟买点价格下方3%
            stop_loss = latest_m30_buy.price * 0.97
            # 止盈：上一个中枢上沿，或当前价上方15%（v4.2 防负止盈：
            # 中枢上沿低于买入价时取 max(中枢上沿, 当前价*1.15)）
            zs_tp = daily_analyzer.zhongshus[-1].zg if daily_analyzer.zhongshus else 0
            take_profit = max(zs_tp, current_price * 1.15)
            return TradeSignal(
                action='BUY',
                position_size=self._cap_position(self.CONF_BASE_POSITION),
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"强共振：日线买点 {latest_daily_buy.reason} + 30min确认 {latest_m30_buy.reason}",
                urgency='HIGH'
            )

        # 条件2: 30分钟三类买点 — 加仓（需已确认+时效内）
        if latest_m30_buy and latest_m30_buy.level == 3:
            zs_tp = daily_analyzer.zhongshus[-1].zg if daily_analyzer.zhongshus else 0
            take_profit = max(zs_tp, current_price * 1.2)
            return TradeSignal(
                action='BUY',
                position_size=self._cap_position(self.CONF_ADD_POSITION),
                entry_price=current_price,
                stop_loss=latest_m30_buy.price,
                take_profit=take_profit,
                reason="30min三类买点确认，趋势延续，建议加仓",
                urgency='MEDIUM'
            )

        # 条件3: 30分钟卖点 — v4.2 分级减仓（从全清改为减半）
        # 缠论：30分钟卖点可能是次级别回调，直接清仓会错过主升。
        # 日线级别卖点才全清；30分钟卖点先减半仓观察。
        if latest_m30_sell:
            return TradeSignal(
                action='SELL',
                position_size=self.CONF_SELL_REDUCE_PCT,
                entry_price=current_price,
                stop_loss=0,
                take_profit=0,
                reason=f"30min压力信号(减半仓): {latest_m30_sell.reason}",
                urgency='HIGH'
            )

        # 条件4: 日线卖点（已确认+时效内）— 全清
        latest_daily_sell = None
        for bp in reversed(daily_bps):
            if self._is_valid_sell_point(bp):
                latest_daily_sell = bp
                break
        if latest_daily_sell:
            return TradeSignal(
                action='SELL',
                position_size=1.0,
                entry_price=current_price,
                stop_loss=0,
                take_profit=0,
                reason=f"日线卖点(全清): {latest_daily_sell.reason}",
                urgency='HIGH'
            )

        # 条件5: 默认持仓不变
        return TradeSignal('HOLD', 0, current_price, 0, 0, "未触发特定买卖点信号", 'LOW')


class FullTradingSystem:
    def __init__(self, data_manager):
        from generate_analysis import RecursiveTimingSystem
        self.dm = data_manager
        self.analyzer_system = RecursiveTimingSystem(data_manager)
        self.strategy = TradingStrategy()

    def execute_for_stock(self, symbol, reference_price=None,
                         daily_analyzer=None, m30_analyzer=None):
        """对单只股票执行完整分析+交易信号生成
        
        参数:
            symbol: 股票代码
            reference_price: 参考价（用于数据缩放）
            daily_analyzer: 可选，预先构建的日线分析器（避免重复API调用）
            m30_analyzer: 可选，预先构建的30分钟分析器
        """
        # 执行多级别分析，返回值是日线分析器
        if daily_analyzer is None:
            daily_analyzer = self.analyzer_system.run_full_analysis(symbol, reference_price=reference_price)
        if m30_analyzer is None:
            m30_analyzer = self.analyzer_system.analyses.get('30min')
        
        if not daily_analyzer:
            return None

        # 获取最新价格（优先从已传入的分析器取）
        if m30_analyzer and m30_analyzer.klines:
            current_price = m30_analyzer.klines[-1].close
        elif daily_analyzer.klines:
            current_price = daily_analyzer.klines[-1].close
        else:
            m30_data = self.dm.get_klines(symbol, level='30min')
            if m30_data.empty:
                daily_data = self.dm.get_klines(symbol, level='daily')
                if daily_data.empty:
                    return None
                current_price = daily_data.iloc[-1]['close']
            else:
                current_price = m30_data.iloc[-1]['close']

        # 生成交易信号
        return self.strategy.generate_signal(daily_analyzer, m30_analyzer, current_price)

    def print_signal_report(self, symbol, name, signal: TradeSignal):
        """打印交易指令单到控制台"""
        if signal is None:
            print(f"\n{'='*45}")
            print(f"  {name}({symbol}) — 交易指令单")
            print(f"{'='*45}")
            print("  ⚠ 分析失败，无法生成指令单")
            print(f"{'='*45}")
            return

        action_emoji = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}
        urgency_color = {'HIGH': '⚠ 高', 'MEDIUM': '● 中', 'LOW': '○ 低'}

        print(f"\n{'='*45}")
        print(f"  {name}({symbol}) — 交易指令单")
        print(f"{'='*45}")
        print(f"  动作: {action_emoji.get(signal.action, '')} {signal.action}")
        print(f"  仓位: {signal.position_size*100:.0f}%")
        print(f"  价格: ¥{signal.entry_price:.2f}")
        if signal.action != 'HOLD':
            print(f"  止损: ¥{signal.stop_loss:.2f} ({(signal.stop_loss/signal.entry_price - 1)*100:.1f}%)")
            print(f"  止盈: ¥{signal.take_profit:.2f} ({(signal.take_profit/signal.entry_price - 1)*100:.1f}%)")
            print(f"  盈亏比: {abs((signal.take_profit - signal.entry_price) / (signal.stop_loss - signal.entry_price)):.2f}" if signal.stop_loss > 0 else "  盈亏比: N/A")
        print(f"  理由: {signal.reason}")
        print(f"  优先级: {urgency_color.get(signal.urgency, '○')}")
        print(f"{'='*45}\n")
