---
name: a-share-earnings-alert
description: A股全市场业绩预警监控 - 增量diff，只报告新增数据
author: user
category: trading
tags: [a-share, earnings, alert, akshare, sqlite, incremental]
version: 2.0.0
---

# A股业绩预警监控 v2.0

监控A股全市场业绩预告和业绩快报，**基于SQLite本地缓存做增量diff**，财报季频繁运行时只报告新增的公告。

## 核心机制

```
AKShare全量拉取 → SQLite缓存diff(复合主键) → 只报告新增
                      ↓
              无新增 → 跳过报告(节省token)
              --force-full → 强制生成全量统计
```

**去重策略**：
- 业绩预告：`(股票代码, 公告日期, 预测指标)` 复合主键 — 同一只股票可能有多条不同指标的预告
- 业绩快报：`(股票代码, 公告日期)` 复合主键 — 每只股票每报告期一条

## 功能

1. **自动推断报告期**：根据当前日期自动判断年报/一季报/中报/三季报
2. **增量 diff**：SQLite本地缓存，首次全量导入，后续只处理新增记录
3. **真空期提示**：5/6/11/12月自动提示"业绩真空期"
4. **双数据源**：业绩预告（stock_yjyg_em）+ 业绩快报（stock_yjkb_em）
5. **分类统计**：按预告类型、业绩变动幅度、行业分布统计（始终基于全量缓存）
6. **Markdown报告**：新增明细 + 全量统计

## 报告期规则

| 报告类型 | 披露时间 | AKShare参数 |
|:--------|:--------|:------------|
| 年度报告（年报） | 1月1日 — 4月30日 | YYYY1231 |
| 第一季度报告（一季报） | 4月1日 — 4月30日 | YYYY0331 |
| 半年度报告（中报） | 7月1日 — 8月31日 | YYYY0630 |
| 第三季度报告（三季报） | 10月1日 — 10月31日 | YYYY0930 |

**真空期**：5月、6月、11月、12月（公告数量极少）

## 使用方法

```bash
# 自动推断报告期 + 增量运行（财报季核心用法）
python3 ~/.hermes/skills/a-share-earnings-alert/scripts/earnings_alert.py

# 指定报告期
python3 ~/.hermes/skills/a-share-earnings-alert/scripts/earnings_alert.py --period 20241231

# 清空缓存，全量重新导入
python3 ~/.hermes/skills/a-share-earnings-alert/scripts/earnings_alert.py --reset

# 即使无新增也生成全量报告
python3 ~/.hermes/skills/a-share-earnings-alert/scripts/earnings_alert.py --force-full

# 指定输出目录
python3 ~/.hermes/skills/a-share-earnings-alert/scripts/earnings_alert.py --output /mnt/d/常用文件/业绩预警/
```

## 输出文件

| 文件 | 说明 |
|:-----|:-----|
| `业绩预警报告_YYYYMMDD.md` | Markdown报告（新增明细 + 全量统计） |
| `earnings_cache.db` | SQLite本地缓存（自动增量更新） |

## 报告结构

```
# 标题
## 🆕 本次新增
   - 本次新增 vs 累计缓存数量
   - 新增业绩预告明细表
   - 新增业绩快报明细表
## 📊 全量统计
   - 预告类型分布
   - 业绩变动幅度分布
   - 业绩变动极值
   - 净利润高增长/大幅下降排名
```

## 依赖

- akshare
- pandas
- sqlite3（Python标准库）

## 参考文档

- `references/akshare-columns.md` — AKShare 业绩接口列名、复合主键定义、`所处行业` 列差异陷阱

## 数据源

- 东方财富（通过AKShare，免费）

## 注意事项

1. AKShare每次返回全量数据，增量diff在客户端完成
2. 首次运行自动全量导入（无本地缓存时）
3. 缓存不清空则永久累积，`--reset` 可强制重建
4. 切换到新报告期时，建议 `--reset` 重建缓存
5. 真空期（5/6/11/12月）数据极少，报告会提示
