# 反转后买点检测逻辑详解

> 基于 pool_scanner.py `_detect_post_reversal_buy()` 函数的代码审查 (2026-04-30)

## 函数入口条件

```python
def _detect_post_reversal_buy(analyzer, klines) -> tuple:
    # 需要至少 3 个中枢 + 有已识别买点
    if len(zs_list) < 3: return 0, "", None
    if not buy_points: return 0, "", None
```

## 三步检测流程

### Step 1: 定位下跌趋势末端中枢

从后往前遍历中枢序列，找"下移"关系（前中枢 ZD > 后中枢 ZG，或 ZG/ZD 同时下移）。
取下移序列最后一环作为 `downtrend_last_zs`。

### Step 2: 确认反向中枢（趋势反转信号）

在 `downtrend_last_zs.end_date` 之后，找第一个 ZG 更高的中枢 → `counter_zs`。
`ref_zg = counter_zs.zg`, `ref_zd = counter_zs.zd`

### Step 3: 在新结构中寻找买点（三分支）

| 分支 | 条件 | 输出 buy_type | 输出 pattern |
|------|------|---------------|-------------|
| **3a** 确认三买 | counter_zs 后有 level=3 买点，30天内，bp_price ≥ ref_zg×0.95 | `反转后三买` | `三买(趋势反转后,N天前)` |
| **3b** 类二买 | 当前价在 counter_zs 下半区 [ZD, mid] + MACD改善 | `反转后类二买` | `类二买(趋势反转后,中枢下半区+MACD改善)` |
| **3c** 三买形成中 | 当前价 > ref_zg + 最新笔终点 > ref_zg×1.02 + 回踩≥2% | `反转后三买` | `三买形成中(趋势反转后,深/浅/微回踩N%)` |

## 3c 分支回踩分级（重点）

```python
# 前提: current_price > ref_zg AND bi_end > ref_zg * 1.02
pullback_pct = (bi_end - current_price) / bi_end

if pullback_pct >= 0.05:   score = 5  # "深回踩"
elif pullback_pct >= 0.03: score = 4  # "浅回踩"
elif pullback_pct >= 0.02: score = 3  # "微回踩"
else:                       score = 2  # "突破延续"（回踩<2%不算三买形成中）
```

## 调用时机

反转后买点不是独立扫描路径，而是**标准买点被窗口/价格惩罚筛掉后的补充检测**：

```python
# pool_scanner.py 第362-369行
if best_buy is not None and best_score < SCORE_THRESHOLD:
    rev_score, rev_pattern, rev_info = _detect_post_reversal_buy(analyzer, klines)
    if rev_score >= REV_SCORE_THRESHOLD and rev_score > best_score:
        best_score = rev_score    # 替换标准买点
        best_pattern = rev_pattern
        best_buy = None
```

- `SCORE_THRESHOLD = 3`（标准筛选阈值）
- `REV_SCORE_THRESHOLD = 4`（反转后买点更严格，要求 ≥4）

## 已知案例：新城控股 601155 (2026-04-28)

| 项目 | 数值 |
|------|------|
| 现价 | 14.08 |
| 反向中枢 ZG | 13.35 |
| 回踩幅度 | ~6% (深回踩) |
| 走的分支 | 3c |
| tech_score | 100 |
| fund_score | 40 (ROE 1%, PE 48.79, 房地产) |
| composite | 67 (B级, 30%仓位) |

**分析结论**：标注正确，但 6% 深回踩安全垫薄（距 ZG 仅 5.5%），基本面硬伤导致综合评分低。
