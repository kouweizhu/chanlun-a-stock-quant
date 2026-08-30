# Alpha Zoo 因子系统 — 技术参考

> 来源：Vibe-Trading (HKUDS/Vibe-Trading, MIT License)
> 验证：CSI300 2018-2025, ~1,940 交易日
> 存活标准：Mean IC > 0.02, t > 2, ≥55% 正向天数

## 4 个 GTJA 幸存因子详解

### gtja191_171 — 最强幸存者 (IC=0.0432, IR=0.2690)

```
公式: -1 * ((close - low) * open^5) / ((close - high) * close^5)
```

**逻辑**：捕捉"开盘冲高后回落"的微观结构形态。收盘靠近最低点 + 开盘价高 → 因子值大（负值绝对值大），预测短期修复。

### gtja191_111 — 量价结构 (IC=0.0349, IR=0.2232)

```
公式: sma(v * ((c-l)-(h-c))/(h-l), 11, 2) - sma(v * ((c-l)-(h-c))/(h-l), 4, 2)
```

**逻辑**：11 天和 4 天量价形态 EMA 的差值。>0 表示形态在改善，<0 表示恶化。类似 MACD 的 DIF 线，但输入是"量×收盘位置"。

### gtja191_054 — 波动率形态 (IC=0.0272, IR=0.1606)

```
公式: -1 * rank(ts_std(|close-open|, 10) + (close-open) + ts_corr(close, open, 10))
```

**逻辑**：捕捉"涨得太顺了警惕回调"。高波动率+大涨+开收盘高度同步 → neg rank → 因子值极负。提示过热。

### gtja191_002 — 日内位置变化 (IC=0.0262, IR=0.1619)

```
公式: -1 * delta(((close-low)-(high-close))/(high-low), 1)
```

**逻辑**：昨天收高位→今天收低位 → delta 负大 → 乘-1 后因子值正大。短期反转信号。

## alpha_score 计算流程

```
候选股 30-140 只
    ↓
DBHub 加载 OHLCV panel（最近 2 年日线）
    ↓
对每只股票计算 4 个因子值（最后一天）
    ↓
每个因子做 cross-sectional percentile rank [0,1]
    ↓
4 个 rank 均值 → scale 到 [0,100]
    ↓
alpha_score
```

## 关键实现细节

```python
# 算子输入/输出格式
# 所有算子操作 "宽表" DataFrame
#   index = 日期 (DatetimeIndex)
#   columns = 股票代码 (str)

# panel 格式
panel = {
    "open":   pd.DataFrame(shape=(days, stocks)),  # 宽表
    "high":   pd.DataFrame(shape=(days, stocks)),
    "low":    pd.DataFrame(shape=(days, stocks)),
    "close":  pd.DataFrame(shape=(days, stocks)),
    "volume": pd.DataFrame(shape=(days, stocks)),
}

# NaN 政策：所有算子自然传播 NaN，无 silent fillna(0)
# Lookahead ban：所有算子禁止使用未来数据
# Inf 禁止：输出含 +/- inf 拒绝
```
