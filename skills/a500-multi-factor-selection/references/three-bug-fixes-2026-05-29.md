# 三买技术评分 100 分 Bug 修复代码参考

> 排查记录见 `references/tianshan-aluminum-bug-session-2026-05-29.md`

## 修复概览

| Bug | 文件 | 行号 | 影响 |
|:----|:----|:----:|:----|
| 非标准买点 fallback 满分 | `pool_screener.py` | 689-705 | 所有非标准买点 |
| 三买中枢引用错误 | `pool_scanner.py` | 98-104 | 反转后三买模式 |
| 跌破中枢无惩罚 | `validate_tech_score.py` | 320-343 | 三买模式 |

## 修复1：pool_scanner.py — 取最后一个反向中枢

**问题**：`_detect_post_reversal_buy()` 用 `break` 只取第一个匹配的反向中枢。
对天山铝业：下跌趋势末中枢=中枢[2] → 中枢[3](ZG=8.66>6.28) 第一个匹配 → `counter_zs=中枢[3]`。但中枢[4](ZG=14.01)和[5](ZG=19.10)是更近期的反向中枢。

**修复代码**（`pool_scanner.py` 第98-104行）：

```python
counter_zs = None
for zs in zs_list:
    if str(zs.start_date) >= dt_end and zs != downtrend_last_zs:
        if float(zs.zg) > float(downtrend_last_zs.zg):
            counter_zs = zs
            # 不 break — 持续更新，取最后一个（最新的）反向中枢作为参考
```

## 修复2a：validate_tech_score.py — buy_point=None 保护

**问题**：`compute_technical_score(analyzer, None, None)` 中 `buy_point.price` 在 `buy_point=None` 时报 AttributeError，被静默 fallback 到 `c['score'] * 20 = 100`。

**修复代码**（`validate_tech_score.py` 第76-90行）：

```python
if buy_point is None:
    class _MockBP:
        price = 0.0
        level = 0
        date = None
        reason = ''
        multilevel_confirmation = {'confidence_score': 0, 'm30_confirmation': False}
    buy_point = _MockBP()
    if daily_analyzer.klines and len(daily_analyzer.klines) > 0:
        buy_point.price = float(daily_analyzer.klines[-1].close)
    details.append("(无标准买点: 用当前价代理)")
```

## 修复2b：validate_tech_score.py — 跌破中枢ZD惩罚

**问题**：`entry_price <= latest_zs.zd * 1.03` 给 structure_score=35（中枢下沿买入），对三买方向错误（三买跌回中枢=突破失败）。

**修复代码**（`validate_tech_score.py` 第320-343行）：

```python
point_level = getattr(buy_point, 'level', 0)
if point_level == 3 and daily_analyzer.zhongshus and daily_analyzer.klines:
    latest_zs = daily_analyzer.zhongshus[-1]
    zs_zd = float(latest_zs.zd)
    current_price = float(daily_analyzer.klines[-1].close)
    if current_price > 0 and current_price < zs_zd * 0.98:
        penalty = -20                         # 跌破 >2% → 结构已坏
    elif current_price > 0 and current_price < zs_zd:
        penalty = -10                         # 跌破 ≤2% → 逼近警戒
scores['zs_break_penalty'] = penalty or 0
```

## 修复3：pool_screener.py — Scanner 缓存买点代理

**问题**：反转后三买等非标准买点无对应 `BuySellPoint` → `recent_buy=None` → 进入 Bug 1 崩溃路径。

**修复代码**（`pool_screener.py` 第689-705行）：

```python
if recent_buy is None and c.get('score', 0) >= 3 and c.get('buy_type', ''):
    from types import SimpleNamespace
    _bp = SimpleNamespace()
    _bp.price = float(c.get('buy_price', 0) or last_kline.get('close', 0))
    _bp.level = {
        '一买': 1, '二买': 2, '三买': 3,
        '反转后三买': 3, '反转后类二买': 2, '类一买(盘整底背驰)': 1
    }.get(c.get('buy_type', ''), 0)
    _bp.date = datetime.strptime(str(c.get('buy_date', ''))[:10], "%Y-%m-%d")
    _bp.reason = ''
    _bp.multilevel_confirmation = {'confidence_score': 0, 'm30_confirmation': False}
    recent_buy = _bp
```

## 验证结果（2026-05-29 18:47）

重新运行全流程后：

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| Top1 | 天山铝业(002532) 91分 ✗ | 艾力斯(688578) 86分 ✅ |
| Top2 | 云铝股份(000807) 89分 ✗ | 恒瑞医药(600276) 83分 ✅ |
| A级(≥70) | 16只 | 24只 |
| 天山铝业排名 | #1 | 消失 ✅ |
| 全流程耗时 | 713s | 780s ✅ |

天山铝业修复后的合理评分：结构35 + 信号15 + 量能5 + 均线3 + 共振5 - 跌破中枢ZD惩罚20 - 波动率0 = 43分(C+级)，符合三买已失效的实际情况。
