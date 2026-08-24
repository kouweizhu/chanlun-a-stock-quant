# 一买/二买跌破买点惩罚 分析与修复

## 背景

2026-06-10 A500 选股结果中，用户发现多只股票（惠泰医疗、华海药业、吉祥航空、视源股份、长春高新）在股价已跌破买点的情况下技术评分仍然很高。

## 数据验证

| 股票 | 买点类型 | 买价 | 现价 | 跌幅 | 旧技术分 | 等级 |
|------|--------|:----:|:----:|:---:|:-------:|:----:|
| 长春高新(000661) | 二买(43天前) | 84.58 | 66.21 | -21.7% | 74 | A |
| 惠泰医疗(688617) | 二买(34天前) | 227.46 | 194.60 | -14.4% | 70 | A |
| 视源股份(002841) | 二买(14天前) | 38.10 | 34.85 | -8.5% | 52 | B |
| 华海药业(600521) | 二买(43天前) | 15.53 | 14.74 | -5.1% | 76 | A |
| 吉祥航空(603885) | 二买(22天前) | 10.60 | 10.53 | -0.7% | 70 | A |

## 根因分析

`validate_tech_score.py` 的 `compute_technical_score()` 原有 7 个评分维度：

| # | 维度 | 满分 | 三买保护 | 一买/二买保护 |
|:-:|------|:---:|:-------:|:------------:|
| 1 | 趋势结构 | 40 | ✅ | ✅ |
| 2 | 信号质量 | 30 | ✅ | ✅ |
| 3 | 多级别共振 | 20 | ✅ | ✅ |
| 4 | 量价辅助 | 10 | ✅ | ✅ |
| 5 | 波动率 | 5 | ✅ | ✅ |
| 6 | 中枢跌破惩罚(zs_break_penalty) | -20~0 | ✅ 有 | ❌ **无** |
| 7 | 波动率调整 | 5 | ✅ | ✅ |

维度 6 仅对三买检查「跌破中枢 ZD」，一买/二买完全没有「跌破买点」的保护。

## 修复

在 `validate_tech_score.py` 第 345-368 行的 `zs_break_penalty` 后，新增维度 8 `buy_price_penalty`：

```python
# ── 8. 跌破买点惩罚（v1.6 新增，一买/二买）──
buy_price_penalty = 0
try:
    point_level = getattr(buy_point, 'level', 0)
    if point_level in (1, 2) and hasattr(buy_point, 'price') and buy_point.price > 0 \
            and daily_analyzer.klines and len(daily_analyzer.klines) > 0:
        current_price = float(daily_analyzer.klines[-1].close)
        buy_price = float(buy_point.price)
        if current_price > 0 and current_price < buy_price * 0.98:
            pct_drop = (buy_price - current_price) / buy_price * 100
            if point_level == 1:
                # 一买：底部确认失败，惩罚更重
                if pct_drop > 10: penalty = -30
                elif pct_drop > 5: penalty = -20
                else: penalty = -10
            else:
                # 二买：回调过深，结构可能破坏
                if pct_drop > 10: penalty = -20
                elif pct_drop > 5: penalty = -15
                else: penalty = -10
            buy_price_penalty = penalty
            details.append(f"跌破买价¥{buy_price:.2f}(当前¥{current_price:.2f}, -{pct_drop:.1f}%): {penalty})")
        elif current_price > 0 and current_price < buy_price:
            buy_price_penalty = -5
            details.append(f"略低于买价¥{buy_price:.2f}(当前¥{current_price:.2f}): 买点边缘(-5)")
except Exception as e:
    print(f'[validate_tech_score] 警告: 跌破买点检查失败: {e}')
scores['buy_price_penalty'] = buy_price_penalty
```

## 验证

2026-06-10 实盘数据验证结果（用真实 ChanLunAnalyzer 重建分析器后评分）：

| 股票 | 结构分 | 信号质 | 量价 | 买点惩罚 | 总分 | 等级 |
|------|:-----:|:-----:|:---:|:-------:|:----:|:----:|
| 惠泰医疗 | 35 | 25 | 5 | **-20** | 50 | C+ |
| 华海药业 | 35 | 25 | 5 | **-15** | 55 | B |
| 吉祥航空 | 35 | 25 | 5 | **-5** | 65 | B+ |
| 长春高新 | 35 | 25 | 5 | **-20** | 50 | C+ |
| 视源股份 | 18 | 20 | 5 | **-15** | 33 | C |

长春高新 -21.7% 仅扣 -20（二买上限）而非 -30，因为二买结构破坏的容忍度略高于一买。

## 评分维度总览（修复后）

| # | 维度 | 满分 | 保护范围 |
|:-:|------|:---:|---------|
| 1 | 趋势结构 | 40 | 全买点类型 |
| 2 | 信号质量 | 30 | 全买点类型 |
| 3 | 多级别共振 | 20 | 全买点类型 |
| 4 | 量价辅助 | 10 | 全买点类型 |
| 5 | 波动率 | 5 | 全买点类型 |
| 6 | 中枢跌破(zs_break） | -20~0 | 三买 |
| 7 | 波动率调整 | 5 | 全买点类型 |
| **8** | **跌破买点(buy_price）** | **-30~0** | **一买/二买** |