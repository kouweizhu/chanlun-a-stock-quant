# 技术面评分速查表（手工计算用）

> 当 `single_stock_analysis.py --report` 超时需要手工评分时，按此表逐项检查。
> 评分范围：-30 到 100。

## 一、加分项

| # | 条件 | 分值 | 数据来源 |
|:-:|:-----|:----:|:---------|
| 1 | 最新一笔为**向上笔** | +15 | `last_5_bis[-1].direction == "up"` |
| 2 | 最新一笔为**向下笔（不破前低）** | +10 | direction=down 且 end_price > 前一低点 |
| 3 | 价格在**中枢中轴上方**（price > (ZG+ZD)/2） | +15 | `current_price > (zg+zd)/2` |
| 4 | 价格在**中枢中轴下方**（ZD < price < 中轴） | +5 | `zd < current_price < (zg+zd)/2` |
| 5 | **突破中枢上沿**（price > ZG） | +20 | `current_price > zg` |
| 6 | **MACD 金叉**（DIF > DEA） | +10 | `dif_dea == "golden_cross"` |
| 7 | **MACD 趋势向上**（柱值缩小/增大） | +10 | `macd_trend == "up"` |
| 8 | 近1月（≤30天）内有**买点信号** | +25 | `buy_sell_points` 中 type=buy 且距今≤30天 |
| 9 | 近3月（31-90天）内有买点信号 | +15 | 同上，31-90天 |
| 10 | 近半年（91-180天）内有买点信号 | +8 | 同上，91-180天 |
| 11 | 买点**置信度≥4** | +5 | `confidence_score >= 4` |
| 12 | **盘整背驰确认**（最新向下笔MACD面积 < 前一同向笔的70%） | +10 | 手动计算MACD面积比 |

## 二、减分项

| # | 条件 | 分值 | 数据来源 |
|:-:|:-----|:----:|:---------|
| 1 | 最新**向下笔（破前低）** | -3 | direction=down 且 end_price < 前一低点 |
| 2 | **跌破中枢下沿**（price < ZD） | -8 | `current_price < zd` |
| 3 | **连续3笔向下** | -5 | `last_5_bis` 最后3笔均为down |
| 4 | **无中枢结构** | -5 | `zhongshus` 为空 |
| 5 | **顶部背驰显现且未修复** | -8 | MACD柱面积比>70%且价格创新高 |
| 6 | **二卖确认**且价格在二卖下方运行 | -5 | 二卖出现（7-30天内）且 current_price < 二卖价 |
| 7 | **无任何买点信号**（含过期） | -5 | `buy_sell_points` 中无 type=buy |
| 8 | **MACD 死叉**（DIF < DEA） | -5 | `dif_dea == "dead_cross"` |

## 三、盘整背驰检测

比较最近两个**同向向下笔**的 MACD 柱**绝对值面积**：

```python
# 从 data_cache/{code}_daily.parquet 读取数据
import pandas as pd, numpy as np
d = pd.read_parquet(f'data_cache/{code}_daily.parquet')
close = d['close'].astype(float).values
ema_fast = pd.Series(close).ewm(span=12).mean()
ema_slow = pd.Series(close).ewm(span=26).mean()
dif = (ema_fast - ema_slow).values
dea = pd.Series(dif).ewm(span=9).mean().values
macd = 2 * (dif - dea)

# 取两段向下笔的日期区间
area_prev = abs(macd[mask_prev]).sum()
area_latest = abs(macd[mask_latest]).sum()
ratio = area_latest / area_prev * 100

# 判断
if ratio < 70:    # 盘背确认 → +10分，卖点扣分减半至-4
elif ratio < 100:  # 面积缩小但不足
else:              # 无背驰
```

⚠️ **必须用 `.abs().sum()`**，不能用代数和（两段均为负值时会得到错误比值）。

## 四、买点时效性（v5.0 修正）

| 距今天数 | 加分 | 说明 |
|:--------:|:----:|:-----|
| ≤30天 | +25 | 最强买入信号 |
| 31-90天 | +15 | 中期信号 |
| 91-180天 | +8 | 远期信号 |
| >180天 | +0 | **过期，不参与评分** |

## 五、卖点消耗规则

卖点后出现**级别更高的反向买点**（或盘背确认），该卖点被消耗，扣分取消或减半。

## 六、快速计算模板

```python
tech_score = 0

# 检查当前价位置
current_price = ...  # 从 quick_chanlun 输出
zg, zd = ...         # 最新中枢
mid = (zg + zd) / 2

# 位置加分
if current_price > zg:
    tech_score += 20  # 突破上沿
elif current_price > mid:
    tech_score += 15  # 中轴上方
elif current_price > zd:
    tech_score += 5   # 中轴下方
else:
    tech_score -= 8   # 跌破下沿

# 最新笔方向
last_bi = last_5_bis[-1]
if last_bi['direction'] == 'up':
    tech_score += 15
else:
    tech_score -= 3   # 假设破前低，需验证

# MACD
if macd_status['dif_dea'] == 'golden_cross':
    tech_score += 10
elif macd_status['dif_dea'] == 'dead_cross':
    tech_score -= 5

if macd_status['macd_trend'] == 'up':
    tech_score += 10

# 买点时效（从 buy_sell_points 中筛选 type=buy，按距今天数）
for bp in buy_sell_points:
    if bp['type'] == 'buy':
        days_ago = (today - parse(bp['date'])).days
        if days_ago <= 30:    tech_score += 25
        elif days_ago <= 90:  tech_score += 15
        elif days_ago <= 180: tech_score += 8
        # >180: 不加分

# 无买点信号惩罚
if not any(bp['type'] == 'buy' for bp in buy_sell_points):
    tech_score -= 5
```

## 七、2026-06-29 运达股份实例

| 条件 | 得分 |
|:-----|:----:|
| 最新向下笔(破前低) | -3 |
| 跌破中枢下沿(11.75 < 15.54) | -8 |
| MACD死叉 | -5 |
| 无买点信号(最近买点2025-10-20已过期>180天) | -5 |
| **合计** | **-21** |
