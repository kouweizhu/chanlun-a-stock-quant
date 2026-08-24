# hithink_fundamental.py 数据格式不一致陷阱

> **⚠️ v5.3.4(2026-08-23) 契约已统一**：三级数据源重构审计（P0-2/P0-5/B2）发现 L1 存小数、L2/L3 的 multi_year 存百分数——同一股票跨层级评分/趋势文字不可比（趋势文字曾显示"ROE从0.1%"）。现已将 `multi_year_data` 全部比率字段（roe/gp_margin/np_margin/liability/revenue_yoy/profit_yoy）统一为**小数**，下表"百分数"列仅适用于 v5.3.4 之前的缓存数据。例外：`growth.revenue_yoy_pct/profit_yoy_pct` 展示键仍为百分点。

## 问题（历史）

同花顺 API（`hithink_fundamental.py`）返回的财务数据中，**多年度数据**（`multi_year_data`）和**最新季度数据**（`profitability` 顶层字段）的格式不一致：

| 字段 | multi_year_data | profitability（最新期） |
|:-----|:---------------:|:----------------------:|
| `roe` | 百分数（如 15.3 = 15.3%） | 小数（如 0.0569 = 5.69%） |
| `gpMargin` | 百分数（如 36.8 = 36.8%） | 小数（如 0.427 = 42.7%） |
| `npMargin` | 百分数（如 11.8 = 11.8%） | 小数（如 0.178 = 17.8%） |
| `liabilityToAsset` | 百分数（如 47.8 = 47.8%） | 小数（如 0.376 = 37.6%） |

## 影响

在 `generate_report.py` 中生成财务表格和季报点评时，如果直接把 `profitability` 的值当百分数格式化（`f"{v:.1f}%"`），0.427 会显示为 `0.4%` 而非正确的 `42.7%`。

## 修复方式

在格式化函数中**自动检测**值的量级：

```python
def _fp(v):
    """格式化百分比——自动检测小数 vs 百分数"""
    if v is not None:
        try:
            v = float(v)
            # 绝对值 <= 1 且不为 0 → 是小数形式（如 0.427），乘以 100
            if abs(v) <= 1 and v != 0:
                v = v * 100
        except (ValueError, TypeError):
            pass
    return f"{v:.1f}%" if v is not None else "—"
```

**边界情况**：真实的个位数百分比（如 ROE=0.5%）会被误乘为 50%。但 A 股中 ROE/毛利率在 0-1% 之间的案例极少，且这种极低值无论显示 0.5% 还是 50% 都不改变"极差"的判断，所以此近似可接受。

## 涉及位置

- `generate_report.py` 的 `_fp()` 函数（财务表格）
- `generate_report.py` 的 `_qfp()` 函数（季报点评）
- `generate_report.py` 的 `_fe()` 函数（营收数值，也做了绝对值适配）

## 避免再次踩坑

新增使用 `hithink_fundamental.py` 输出数据的脚本时，务必对所有从 `profitability`/`health` 顶层字段提取的百分比值应用此检测。v5.3.4 起 `multi_year_data` 已统一为小数格式——展示端同样需要 ×100（或复用 `_fp` 自动检测），不要再按旧的"multi_year=百分数无需处理"约定处理。
