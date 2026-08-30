"""
slippage_model.py — 滑点与冲击成本模型

交易执行时，实际成交价 != 信号价（收盘价）。滑点来源于：
1. 买卖价差 (bid-ask spread)
2. 市场冲击 (大单推高/压低价格)
3. 流动性不足

参考：A500 中小盘票 5-21T 短线策略，滑点可能吃掉 10%-30% 理论收益。

用法：
    from slippage_model import SlippageModel
    model = SlippageModel()
    buy_price = signal_price * (1 + model.get_slippage(code, daily_volume_yuan))
    sell_price = signal_price * (1 - model.get_slippage(code, daily_volume_yuan))
"""

import numpy as np
from date_utils import date_to_str, parse_date_to_datetime


class SlippageModel:
    """滑点估算模型
    
    按成交额反比 + 市值分层：
    - 大盘股 (日均成交额 > 50亿):   滑点 0.01%
    - 中盘股 (日均 5-50亿):         滑点 0.05%
    - 小盘股 (日均 < 5亿):          滑点 0.10%
    - 微量 (< 1亿):                滑点 0.20%
    
    公式参考：slippage = min(0.002, 0.1 / avg_daily_volume_yuan)
    即日均成交额 1亿 → 0.1%，100亿 → 0.01%，最大 0.2%
    """

    # 市值分层滑点 (备选方案，按实际成交额自动计算更精确)
    TIER_SLIPPAGE = {
        'large':  0.0001,   # 0.01% — 大盘权重股
        'medium': 0.0005,   # 0.05% — 中等流动性
        'small':  0.0010,   # 0.10% — 小市值
        'micro':  0.0020,   # 0.20% — 流动性差
    }

    def __init__(self, base_slippage: float = 0.0005):
        """
        Args:
            base_slippage: 默认滑点率（无成交量数据时使用），默认 0.05%
        """
        self.base_slippage = base_slippage

    def get_slippage(self, avg_daily_volume_yuan: float = None) -> float:
        """计算单边滑点率
        
        Args:
            avg_daily_volume_yuan: 日均成交额（元），None 时使用默认滑点
        
        Returns:
            滑点率（小数），如 0.001 表示 0.1%
        
        公式: slippage = 0.1 / (avg_daily_volume / 1e8)，即按亿为单位反比
        
        示例:
            日均 100亿 → 0.1/100 = 0.001 (0.10%)
            日均 10亿  → 0.1/10  = 0.010 (1.00%)
            日均 1亿   → 0.1/1   = 0.100 (10.0%) → capped at 0.2%
        
        上限 0.2%（流动性极差），下限 0.01%（流动性极好）
        """
        if avg_daily_volume_yuan is None or avg_daily_volume_yuan <= 0:
            return self.base_slippage

        # 以亿为单位计算反比
        volume_yi = avg_daily_volume_yuan / 100_000_000  # 转为亿
        slippage = 0.1 / volume_yi
        # 上限 0.2%，下限 0.01%
        slippage = max(0.0001, min(0.002, slippage))
        return round(slippage, 6)

    def get_slippage_tier(self, avg_daily_volume_yuan: float) -> str:
        """获取市值分层标签"""
        if avg_daily_volume_yuan >= 5_000_000_000:   # >50亿
            return 'large'
        elif avg_daily_volume_yuan >= 500_000_000:    # 5-50亿
            return 'medium'
        elif avg_daily_volume_yuan >= 100_000_000:    # 1-5亿
            return 'small'
        else:
            return 'micro'

    def apply_buy(self, signal_price: float, avg_daily_volume_yuan: float = None) -> float:
        """买入价（加上滑点：买得更贵）"""
        return signal_price * (1 + self.get_slippage(avg_daily_volume_yuan))

    def apply_sell(self, signal_price: float, avg_daily_volume_yuan: float = None) -> float:
        """卖出价（减去滑点：卖得更便宜）"""
        return signal_price * (1 - self.get_slippage(avg_daily_volume_yuan))

    @staticmethod
    def estimate_avg_daily_volume(daily_data) -> float:
        """从日线 DataFrame 估算近20日均成交额
        
        Args:
            daily_data: DataFrame with 'amount' or 'volume'*'close' columns
        
        Returns:
            日均成交额（元）
        """
        import pandas as pd
        if daily_data is None or len(daily_data) == 0:
            return None
        
        df = daily_data.tail(20)
        
        if 'amount' in df.columns:
            return float(df['amount'].mean())
        
        if 'volume' in df.columns and 'close' in df.columns:
            return float((df['volume'] * df['close']).mean())
        
        return None
