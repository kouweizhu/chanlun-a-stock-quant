# 沪深300月线缠论分析 HTML 报告生成经验（2026-05-08）

## 背景
用户要求生成沪深300月线缠论分析HTML报告，以便直观理解月线卖点判定依据。

## 关键问题与解决方案

### 问题1：DataManager 不直接支持月线
- `dm.get_klines(symbol, level='monthly')` 失败：Baostock/eFinance/AKShare Sina/AKShare EM 全部不支持直接拉月线
- 错误信息：`所有数据源均失败: 000300 monthly`
- **解决方案**：用 AKShare `stock_zh_index_daily` 获取日线 → pandas 按月重采样合成月线

### 问题2：pandas 2.x 频率参数变更
- 原代码：`df.resample('M')` → 报错 `ValueError: 'M' is no longer supported`
- **修复**：改用 `'ME'`（月末频率，pandas 2.0+ 新标准）

### 问题3：KLine 不接受 `code` 字段
- 传递给 `ChanLunAnalyzer.analyze()` 的字典不能包含 `code`、`amount` 等 KLine 类未定义的字段
- **修复**：只保留 `date/open/high/low/close/volume` 五个字段

### 问题4：合成月线的中枢数量偏少
- 89根月K线（2019-2026）只识别出1个中枢（长期横盘中枢）
- 这是正常现象：月线级别中枢需要至少3笔重叠，5年数据量有限

## 成功路径（最终方案）

```python
# 1. 用 AKShare 获取沪深300日线（指数专用接口）
df = ak.stock_zh_index_daily(symbol='sh000300')

# 2. 日线 → 月线重采样
df['date'] = pd.to_datetime(df['date'])
df.set_index('date', inplace=True)
monthly = df.resample('ME').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last',
    'volume': 'sum'
}).dropna()

# 3. 转 JSON 列表（仅保留 KLine 接受的字段）
klines = []
for _, row in monthly.iterrows():
    klines.append({
        'date': row['date'].strftime('%Y-%m-%d'),
        'open': float(row['open']),
        'high': float(row['high']),
        'low': float(row['low']),
        'close': float(row['close']),
        'volume': float(row.get('volume', 0)),
    })

# 4. 缠论分析 + HTML 生成
analyzer = ChanLunAnalyzer(level='monthly')
analyzer.analyze(klines)
viz = HTMLVisualizer(symbol='000300', name='沪深300', analyzer=analyzer)
viz.generate_html(output_path)
```

## 月线分析结论（2026-05-08）

- **末笔方向**：向上（2025-04 → 2026-05，3514 → 4901，+39.4%）
- **中枢数量**：1个（2019-04 ~ 2026-05，ZG=4126 / ZD=3503）
- **当前位置**：价格 4901 远在中枢上方（ZG=4126），属“脱离中枢”状态
- **卖点依据**：2026-05-01 形成顶分型 + 末笔延伸4个月，缠论视为“上涨动能衰竭”信号

## 用户困惑：为什么“走势健康”但出卖点？

这是缠论系统的**保守特性**：
- 缠论卖点是**结构信号**，不是“价格立刻跌”的确认
- 即使月线还在收阳、价格仍在高位，只要：
  1. 顶分型成立（3根K线：中K线高点最高、低点最高）
  2. 离开中枢的笔出现背驰（MACD柱面积缩小）
- 系统就会触发卖点，并**持续4-6个月维持偏空判定**

用户看到的是“价格表象”，缠论看的是“结构本质”。

## 输出文件
`/mnt/d/常用文件/缠论分析/2026-05-08_沪深300月线缠论分析.html`

## 教训
1. 不要试图让 DataManager 支持月线——直接调 AKShare 指数接口更可靠
2. pandas 版本升级后，`'M'` → `'ME'` 是必须要注意的坑
3. 生成报告前先用 `market_regime.py` 验证结论，确保解释有据可依