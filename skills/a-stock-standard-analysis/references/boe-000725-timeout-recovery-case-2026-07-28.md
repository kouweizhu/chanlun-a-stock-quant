# 京东方A(000725) 超时恢复案例 — 2026-07-28

## 现象

`single_stock_analysis.py --code 000725 --name "京东方A" --report` 超时（>3分钟无进程输出）。

## 根因分析

1. **30分钟数据源耗尽**：`.source_failed_000725_30min.flag` 存在，说明 Baostock→efinance→AkShare Sina→AkShare EM 全部失败
2. **HTML 已生成**：`reports_html/000725_chanlun.html` 在 pipeline 早期已成功生成（406KB）
3. **日线缓存已更新**：`data_cache/000725_daily.parquet` 包含 2810 行数据（2015-01-05 至 2026-07-28）

## 恢复流程

1. 终止超时进程（`process(action=kill)`）
2. 检查已有产出：
   - HTML 已存在 → 无需重跑 `quick_html.py`
   - 日线 parquet 已更新 → 无需重跑数据获取
   - 30min 失败标志存在 → 跳过 30min 分析
3. 并行启动 5 个独立脚本（利用已有缓存）：
   ```bash
   python quick_chanlun.py 000725           # 缠论（~10s）
   python hithink_fundamental.py 000725     # 基本面（~30s）
   python news_detail_report.py --code 000725 --name "京东方A"   # 消息面（~45s）
   python check_negative_news.py --stocks 000725 --name "京东方A" --json  # 负面（~60s）
   ```
4. 全部在 60s 内完成

## 关键数据点

| 指标 | 数值 |
|:-----|:-----|
| 当前价 | 5.54 |
| 最新中枢 ZG/ZD | 6.71 / 5.51 |
| MACD | 死叉，柱线收敛 |
| 技术面评分 | 15/100 |
| 基本面评分 | 54/100 |
| 消息面评分 | 54.8/100 |
| 综合评分 | 42.54 |
| 决策 | 回避 |

## 教训

1. **不要无脑重试 `single_stock_analysis.py`**：先检查已有产出，再补跑缺失部分
2. **HTML 报告往往最先完成**：pipeline 早期执行，即使后续超时也有效
3. **30min 数据耗尽是常见超时原因**：与股票复杂度无关，独立风险因素
4. **并行后台恢复效率显著**：60s vs 串行 2-5min
