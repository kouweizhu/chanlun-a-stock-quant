# 手动底背驰/顶背驰检查参考

## 适用场景

当 `quick_chanlun.py` / `single_stock_analysis.py` 输出的评分对背驰判断模糊时（如面积比接近1.0），或需要验证系统自动判定的背驰是否准确时，使用本方法手动计算MACD柱面积。

## Python代码模板

```python
import pandas as pd, numpy as np
from data_manager import DataManager

dm = DataManager()
code = '601628'  # 替换为目标股票代码
daily = dm.get_klines(code, 'daily')
if daily is None or len(daily) == 0:
    print('No data')
    exit()

close = daily['close'].values.astype(float)
ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
dif = ema12 - ema26
dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
macd = 2 * (dif - dea)

# 设置要比较的两个向下段区间
mask_a = (daily['date'] >= '2026-04-15') & (daily['date'] <= '2026-04-24')
mask_b = (daily['date'] >= '2026-05-11') & (daily['date'] <= '2026-06-04')

area_a = np.abs(macd[mask_a.values]).sum()
area_b = np.abs(macd[mask_b.values]).sum()

print(f'段A: area={area_a:.4f}')
print(f'段B: area={area_b:.4f}')
print(f'B/A面积比 = {area_b/area_a:.2%}')
print(f'底背驰: {"成立" if area_b < area_a else "不成立"} (需衰减≥30%即<70%才确认)')
```

## 重要参数说明

| 参数 | 值 | 说明 |
|:-----|:---|:-----|
| 衰减阈值 | 0.7 (70%) | 2026-05-31 v4.0 从 1.0 降至 0.7，至少衰减30%才算背驰 |
| MACD参数 | (12, 26, 9) | 标准缠论参数 |
| 复权方式 | 前复权 (adjustflag='2') | Baostock默认使用前复权 |

## 判断逻辑

比较**最近两个同方向**的向下笔的MACD柱**绝对值面积和**：

1. **底背驰（一买）**：价格新低（段B低点 < 段A低点），但段B的MACD柱面积 < 段A的MACD柱面积 × 0.7
2. **顶背驰（一卖）**：价格新高，但MACD柱面积萎缩

## 实战案例：中国人寿 2026-06-10

```
段A (04-15→04-24, 38.31→36.10): area=5.2159
段B (05-11→06-04, 38.60→32.32): area=5.1926
B/A面积比 = 99.55%

判断：面积几乎相等，远未达到30%衰减阈值 → 底背驰不成立
```

## 注意事项

- 区间筛选必须对应缠论划分的完整向下笔（而非任意日期范围）
- 两段均为负值时**必须用 `.abs()` 取绝对值**后再比较（不能用代数和）
- 当前MACD柱=2×(DIF-DEA)，金叉后柱值为正
- 仅适用于**同级别**的两段比较（笔级别对笔级别，段级别对段级别）
