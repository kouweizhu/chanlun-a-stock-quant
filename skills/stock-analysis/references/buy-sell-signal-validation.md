# 买卖点信号有效性校验

> 自动买卖点检测算法可能产生理论误标。本文件描述了如何在评分和否决决策前验证这些信号。

## 一卖被后续新高破坏（中国中免 2026-06-12 案例）

### 问题发现

用户指出：中国中免的技术面分析中，系统标记了2025-09-18一卖@74.06，然后2026-06-05二卖@60.26参照这个一卖。但一卖后价格最高涨到了99.81，缠论铁律——**一卖如果是真正的趋势背驰终点，其后价格不可能创出新高。**

### 数据验证

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
python -c "
import pandas as pd
d = pd.read_parquet('data_cache/601888_daily.parquet')
# 校验一卖@74.06(2025-09-18)是否被后续新高破坏
mask = d['date'] > '2025-09-18'
peak_after = d[mask]['high'].max()
peak_date = d[mask].loc[d[mask]['high'].idxmax(), 'date']
yi_mai_price = 74.06
print(f'一卖后最高价: {peak_after:.2f} @ {peak_date}')
print(f'一卖价格: {yi_mai_price:.2f}')
print(f'差额: {peak_after - yi_mai_price:.2f} ({((peak_after/yi_mai_price)-1)*100:.1f}%)')
print(f'→ 一卖已被破坏' if peak_after > yi_mai_price else '→ 一卖有效')
"
```

输出：
```
一卖后最高价: 99.81 @ 2026-01-20
一卖价格: 74.06
差额: 25.75 (34.8%)
→ 一卖已被破坏
```

### 系统误标的原因推测

`quick_chanlun.py` 的买卖点算法在识别一卖时可能基于局部笔背驰而非完整的趋势背驰判断，导致以下误标：

1. **笔级别误判为趋势级别**：2025-09-18的顶可能只是向上笔内部的盘整背驰或小级别背驰，被算法放大为日线一卖
2. **未考虑后续走势对新高的验证**：算法是静态的（基于当前已有数据），不会在出现新高后自动撤回之前的一卖标记

### 正确的缠论结构

```
真正的顶部 (系统未识别)
   99.81 (2026-01-20) / 98.72 (2026-02-11 笔终点)
         │
         ▼ 暴跌(-44.7%)
     54.56 (2026-05-28)
         │
         ▼ 弱势反弹
     60.26 (2026-06-05)  ← 二卖（相对于真正顶部99.81而言）
         │
         ▼ 继续下行
     58.49 (分析当日)
```

### 校验流程（通用）

对任何买卖点信号，在用于评分/否决前执行以下校验：

#### 一卖有效性校验

```python
# 伪代码
yi_mai_price = buy_sell_point.price
yi_mai_date = buy_sell_point.date
daily_data = get_daily_data(code)

# 检查一卖后是否有任何K线最高价超过一卖价
mask = daily_data['date'] > yi_mai_date
if daily_data[mask]['high'].max() > yi_mai_price * 1.005:  # 5‰容差
    # 一卖被破坏
    signal_valid = False
    actual_top = daily_data[mask].loc[daily_data[mask]['high'].idxmax()]
else:
    signal_valid = True
```

#### 二卖有效性校验

```python
# 先找到二卖参照的一卖
# 从买卖点列表中找时间最近的、在一卖之前的那个一卖
yi_mai = find_previous_sell(buy_sell_point, level=1)

# 校验参照的一卖是否有效
if not validate_yi_mai(yi_mai):
    # 一卖已失效，二卖参照错误
    # 寻找真正的顶部作为替代基准
    actual_top = find_highest_peak_since(yi_mai.date)
    # 如果二卖价格确实低于真正顶部，二卖概念仍然正确
    if buy_sell_point.price < actual_top.price:
        # 二卖概念正确但参照偏移
        mark_as_correct_concept_wrong_reference(buy_sell_point, yi_mai, actual_top)
    else:
        # 二卖本身也有问题
        reject_signal(buy_sell_point)
else:
    # 一卖有效，正常使用
    pass
```

#### 一买有效性校验

对称规则：一买后如果价格创出更低的新低，一买被破坏。

```python
yi_mai_price = buy_sell_point.price
mask = daily_data['date'] > buy_sell_point.date
if daily_data[mask]['low'].min() < yi_mai_price * 0.995:
    # 一买被破坏
    signal_valid = False
```

### 报告中的标注

当发现一卖/一买被破坏时：

| 场景 | 报告标注模板 |
|:----|:------------|
| 一卖被新高破坏 | ⚠️ **信号修正**：系统标记一卖@[价格]([日期])，但后续最高[价格]([日期])已超过一卖价。原一卖被破坏，否决机制不以此为依据。确认真正顶部在@[价格]([日期])。 |
| 二卖参照错误的一卖 | ⚠️ **信号修正**：系统标记二卖@[价格]([日期])参照的一卖@[价格]([日期])已被新高破坏。以实际顶部@[价格]([日期])为基准重新判断，二卖概念仍然成立/不成立。 |
| 一买被新低破坏 | ⚠️ **信号修正**：系统标记一买@[价格]([日期])，但后续最低[价格]([日期])已跌破一买价。原一买被破坏，不计入评分。 |

### 预防措施

1. 在分析报告中加入「买卖点信号有效性校验」子节
2. 对于否决机制中的一卖，必须校验通过后才触发否决
3. 对于评分中的顶部背驰惩罚，使用经过校验的卖点
4. 定期（或在发现误标时）向 `quick_chanlun.py` 算法团队反馈误标案例
