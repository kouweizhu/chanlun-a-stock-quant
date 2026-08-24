# 日线合成月线方法 (Daily-to-Monthly Resample)

## 问题背景
`DataManager` 不直接支持月线周期（`frequency='m'` 在 Baostock 中可用，但 `DataManager.get_klines(level='monthly')` 未实现）。直接调用 Baostock 月线接口又可能遇到数据源故障（WSL2 网络限制）。

## 解决方案：日线 → 重采样 → 月线

### 核心步骤
1. **获取日线数据**（DataManager 支持，多源故障转移）
2. **pandas resample 合成月线**（频率字符串 `'ME'` = Month End，pandas 2.x+）
3. **过滤未闭合当月K线**（关键！月线未收盘不参与缠论分析）

### 实现代码
```python
import pandas as pd
from datetime import datetime

def daily_to_monthly(df):
    """日线DataFrame → 月线JSON列表（自动过滤未闭合当月）"""
    if df.empty:
        return []
    
    # 1. 确保date列为datetime类型
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    
    # 2. 按月重采样（pandas 2.x 用 'ME' 不是 'M'）
    monthly = df.resample('ME').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'amount': 'sum',
    }).dropna()
    
    # 3. ⚠️ 关键：过滤未闭合的当月K线
    current_month = datetime.now().strftime('%Y-%m')
    monthly = monthly[monthly.index.strftime('%Y-%m') != current_month]
    
    if monthly.empty:
        return []
    
    # 4. 转回ChanLunAnalyzer需要的JSON格式
    monthly.reset_index(inplace=True)
    monthly['date'] = monthly['date'].dt.strftime('%Y-%m-%d')
    
    klines = []
    for _, row in monthly.iterrows():
        klines.append({
            'date': row['date'],
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': float(row.get('volume', 0)),
            'amount': float(row.get('amount', 0)),
        })
    return klines
```

## 关键注意事项

### 1. pandas 频率字符串（坑！）
| pandas 版本 | 月线频率字符串 | 说明 |
|:----------|:----------------|:------|
| < 2.0 | `'M'` | 旧版 Month End |
| ≥ 2.0 | `'ME'` | 新版 Month End（必须用这个！） |
| ≥ 2.0 | `'M'` | ❌ 报错：`'M' is no longer supported, use 'ME'` |

### 2. 未闭合K线过滤（缠论原则）
- **月线需要整月交易数据才能闭合**（如5月1日-5月31日）
- **未闭合K线的高/低点不稳定**，会导致：
  - 误判顶/底分型
  - 错误标记笔的延伸方向
  - 提前生成买卖点信号（如2026-05-01的虚假卖点）
- **过滤方法**：`monthly[monthly.index.strftime('%Y-%m') != datetime.now().strftime('%Y-%m')]`

### 3. AKShare 指数日线接口
沪深300等指数可用 AKShare 专用接口：
```python
import akshare as ak
df = ak.stock_zh_index_daily(symbol='sh000300')  # 沪深300日线
df['date'] = pd.to_datetime(df['date'])
```
注意：指数无需前复权（`adjustflag` 参数），直接使用即可。

## 应用场景
- `market_regime.py`：沪深300月线缠论分析（已集成）
- 生成月线缠论HTML报告（`generate_hs300_html_v2.py` 已验证）
- 任何需要月线/周线的高级别缠论分析

## 历史经验
- 2026-05-08：首次发现未闭合K线问题，用户指出“5月8日时5月K线还没走完”
- 修正前：`market_regime.py` 误报“卖点2026-05-01”
- 修正后：过滤未闭合当月K线，卖点信号消失，市场判定更严谨
