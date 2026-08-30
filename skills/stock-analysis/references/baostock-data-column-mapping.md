# Baostock vs 其他数据源 · K线列顺序陷阱

> ⚠️ **日期类型变更（str→datetime.date）** 的兼容处理已迁移至独立的参考文件 `references/baostock-date-type-migration.md`。本文件聚焦列顺序问题。

## 触发场景

当你使用 baostock 获取 K 线数据，但分析代码改编自其他数据源（腾讯财经、AkShare、东方财富等）时。

## 问题现象

不同数据源的列返回顺序不同。如果复用旧代码的列映射而不验证，**不会报错**，但会静默产生错误分析结果（分型、笔、中枢全部错乱）。

## 列顺序对照表

| 索引 | baostock (本技能) | 腾讯财经 | 常见误映射 |
|------|-------------------|----------|-----------|
| k[0] | **date** | **date** | ✓ |
| k[1] | **open** | **open** | ✓ |
| k[2] | **high** | **close** ⚠️ | ❌ 很多人误当作 close |
| k[3] | **low** | **high** ⚠️ | ❌ 误当作 high |
| k[4] | **close** | **low** ⚠️ | ❌ 误当作 low |
| k[5] | **volume** | **volume** | ✓ |

**baostock 查询字段顺序**：`'date,open,high,low,close,volume,amount'` 
→ 返回的 k[0..6] 严格按此顺序

## 标准代码模板

```python
def load_baostock_data(rows):
    """baostock 返回: date,open,high,low,close,volume,amount"""
    data = []
    for k in rows:
        data.append({
            'date': k[0],           # date
            'open': float(k[1]),    # open
            'high': float(k[2]),    # high  ← 注意不是close
            'low': float(k[3]),     # low   ← 注意
            'close': float(k[4]),   # close ← 索引4才是close
            'vol': int(float(k[5])) # volume
        })
    return data
```

## 必做数据完整性检查

加载数据后立即运行（这是抓到映射错误的唯一方法）：

```python
# 打印最后3行原始数据
for row in data[-3:]:
    print(f"{row['date']} O:{row['open']:.2f} H:{row['high']:.2f} "
          f"L:{row['low']:.2f} C:{row['close']:.2f} V:{row['vol']}")

# 验证基本价格关系
for d in data[-10:]:
    assert d['high'] >= d['close'] >= d['low'], \
        f"数据异常! {d['date']}: H={d['high']} < C={d['close']}"
    assert d['open'] >= d['low'] and d['high'] >= d['open'], \
        f"数据异常! {d['date']}: open={d['open']} out of range [{d['low']}, {d['high']}]"
```

如果 `high < close` 或 `low > close`，说明列映射错了，需要修正。

## 要点

- 每次切换数据源必须重新验证列顺序
- 注释标注每个字段的索引来源
- 加载后立即执行 `high >= close >= low` 检查
- Baostock 的 date 字段类型可能随时变更（str ↔ datetime.date），始终用 `date_utils` 统一处理（详见 `references/baostock-date-type-migration.md`）