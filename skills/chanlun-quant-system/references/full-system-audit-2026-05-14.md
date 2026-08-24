# 全系统缠论审计报告 — 2026-05-14

审计范围：`generate_analysis.py` + `pool_scanner.py` + `composite_scorer.py`

## 🔴 一级问题（违反缠论定义）

### BUG-1. 一类买卖点结构破坏检查（已修复 ✅ v3.5）
**文件**: `generate_analysis.py:_find_first_class_points()`  
三卖后反弹破坏结构但一买仍产生（沃森生物案例）。新增 `_check_first_buy_structure()` / `_check_first_sell_structure()`。

### BUG-2. 二买/二卖笔终点分型确认（已修复 ✅ v3.5.3）
**文件**: `generate_analysis.py:_find_second_class_points()`  
末笔延伸后取无分型终点的日期做二买（赛力斯案例）。新增分型校验+回溯。

### BUG-3. 笔延伸无分型确认 ⚠️ 根因未修
**文件**: `generate_analysis.py:_extend_last_bi()`  
用极值替代分型确认更新末笔 `end_date`。违反缠论——笔端点必须有分型。  
**影响**: 所有 `bi.end_date` 下游逻辑都可能受影响。消费端已补救（二买/二卖分型回溯）。  
**根因修复建议**: 延伸只更新价格不更新日期（保持原分型确认日期）。

## 🟡 二级问题（理论边界不严谨）

### BUG-4. 三类买卖点遇回踩进中枢后放弃整中枢 ⚠️ 未修
**文件**: `generate_analysis.py:_find_buy_sell_points()` 第822行、第846行  
三买遇回踩进中枢（`pullback_low <= zs.zg`）即 `break`，错过后续更浅回踩产生的有效三买。  
**修复建议**: `break` → `found_up_break = False; pullback_bi = None; continue`  
三卖同理。

### BUG-5. 二买只找第一个回调笔（未修）
**文件**: `generate_analysis.py:_find_second_class_points()`  
只找一买后第一个向下笔，如果该笔回调太深（跌破一买99%），直接跳过不再搜索后续笔。  
**修复建议**: 遍历所有向下笔，取不创新低笔中回调最低点最高的那个。

## 🔵 三级问题（潜在地雷）

### BUG-6. K线合并方向参考用原始K线
**文件**: `generate_analysis.py:_merge_klines()` 第144行  
`ref_kline = klines[ref_index]` 用原始K线而非合并后K线判断方向。极端情况导致方向判断偏差。

### BUG-7. 分型条件过严
**文件**: `generate_analysis.py:_find_fenxings()` 第176-182行  
顶分型要求 `high` 和 `low` 都最高，底分型要求 `high` 和 `low` 都最低。缠论原文顶分型只需 `high` 最高、底分型只需 `low` 最低。**会漏掉有效分型→漏笔→漏买卖点。**  
**修复**: 只检查 `high`（顶）或 `low`（底）。

### BUG-8. 日期字符串比较依赖YYYY-MM-DD格式
多处用字符串比较日期，格式变更会静默出错。

## 当前健康度

| 层级 | 检查项 | 状态 |
|:----|:------|:----:|
| K线合并 | 包含关系处理 | ✅ 基本正确 |
| 分型 | 顶底分型识别 | ⚠️ BUG-7：条件过严 |
| 笔 | 顶底交替、最小K线、分型确认 | ⚠️ BUG-3：延伸无分型 |
| 中枢 | 3笔重叠、延伸、过滤 | ⚠️ BUG-4：break放弃 |
| 一买一卖 | 趋势背驰 | ✅ 已修复 |
| 二买二卖 | 一买后回调不创新低 | ✅ 已修复，另留BUG-5 |
| 三买三卖 | 突破后回踩不进中枢 | ⚠️ BUG-4 |
| 趋势识别 | 中枢非重叠 | ✅ 正确 |

## 修复优先级建议

| 优先级 | Bug | 影响 | 改动量 |
|:-----:|:----|:----|:------:|
| P0 | BUG-3 笔延伸无分型 | 根本性违反缠论 | ~10行 |
| P1 | BUG-4 三买break | 漏有效三买 | ~4行 |
| P1 | BUG-7 分型过严 | 漏分型→漏买卖点 | ~6行 |
| P2 | BUG-5 二买只找第一个回调 | 漏最优二买 | ~15行 |
| P3 | BUG-6 合并方向 | 极端变形 | ~1行 |
