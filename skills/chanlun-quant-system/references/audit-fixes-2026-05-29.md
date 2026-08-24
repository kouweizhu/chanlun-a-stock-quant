# 缠论系统深度审计修复记录 — 2026-05-29

## 审计范围
缠论核心引擎(generate_analysis.py) + A500选股系统(pool_scanner/pool_screener/validate_tech_score/composite_scorer/alpha_factor_filter) + 回测/监控/辅助模块。总计~170KB代码，3个并行子代理逐行审查。

## 4个已修复的Bug

### P0-1: 共振惩罚方向错误 — composite_scorer.py:238

**根因**：`composite += penalty * 0.5` 应为 `composite -= penalty * 0.5`。

**影响**：tech=40, fund=40 时，弱票composite从43.5被加到50.0（奖励而非惩罚）。评分机制完全反转。

**修复**：1行，`+=` → `-=`。

**验证**：弱票(40+40) composite=37.0（扣6.5分），强票(80+80)=69.5（不扣）。

**教训**：变量名是 `penalty` 但实际效果是加分——命名与逻辑不一致是代码审查的高危信号。

---

### P0-2: 一买进入段选取偏差 — generate_analysis.py:592-595

**根因**：`_find_first_class_points()` 取的是"最后一个中枢的进入段"与离开段比较MACD面积。缠论原文（第62课）要求取"趋势**第一个中枢**的进入段"。

当前实现更接近"盘整背驰"（局部中枢的进入/离开比较），而非"趋势背驰"（趋势首尾中枢比较）。

**影响**：
- 产生大量假一买信号（盘整底背驰被误判为趋势一买）
- 之前标记的9只假一买（平安银行89.1%、中国平安87.5%等）被正确过滤

**修复**：买点和卖点两处对称修改，`last_zs.start_date` → `first_zs.start_date`（`first_zs = trend[0]`）。

**验证**：用divergence_threshold=1.0测试5只之前被误判的股票，全部从"一买"变为"无一买"。用0.7阈值效果更严格。

**教训**：趋势背驰 vs 盘整背驰的区分在于"进入段属于哪个中枢"——这是缠论中最容易混淆的概念之一。

---

### P1-3: 消息面补扫丢失alpha_score — pool_screener.py:571-577

**根因**：`_update_news()` 调用 `compute_3d_score()` 时未传 `alpha_score` 和 `w_alpha`，默认值50.0覆盖了真实的alpha评分。

**影响**：Top30消息面更新后，composite与Phase2不一致（alpha被重置为中性50）。

**修复**：补传 `alpha_score=s.get('alpha_score', 50.0)` 和 `w_alpha=W_ALPHA`。

---

### P1-4: 类一买代理confirmed判断错误 — pool_screener.py:698

**根因**：`_bp.confirmed = c.get('buy_type', '') != '类一买'`。但pool_scanner输出的buy_type是 `"类一买(盘整底背驰)"`（含括号后缀），`!=` 精确匹配永远为True。

**影响**：盘整底背驰通过代理路径获得与标准一买相同的高评分（signal_score多8分）。

**修复**：`!= '类一买'` → `'类一买' not in c.get('buy_type', '')`。

**教训**：字符串匹配时，`!=` 精确匹配 vs `not in` 子串匹配的选择——当上游输出有后缀/变体时，必须用 `not in`。

---

## 审计方法论

并行3个子代理分工：
1. 子代理A：缠论核心引擎（generate_analysis.py 86KB + segment_analyzer.py 44KB）
2. 子代理B：A500选股系统（8个文件 ~170KB）
3. 子代理C：回测/监控/辅助模块（7个文件）

每个子代理对照缠论原文理论逐函数审查，输出结构化的"发现→影响→修复建议"。

## 验证流程

1. 语法检查（py_compile）
2. 导入链完整性（`__import__`）
3. 功能验证（共振惩罚方向、一买信号过滤）
4. 全流程重跑（A500 pipeline 12.1分钟，80只候选，79只推荐）
