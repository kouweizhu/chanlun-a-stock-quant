# 深度审计记录 2026-05-29

## 审计方法
- 3个并行子代理分别审查：缠论核心引擎、A500选股系统、回测/监控/辅助模块
- 总计审查 ~170KB 核心代码（70个Python文件）
- 每个子代理对照缠论原文理论逐函数检查

## 发现的4个关键bug

### P0-1: 共振惩罚方向错误 — composite_scorer.py:238
```python
# 错误：composite += penalty * 0.5（弱票被奖励）
# 修复：composite -= penalty * 0.5（弱票被惩罚）
```
验证：tech=40, fund=40 → composite从50.0降至37.0（扣6.5分）

### P0-2: 一买进入段选取偏差 — generate_analysis.py:592-595, 676-681
```python
# 错误：取最后一个中枢的进入段（盘整背驰）
# 修复：取趋势第一个中枢的进入段（趋势背驰）
# 对称修复：一类买点和一类卖点都改
```
验证：5只假一买（平安银行89.1%、中国平安87.5%等）全部被正确过滤

### P1-3: 消息面补扫丢失alpha_score — pool_screener.py:571-577
```python
# 错误：compute_3d_score()未传alpha_score和w_alpha
# 修复：补传 alpha_score=s.get('alpha_score', 50.0), w_alpha=W_ALPHA
```

### P1-4: 类一买代理confirmed判断 — pool_screener.py:698
```python
# 错误：c.get('buy_type', '') != '类一买'（精确匹配，但实际是"类一买(盘整底背驰)"）
# 修复：'类一买' not in c.get('buy_type', '')
```

## 修复后全流程验证结果
- 总耗时：12.1分钟
- 候选股：80只，推荐79只
- Top1: 艾力斯(688578) 85.1分 A级
- Alpha列正确显示（恒瑞93.8、艾力87.1、泸州90.6）
- 类一买正确区分（模式列显示"类一买"而非"一买"）
- 假一买全部消失（平安银行、中国平安等不再出现）

## 审计报告路径
/home/zjj1990/work/chanlun_core/审计报告_2026-05-29_深度审计.md
