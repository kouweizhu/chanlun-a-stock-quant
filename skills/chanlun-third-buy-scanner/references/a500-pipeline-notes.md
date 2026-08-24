# A500 选股流水线 — 实现笔记

## 文件清单

| 文件 | 行数 | 角色 |
|------|------|------|
| `baostock_utils.py` | ~160 | 共享工具：print重定向(幂等) + to_bs_code + login/logout/ensure_login + query_with_retry |
| `pool_scanner.py` | ~230 | Phase 1：日线 ChanLun 扫描，全买点类型 + 结构位置评分 |
| `pool_screener.py` | ~450 | 主控：Phase 1→2→3 全流程 + 报告生成 |

## 关键设计决策

### 1. 买点评分（Phase 1）

不只看三买，而是覆盖所有买入机会：

- **标准买点**：遍历 `analyzer.buy_sell_points`，取最近 66/120/250 天内的买点，按 level(1/2/3类) 和 recency 评分
- **潜在买点**：无标准买点时，基于中枢位置 + MACD 状态 + 笔方向评分
  - `0.95 ≤ price/ZD ≤ 1.05` + MACD golden → 潜在一买/二买 (score 3)
  - `price > ZG` + 无回踩 → 观察三买 (score 2)
  - 其他结构: score 0-1

### 2. 容错设计（Phase 2）

三层 fallback：
1. `compute_technical_score(analyzer, None, None)` 可能抛异常 → catch，用 `c['score'] * 20` 估算
2. `get_fundamentals(code)` Baostock 瞬断 → catch，fund_score=50 中性
3. `scan_news()` DuckDuckGo 搜索失败 → 返回 50

### 3. Baostock session 管理

统一方案：`baostock_utils.py`
- `login()` 返回 `(bs_module, login_result)`，幂等
- `logout()` 内部 try/except
- `ensure_login()` 先 query_stock_basic 验证 session，失败则重登
- `query_with_retry(query_fn, max_retries=2)` 失败自动重登重试

## 踩过的坑

### 坑1：klines dict vs KLine object
`dm.to_json_list(df)` 返回 `list[dict]`，不是 `list[KLine]`。访问日期用 `klines[-1].get('date')` 而非 `klines[-1].date`。

### 坑2：Baostock "接收数据异常"
快速连续查询多只股票时 Baostock session 可能过期。表现为 `error_code != '0'` 且 error_msg="接收数据异常，请稍后再试。"。解决方案：`query_with_retry` 自动重登重试；`get_fundamentals` 整体 catch + 降级。

### 坑3：行业匹配 "白酒" vs "酒"
Baostock 行业分类是 "C15酒、饮料和精制茶制造业"，不包含"白酒"子串。在 `classify_by_industry` 的 `blue_chip_industries` 中加了"酒"条目（排在"白酒"之后，确保精确匹配优先）。

### 坑4：ROE 显示
Baostock 的 `roeAvg` 是小数（0.34 = 34%），报告中需 `*100` 显示。

### 坑5：quick_html.py 遗漏
`quick_html.py` 是第 4 个曾 monkey-patch print 的文件，本次一并修复。

### 坑6：类一买信号泛滥（v3.6修复）
盘整底背驰（类一买）在趋势下跌中反复触发。根因：`_detect_panbei_divergence()` 只看MACD面积衰竭，不区分"盘整"和"趋势下跌"。修复：增加"中枢下移"检查（最近3个ZD递减），趋势下跌中的类一买降级为 2/1/0 分。典型反例：同仁堂2025年9月后中枢ZD从33.6→31.4→27.5持续下移，修复前score=4进候选，修复后score=2不进候选。详见 `references/leiyimai-signal-quality-fix.md`。

## 测试结果

小样本（3只，2026-04-29）：
- 贵州茅台(600519)：二买(51天前) → 技术75.8 + 基本面68 + 消息50 → 综合65.7(B级) ✓
- 宁德时代(300750)：三买(远期,189天前) → Baostock 瞬断 → 容错降级
- 中国平安(601318)：三买(远期,154天前) → Baostock 瞬断 → 容错降级

## 输出目录结构

```
/mnt/d/常用文件/股票池推荐股/
├── 扫描汇总_YYYY-MM-DD.md
├── 扫描汇总_YYYY-MM-DD.xlsx
├── {股票名}_{代码}/
│   ├── {代码}_chanlun_analysis.html
│   └── {代码}_score_report.md
└── ...
```
