# 类一买评分：潜在一买(confirmed=False)

## 背景

v3.5.5 新增 `BuySellPoint.confirmed` 字段：一买（level=1）检测到趋势背驰但尚无向上一笔确认时 → `confirmed=False`（潜在一买）。HTML 已正确显示"潜B1?"（空心圈标签），但 MD/XLSX 汇总表及技术评分尚未区分。

v3.5.6 修复：三处代码同步感知 `confirmed`。

## 影响范围

| 文件 | 改动 | 影响 |
|:----|:-----|:-----|
| `pool_scanner.py` L518-521 | `best_buy.level==1` + `confirmed=False` → `buy_type_str="类一买"`, `best_score -= 1` | 扫描评分降1分, buy_type 字段 |
| `pool_scanner.py` L403 | `bp.level==1` + `confirmed=False` → `level_name_display="类一买"` | pattern 字段（汇总表模式列） |
| `validate_tech_score.py` L131-155 | `confirmed=False` → signal_quality 降8分, `point_type_str="类一买"` | 技术评分降8-10分 |
| `pool_screener.py` L697-698 | 映射表加 `'类一买': 1`, proxy 加 `confirmed` | 买点代理 fallback 正确 |

## 验证方法

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
# 检查汇总 MD 的模式列
grep "类一买" D:/常用文件/股票池推荐股/扫描汇总_*-05-29.md
# 检查 phase2_results 中的 buy_type
python -c "import json; d=json.load(open('.phase2_results.json')); print([(s['code'],s['buy_type'],s['pattern']) for s in d[:30] if '类一买' in s.get('buy_type','')])"
# 对比类一买 vs 一买的技术分
python -c "import json; d=json.load(open('.phase2_results.json')); print('类一买均分:', sum(s['tech_score'] for s in d[:30] if s.get('buy_type')=='类一买')/max(1,sum(1 for s in d[:30] if s.get('buy_type')=='类一买'))); print('一买均分:', sum(s['tech_score'] for s in d[:30] if s.get('buy_type')=='一买')/max(1,sum(1 for s in d[:30] if s.get('buy_type')=='一买')))"
```

## 注意事项

- 修复后需**重跑 pool_scanner.py + pool_screener.py --from-cache** 才能生效
- 汇总MD表"模式"列用 `s['pattern']` 字段，**不是** `s['buy_type']`
- 个股报告"买点类型"行用 `s['buy_type']` 字段
- 两类类一买在系统中并存：
  1. `"类一买"` — 来自潜在一买（confirmed=False的level=1一买）
  2. `"类一买(盘整底背驰)"` — 来自 `_detect_panbei_divergence()` 的独立检测
