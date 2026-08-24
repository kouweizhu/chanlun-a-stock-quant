# 基本面5年趋势修正移植方案 & 实施报告

## 背景

`hithink_fundamental.py`（个股分析用）已实现：5年multi_year_data提取 + `trend_direction()` v2趋势判定 + trend_correction评分修正。
`akshare_fundamental.py`（A500系统主数据源）仅取单年快照，趋势修正能力缺失。

## 核心发现

`ak.stock_financial_abstract_ths` 返回的DataFrame已包含116+行、25列的完整多期数据（2020-2026各季度），但代码只取 `df.iloc[-1]`。
**无需新增任何API调用**，只需在已有DataFrame上做过滤提取。

## 4层改动（原始方案）

### Layer 1 — 数据层 (`akshare_fundamental.py`)

在 `get_fundamentals_akshare()` 中，现有获取 `df` 之后，从DataFrame过滤年报并提取5年数据。

### Layer 2 — 评分层 (`quick_fundamental.py`)

新增 `analyze_trend(multi_year_data)` + `trend_direction(v2)` 函数。
修改 `calculate_fundamental_score()` 签名增加可选参数 `multi_year_data=None`。

### Layer 3 — 集成层 (`pool_screener.py`)

评分段传 multi_year_data，候选字典存趋势字段。

### Layer 4 — 报告层 (`pool_screener.py:generate_md_report()`)

基本面详情段新增5年趋势对比表 + trend_correction 行。

---

## 实施结果（2026-05-30）

### 实际改动

**3个文件**，0额外API调用，6个 patch 操作：

1. **akshare_fundamental.py** (1 patch)：在 `get_fundamentals_akshare()` 中已有 `df.iloc[-1]` 之后，新增 `df[df['报告期'].str.contains('-12-31')]` 过滤 + 5年循环提取 → `result["multi_year_data"]`
2. **quick_fundamental.py** (5 patches)：
   - `calculate_fundamental_score()` 签名增加 `multi_year_data=None`，scores 初始化增 `trend_correction`/`trend_correction_detail`
   - 评分段：替换硬编码 `roe_std=None` 为动态计算（multi_year_data≥3年时）
   - total_score 改为 `base_total + trend_correction`（min 100 max 0）
   - 新增 `trend_direction(v2)` 函数（含纯单边/混合方向/幅度优先/近期优先判定树）
   - 新增 `analyze_trend()` 函数（4维度评分 + 综合判定）
3. **pool_screener.py** (3 patches)：
   - 评分调用：`fund_score_obj = calculate_fundamental_score(fund_data, multi_year_data=fund_data.get('multi_year_data'))`
   - 候选字典新增：`trend_correction`, `trend_correction_detail`, `roe_std`, `revenue_volatility`, `multi_year_data`
   - `generate_md_report()`：新增5年趋势表动态构建代码（数据行长序列 + extra_rows统计行）

### 关键修复：trend_direction 分支覆盖

**测试中发现的问题**：`analyze_trend()` 的 ROE 趋势分支只覆盖了 `持续下降/先降后升/持续上升/近期回升/近期下降` 5种方向。但 `trend_direction` 对中炬高新 ROE（17.52→-17.41→44.0→18.24→9.41）返回了 `震荡下降`，导致没有任何条件匹配 → detail 字段留空。

**修复**：4个维度的 if/elif 链都增加了 else fallback：
- ROE: `震荡下降→-1` / `震荡上升→+1` / else→0
- 毛利率: `降→-1, 升→+1, else→0`（通用 fallback）
- 营收: 已有 else→0

### 测试验证（6项全过）

```
测试1: AKShare 多年度数据提取 ✓ (5年完整: 2021-2025)
测试2: 趋势分析 ✓ (ROE=-1, 营收=-2, 毛利率=-1, 负债率=0, 综合=-4)
测试3: 评分含趋势修正 ✓ (total=79, 修正=-4, roe_std=0.220, rev_vol=0.101)
测试4: 无多年度数据时回退 ✓ (trend_correction=0, roe_std=None)
测试5: Baostock fallback ✓ (无multi_year_data, total=62, 趋势=0)
测试6: 报告模板 ✓ (含5年趋势表+ROE(%)+趋势修正, 1287bytes)
```

### 产出报告示例（中炬高新600872）

```
## 📈 基本面详情

### 当前快照
| ROE | 9.4% | 净利率 | 12.8% | PE(TTM) | 25.0 | ...

### 5年趋势
| 指标 | 2021 | 2022 | 2023 | 2024 | 2025 |
| ROE(%)     | 17.5 | -17.4 | 44.0 | 18.2 |  9.4 |
| 营收(亿)   | 51.2 |  53.4 | 51.4 | 55.2 | 42.0 |
| 毛利率(%)  | 34.9 |  31.7 | 32.7 | 39.8 | 39.2 |
| 负债率(%)  | 28.2 |  44.3 | 22.6 | 29.9 | 25.7 |

| ROE标准差   | 22.0% |
| 营收波动率  | 10.1% |
| 趋势修正    | -4分 (趋势走弱) |
```

### 向下兼容验证

- Baostock 路径（`quick_fundamental.get_fundamentals()`）：无 multi_year_data → trend_correction=0, roe_std=None, total=62（与改动前一致）
- 不传 multi_year_data：trend_correction=0
- 空 dict：trend_correction=0

### 注意事项

1. `trend_direction()` 可能返回 `震荡下降/震荡上升/先升后降/先降后升/波动` 等混合方向，所有评分分支必须有 else fallback
2. AKShare DataFrame 的 `报告期` 字段包含 `-12-31`（年报）、`-06-30`（中报）、`-03-31`（季报），过滤条件必须精确
3. ROE标准差用 `statistics.stdev()` 而非 numpy.std()（无需额外依赖）
4. 营收波动率 = 标准差/均值（变异系数）