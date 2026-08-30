# 深度审计报告 2026-05-29

## 审计范围
缠论核心引擎 (generate_analysis.py 86KB + segment_analyzer.py 44KB) + A500选股系统 (8个核心文件 ~170KB) + 回测/监控/辅助模块

## 修复的4个问题

### P0-1: 共振惩罚方向错误 — composite_scorer.py:238
```python
# 修复前（错误）：composite += penalty * 0.5
# 修复后（正确）：composite -= penalty * 0.5
```
弱tech(40)+弱fund(40)的股票 composite 从 50.0 降至 37.0。之前反而加分。

### P0-2: 一买进入段选取偏差 — generate_analysis.py:592-595
```python
# 修复前：取最后一个中枢的进入段（盘整背驰）
# 修复后：取趋势第一个中枢的进入段（趋势背驰）
```
缠论原文（第62课）要求：趋势背驰比较的是"趋势第一个中枢的进入段" vs "趋势最后一个中枢的离开段"。
修复后，之前误判的9只假一买（平安银行89.1%、中国平安87.5%等）全部被正确过滤。
对称修复了一类卖点（L676-681）。

### P1-3: 消息面补扫丢失alpha_score — pool_screener.py:571-577
```python
# 修复前：compute_3d_score() 缺少 alpha_score 和 w_alpha 参数
# 修复后：补传 alpha_score=s.get('alpha_score', 50.0), w_alpha=W_ALPHA
```
alpha_score 默认50中性，如果股票有真实alpha评分会被覆盖。

### P1-4: 类一买代理confirmed判断 — pool_screener.py:698
```python
# 修复前：c.get('buy_type', '') != '类一买'  # 精确匹配失败
# 修复后：'类一买' not in c.get('buy_type', '')  # 子串匹配
```
pool_scanner 输出 "类一买(盘整底背驰)" 含括号后缀，!= 精确匹配永远为 True → confirmed 错误为 True。

## 确认正确的部分
- K线包含处理、分型、笔、中枢、三类买卖点、MACD面积 — 全部正确
- config一致性、Alpha截面排名、News降级链、Veto关键词 — 全部正确
- v3.5.5改动（confirmed字段、笔延伸日期同步）— 安全无新bug

## 验证结果
修复后重跑A500全流程（12.1分钟）：
- Top1: 艾力斯 85.1分（A级），Alpha=87.1
- Alpha列不再全是50.0，四维评分真正生效
- "类一买"正确显示在汇总表
- 共振惩罚方向已修正（弱票被扣分而非加分）

## 待处理的P2/P3问题（13项）
详见审计报告完整版：
`/home/zjj1990/work/chanlun_core/审计报告_2026-05-29_深度审计.md`
