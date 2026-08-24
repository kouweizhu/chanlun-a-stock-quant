# multi_year_data 查询格式陷阱 & trend_direction v2 修复

## 问题描述（2026-05-30发现）

`hithink_fundamental.py` 的 `multi_year_data` 返回为空字典 `{}`，导致基本面评分中的趋势修正因子永远为0。

## 根因两个

### 根因1：单次查询字段数过多

原代码将45个字段合并为一次查询请求：
```python
query_multi = f"营业收入[{year}];归母净利润[{year}];...（45个字段）"
```

同花顺 hithink-finance-query API 对单次查询的字段数量有限制——超过约20个字段或总字符串长度过长会返回 `status_code=-3001`（参数错误）。

**修复**：分为5次查询，每次9个精确键名：
```python
# 第1批：营收+利润类
query1 = f"营业收入[{date}];营业总收入[{date}];归母净利润[{date}];扣非净利润[{date}];净利润[{date}];基本每股收益[{date}];经营活动现金流净额[{date}];加权净资产收益率[{date}];毛利率[{date}]"
```

### 根因2：查询键名格式错误

原代码使用简写格式：
```python
query_multi = f"2022营收;2022净利润;..."  # 简写格式 - 返回-3001
```

同花顺API要求精确键名格式 `指标名称[报告期]`：
```python
query = f"营业收入[20221231];归母净利润[20221231]"  # ✅ 精确格式
```

- 年份对应报告期日期：`2021-2025` → `20211231`,`20221231`,`20231231`,`20241231`,`20251231`
- 最新季报对应：`20260331` 等

## 修复详情

### 修改1：查询语句重构（hithink_fundamental.py L134-145）

从单次45字段改为5次×9字段分批查询后合并结果字典。

### 修改2：年份范围扩展（L149）

```python
# 旧: [2022, 2023, 2024, 2025]  — 4年
# 新: [2021, 2022, 2023, 2024, 2025]  — 5年
```

同步更新 analyze_trend 循环中的年份列表（L273）。

### 修改3：trend_direction 函数 v2 升级（L287-307）

原逻辑误判问题：毛利率从 34.9→36.4→37.2→39.2% 被判定为"持续下降"。

bug原因：原逻辑判断 `changes[-1] < 0`（最后一期为负）就降级为"持续下降"方向，没有检查整体系列的单调性。

v2修复：
```python
def trend_direction(values, labels):
    # 1. 纯单边判定：只有所有变化方向一致才判"持续上升"/"持续下降"
    down_count = sum(1 for v in changes if v < 0)
    up_count = sum(1 for v in changes if v > 0)
    
    if down_count == len(changes):  # 全部为负 → 持续下降
        ...
    elif up_count == len(changes):  # 全部为正 → 持续上升
        ...
    else:
        # 2. 整体幅度判定：首尾差值>5%差值或超过阈值fabs(首尾/均值)>15%时取主导方向
        total_change = values[-1] - values[0]
        avg = sum(values) / len(values)
        change_ratio = abs(total_change / avg) if avg != 0 else 0
        if change_ratio > 0.15 or abs(total_change) > 5:
            # 幅度显著 → 取主导方向
        else:
            # 幅度不显著 → 波动/震荡
```

## 后续影响

- 趋势修正评分范围：-15 ~ +20
- 如果切换回单次查询（未来优化），需确认API字段数限制是否变化
- AKShare 始终作为双源备份计划的一部分（方案C）