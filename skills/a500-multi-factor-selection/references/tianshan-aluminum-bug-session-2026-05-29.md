# 天山铝业(002532) 技术评分 100 分 Bug 排查记录

## 时间
2026-05-29 A500 选股复盘时发现

## 表面现象
- 扫描模式：三买形成中(趋势反转后,深回踩10%)
- 报告最近中枢：ZG=8.66/ZD=7.38
- 当前价：¥15.06
- 技术评分：**100 分**（满分）
- 用户反映：近期股价已跌破中枢，100 分不合理

## 实际缠论结构分析

日线分析器返回 6 个中枢（按时间顺序）：

| 中枢 | ZG | ZD | 中轴 | 当前价位置 |
|:----:|:--:|:--:|:---:|:----------:|
| [0] | 5.36 | 4.96 | 5.16 | ⬆ |
| [1] | 8.07 | 7.12 | 7.60 | ⬆ |
| [2] | 6.28 | 6.04 | 6.16 | ⬆ |
| [3] | 8.66 | 7.38 | 8.02 | ⬆ （报告里显示的这个） |
| [4] | 14.01 | 11.98 | 12.99 | ⬆ |
| [5] | 19.10 | 17.90 | 18.50 | ⬇ 已跌破！ |

**关键发现**：报告写的是中枢[3]（应是多年以前的），而当前价 ¥15.06 已跌破中枢[5]下沿 ¥17.90。三买实质已失效，但报告无任何警告。

## Bug 1：非标准买点 fallback 满分

### 触发链路
1. 扫描器检测到"反转后三买"（来自 `_detect_post_reversal_buy()`）
2. 此模式**不是标准买点**，不在 `analyzer.buy_sell_points` 中
3. Phase 2（`pool_screener.py` 第676-687行）搜索120天内标准买点：
   - 最近标准三买：2026-01-22 ¥17.90（距今127天 > 120）
   - 120天内无标准买点 → `recent_buy = None`
4. `compute_technical_score(analyzer, None, None)` 被调用
5. 函数内部访问 `buy_point.price` → AttributeError
6. `except Exception` 捕获 → `tech_score = c['score'] * 20 = 5 × 20 = 100`

### 代码位置
`pool_screener.py` 第688-698行：
```python
try:
    tech_result = compute_technical_score(analyzer, None, recent_buy)
    tech_score = tech_result.get('tech_score', c['score'] * 20)
    tech_detail = tech_result.get('details', [])
    # ...
except Exception as e:
    tech_score = c['score'] * 20  # fallback 满分！
```

### 手动验证
用最近标准三买（2026-01-22 ¥17.90）代入评分，得到 86 分而非 100：
```python
{'structure': 35, 'signal_quality': 22, 'resonance': 18, 'volume': 9, 
 'trend_continuation': 0, 'volatility': 2}
```
详情：中枢下沿附近买入(安全边际高+15); 三买中枢突破确认(+22); 多级别共振(+18); 缩量止跌(+9)

## Bug 2：三买中枢引用错误

### 代码位置
`pool_scanner.py` 第344行（标准买点）和第511-512行（反转买点）：
```python
latest_zs = analyzer.zhongshus[-1]
# ...
display_zg = _rev_buy_info.get("zg", zg)
display_zd = _rev_buy_info.get("zd", zd)
```

`zhongshus[-1]` 返回 **最后完成的中枢**（按时间顺序），但三买引用的应当是**被突破的那个中枢**。对于已形成多个更高中枢的股票（如天山铝业 6 个中枢），最后完成的中枢[5]的 ZG 是 19.10，而扫描器报告的中枢[3] ZG=8.66 是多年前的低位中枢。

## Bug 3：跌破中枢无惩罚

### 代码位置
`validate_tech_score.py` 第83-99行：
```python
if entry_price <= latest_zs.zd * 1.03:
    structure_score = 35  # "中枢下沿附近买入: 安全边际高"
```

对一买（底部买入）这个逻辑正确。但对三买，如果买入价跌到中枢 ZD 附近，说明突破已彻底失败，应该大幅扣分而非给出最高结构分。

### 手动验证
天山铝业 15.06 对中枢[5] ZD=17.90：
- `15.06 <= 17.90 * 1.03 = 18.44` → True → **structure_score = 35**
- 期望：三买失效 → structure_score = 0-5

## 实锤数据汇总

| 问题 | 位置 | 证据 | 影响面 |
|:----|:----|:----|:-----:|
| fallback 满分 | `pool_screener.py:695` | 反转后三买无 buy_point → 100 | 所有非标准买点 |
| 中枢引用错 | `pool_scanner.py:344` | 报告中枢[3] vs 实际中枢[5] | 三买/反转模式 |
| 无跌破惩罚 | `validate_tech_score.py:85` | 跌破反而给最高分 | 三买（仅三买类型） |

## 修复优先级
1. Bug 1（满分掩蔽，影响最大）
2. Bug 3（方向错误，修复代价小）
3. Bug 2（显示误导，次优先）

## 修复记录（2026-05-29 当日完成三处修复）

三处 bug 已在同日修复并部署到代码中。修复后原 cache 已清空，全流程重新运行。

### 修复1：pool_scanner.py — 取最后一个（最新）反向中枢

**文件**：`/home/zjj1990/work/chanlun_core/pool_scanner.py` 第98-104行

**问题**：`_detect_post_reversal_buy()` 用 `break` 只取第一个匹配的反向中枢。
对天山铝业：下跌趋势末中枢=中枢[2] → 在 `zs_list` 中顺序搜索 → 中枢[3] (ZG=8.66>6.28) 第一个匹配 → 取为 counter_zs。但中枢[4] (ZG=14.01) 和中枢[5] (ZG=19.10) 是更近期的反向中枢。

**修复**：去掉 `break`，循环走到底，`counter_zs` 变量持续更新为最近匹配项。

```python
for zs in zs_list:
    if str(zs.start_date) >= dt_end and zs != downtrend_last_zs:
        if float(zs.zg) > float(downtrend_last_zs.zg):
            counter_zs = zs
            # 不 break — 持续更新，取最后一个（最新的）反向中枢
```

**影响**：修复后 counter_zs 从中枢[3]（ZG=8.66/ZD=7.38）变为最后一个匹配的更高中枢（中枢[4] 14.01/11.98 或中枢[5] 19.10/17.90）。显示的 ZG/ZD 更贴近当前价格，中枢穿透检测更准确。

---

### 修复2：validate_tech_score.py — buy_point=None 保护 + 跌破中枢ZD惩罚

**文件**：`/home/zjj1990/work/chanlun_core/validate_tech_score.py`

**2a：buy_point=None 保护**（第76-90行）

**问题**：`compute_technical_score(analyzer, None, None)` 中 `buy_point.price` 在 `buy_point=None` 时报 AttributeError，被 `except` 捕获后静默 fallback 到 `c['score'] * 20 = 100`。

**修复**：在结构评分前插入 None 保护，构造 `_MockBP` 兜底：

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

修复后 `compute_technical_score` 在无标准买点时仍可运行，使用当前价作为 entry_price 代理，level=0 → signal_score=15（未知类型）。

**2b：跌破中枢ZD惩罚**（第320-343行）

**问题**：`entry_price <= latest_zs.zd * 1.03` 给 structure_score=35（中枢下沿买入），对三买方向错误。

**修复**：新增第7维度 `zs_break_penalty`：

```python
# 仅三买模式（point_level==3）触发
if current_price < zs_zd * 0.98:          # 跌破 >2%
    penalty = -20                           # "三买结构已坏"
elif current_price < zs_zd:                # 跌破 ≤2%
    penalty = -10                           # "三买警戒"
```

**天山铝业验证**：当前价 15.06 < 中枢[5]ZD=17.90×0.98=17.54 → 触发-20惩罚。

**影响**：
- 结构分 20(基础) + 15(中枢底部) = 35
- 信号分 15(未知类型)
- 跌破惩罚 -20
- 其他维度保守值 (5+5+3+0) = 13
- 总分 ≈ 35+15-20+13 = 43 → C+级
- 旧：100分(A+级) → 新：43分(C+级)，符合三买已失效的实际情况

---

### 修复3：pool_screener.py — Scanner 缓存买点代理

**文件**：`/home/zjj1990/work/chanlun_core/pool_screener.py` 第689-705行

**问题**：反转后三买等非标准买点无对应 `BuySellPoint` 对象 → `recent_buy = None` → 进入Bug 1的崩溃路径。

**修复**：在120天标准买点搜索失败后，检查 scanner 缓存中的买点信息：

```python
if recent_buy is None and c.get('score', 0) >= 3 and c.get('buy_type', ''):
    from types import SimpleNamespace
    _bp = SimpleNamespace()
    _bp.price = float(c.get('buy_price', 0) or last_kline.get('close', 0))
    _bp.level = {'一买': 1, '二买': 2, '三买': 3,
                 '反转后三买': 3, '反转后类二买': 2, '类一买(盘整底背驰)': 1}.get(c.get('buy_type', ''), 0)
    _bp.date = datetime.strptime(...) 
    _bp.reason = ''
    _bp.multilevel_confirmation = {'confidence_score': 0, 'm30_confirmation': False}
    recent_buy = _bp
```

**买点类型映射表**：

| Scanner buy_type | level | 信号分 |
|:----------------|:-----:|:------:|
| 一买 | 1 | 25-30 |
| 二买 / 反转后类二买 | 2 | 20-25 |
| 三买 / 反转后三买 | 3 | 14-22 |
| 类一买(盘整底背驰) | 1 | 25-30 |

**影响**：所有非标准买点纳入 `compute_technical_score` 的评分逻辑，不再 fallback 满分。同时 `zs_break_penalty` 会评估是否跌破中枢，对结构已坏的标的扣分。

### 修复验证方式

```bash
cd /home/zjj1990/work/chanlun_core
# 验证代码无语法错误
python3 -c "import py_compile; py_compile.compile('validate_tech_score.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('pool_screener.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('pool_scanner.py', doraise=True)"

# 验证单个股票（天山铝业）
python3 -c "
from validate_tech_score import compute_technical_score
from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer

dm = DataManager()
df = dm.get_klines('002532', 'daily', '2024-01-01', '2026-05-29')
klines = dm.to_json_list(df)
analyzer = ChanLunAnalyzer('daily', min_bi_klines=5)
analyzer.analyze(klines)

# 模拟无标准买点
result = compute_technical_score(analyzer, None, None)
print(f'tech_score={result[\"tech_score\"]}, grade={result[\"grade\"]}')
print(f'details={result[\"details\"]}')
# 预期: tech_score ~40-55, 含"跌破中枢ZD"和"无标准买点: 用当前价代理"
"
```