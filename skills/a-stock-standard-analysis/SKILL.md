---
name: a-stock-standard-analysis
description: A股三报告标准分析——用户说"分析一下XX股票"时自动触发的完整流程。并行生成三维分析报告（.md + 缠论 .html）+ 基本面深度分析报告（.md），全部保存在同一股票名文件夹下。编排 stock-analysis 和 fundamental-deep-analysis 两个子技能。
category: trading
tags: [a-stock, three-report, full-analysis, orchestrated]
version: 1.0.0
---

# A股三报告标准分析（v1.0）

## 触发条件

用户说以下任意一句时自动触发：
- "分析一下 XX 股票"
- "分析 XX"
- "帮我看看 XX"
- "XX 怎么样"
- 或任何隐含要求完整分析某只A股的表达

## 执行流程（必须并行）

收到请求后，**同时启动两条线**（常见错误：只启动线1忘记线2）：

### 线1：三维分析报告（stock-analysis 技能）
调用 `single_stock_analysis.py --report` 或 5 脚本并行方案（见 stock-analysis 技能 Step 2b）。
生成：
- `{股票名}_{代码}_{日期}.md` — 三维分析（技术+基本面+消息面）
- `{股票名}_{代码}_{日期}_chanlun.html` — 缠论可视化

### 线2：基本面深度分析报告（fundamental-deep-analysis 技能）
加载 `fundamental-deep-analysis` skill。
生成：
- `{股票代码}_{股票简称}_深度分析_{日期}.md` — 基本面深度分析

> ⚠️ **京东方A(000725) 实测（2026-07-28）**：`single_stock_analysis.py --report` 超时（>3分钟无响应），
> 但 HTML 报告已在 pipeline 早期生成。终止后按 5 脚本并行方案恢复，利用已有缓存全部在 60s 内完成。
> 30分钟数据源耗尽（`.source_failed_000725_30min.flag`），小级别分析缺失。

## 统一输出路径

所有三份报告必须保存在**同一文件夹**：

```
D:/常用文件/analysis_reports/{股票名}/
```

执行步骤：
1. 创建目标文件夹（如不存在）：`mkdir -p "D:/常用文件/analysis_reports/{股票名}"`
2. 启动线1 + 线2（并行）
3. 等待全部完成
4. **最终确认**：同一文件夹下存在三份文件（.md + .html + 深度.md）
5. 若任一文件缺失，单独补跑对应脚本

| # | 文件名 | 说明 |
|---|--------|------|
| 1 | `{股票名}_{代码}_{日期}.md` | 三维分析报告 |
| 2 | `{股票名}_{代码}_{日期}_chanlun.html` | 缠论可视化 |
| 3 | `{股票代码}_{股票简称}_深度分析_{日期}.md` | 基本面深度分析 |

## 与子技能的关系

| 技能 | 用途 | 输出 |
|------|------|------|
| `stock-analysis` | 三维分析（技术面+基本面+消息面） | .md + .html |
| `fundamental-deep-analysis` | 基本面深度分析（四层框架） | .md |

**本技能是编排层**，不直接做数据分析。所有具体分析逻辑由子技能完成。

## 已知坑点

1. **single_stock_analysis.py 超时（有条件风险）**：当股票笔数>40**且**30min数据耗尽时大概率超时。**但笔数>40+30min数据成功≠必然超时**——鲁西化工(000830, 50笔/199分型) quick_chanlun 25s+quick_html 16s完成。详见 `references/timeout-bi-count-nuance-2026-07-21.md`。超时后按 stock-analysis 技能 Step 2b 的 5 脚本并行回退方案执行，利用已有缓存。
2. **fundamental-deep-analysis 路径偏好**：该技能默认输出到 `D:/常用文件/基本面深度分析/`，**必须覆盖**为统一输出路径（同一股票名文件夹）。
3. **路径冲突**：`quick_html.py` 先在 `chanlun_core/reports_html/` 生成 HTML，**必须复制**到最终文件夹。
4. **并发执行**：两条线应同时启动，不要串行等待。使用 `terminal(background=true)` 或并行 tool calls。
5. **常见错误：只跑线1不跑线2**：实测京东方A(000725) 分析时，因 single_stock_analysis.py 超时恢复后忘记启动线2（基本面深度分析），导致最终只有2份报告。**必须确保两条线都启动**。
6. **极端高笔数（400+）**：恒瑞医药(600276, 419笔) single_stock_analysis.py 300s超时。恢复：并行5脚本~60s完成。Baostock PE/PB对长期股票仅返回1条数据(2021-08-02)，无法计算历史分位。详见 `a-stock-analysis-gotchas` 技能第9-10章。

## CHANGELOG

| 版本 | 日期 | 变更内容 |
|:----|:----|:---------|
| v1.1 | 2026-07-28 | 京东方A实测：single_stock_analysis.py超时但HTML已生成，5脚本并行恢复。新增"常见错误"提示（只启动线1忘记线2）。 |
| v1.0 | 2026-07-20 | 首次创建。用户指定偏好：所有三份报告保存在同一股票名文件夹下。 |

## 引用

- `stock-analysis` — 三维分析方法论、评分规则、超时恢复
- `fundamental-deep-analysis` — 基本面深度分析四层框架
- `references/boe-000725-timeout-recovery-case-2026-07-28.md` — 京东方A超时恢复完整案例
- `references/timeout-bi-count-nuance-2026-07-21.md` — 笔数与超时风险关系
