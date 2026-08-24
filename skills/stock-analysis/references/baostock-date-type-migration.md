# Baostock 日期类型变更修复记录（2026-05-15）

## 问题描述

Baostock 返回的 `date` 字段从 `str`（如 `"2026-05-14"`）变更为 `datetime.date` 对象后，`segment_analyzer.py` 和 `generate_analysis.py` 中多个位置因类型不匹配报错。

## 受影响代码模式

### 模式1：`datetime.strptime()` 收到 date 对象而非 str

```python
# ❌ 报错: strptime() argument 1 must be str, not datetime.date
zs_start_dt = datetime.strptime(s1.start_date, "%Y-%m-%d")

# ✅ 修复: 使用 date_utils 的统一解析函数
zs_start_dt = parse_date_to_datetime(s1.start_date)
```

**涉及文件**: `segment_analyzer.py` 第523、537、638行

### 模式2：对 date 对象执行字符串切片

```python
# ❌ 报错: 'datetime.date' object is not subscriptable
reason=f"突破中枢[{zs.start_date[-5:]}~...]"

# ✅ 修复: 先用 date_to_str() 转成字符串
reason=f"突破中枢[{date_to_str(zs.start_date)[-5:]}~...]"
```

**涉及文件**: `segment_analyzer.py` 第782、809行

### 模式3：str vs date 混合比较

```python
# ❌ 报错: '<=' not supported between instances of 'str' and 'datetime.date'
if bi_end and bi_end <= buy_date:

# ✅ 修复: 统一类型
buy_date_str = date_to_str(buy_date)
if bi_end and bi_end <= buy_date_str:
```

**涉及文件**: `generate_analysis.py` 第951行 (`_check_m30_downtrend`)

### 模式4：JSON 序列化时 date 对象不可序列化

```python
# ❌ 报错: Object of type date is not JSON serializable
calibrated_klines.append({'date': k.date, ...})
json.dumps(calibrated_klines)  # 失败

# ✅ 修复: 构建数据时即转为字符串
calibrated_klines.append({'date': date_to_str(k.date), ...})
```

**涉及文件**: `generate_analysis.py` `generate_html()` 方法，涉及 calibrated_klines、calibrated_fenxings、calibrated_bis、calibrated_zhongshus、calibrated_points、macd_data、seg_zhongshus_json、seg_points_json、segments_json、latest_date、start_date 共11处。

## 统一修复原则

`date_utils.py` 提供了两个兼容函数处理所有日期类型：

| 函数 | 用途 | 输入支持 | 输出 |
|:-----|:-----|:---------|:-----|
| `date_to_str(dt)` | 统一转字符串 | str, datetime, date, pd.Timestamp | `"2026-05-14"` |
| `parse_date_to_datetime(dt)` | 统一转 datetime | str, datetime, date, pd.Timestamp | `datetime(2026,5,14)` |

**永远不要直接对 date 字段做以下操作：**
- `datetime.strptime(x.date, fmt)` — 用 `parse_date_to_datetime(x.date)`
- `x.date[-5:]` / `x.date[:10]` — 先用 `date_to_str(x.date)` 再切片
- 将 date 对象直接放入 JSON 结构 — 先用 `date_to_str()` 转换

## 审计清单

修改 `segment_analyzer.py` 或 `generate_analysis.py` 中的日期处理逻辑后，用以下命令验证所有消费者不报错：

```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core

# 测试 segment_analyzer（需要 generate_analysis 配合）
python -c "
from data_manager import DataManager
from generate_analysis import RecursiveTimingSystem
from segment_analyzer import SegmentChanLunAnalyzer
dm = DataManager()
rec = RecursiveTimingSystem(dm)
daily = rec.run_full_analysis('300059')
seg = SegmentChanLunAnalyzer()
seg.analyze(daily)
print('segment_analyzer OK')
"

# 测试 HTML 生成
python quick_html.py 300059 2>&1 | grep '✅'

# 测试 quick_chanlun JSON 输出
python quick_chanlun.py 300059 2>&1 | python -c "import sys,json; d=json.load(sys.stdin); print(f'OK: price={d[\"daily\"][\"current_price\"]}, bis={d[\"daily\"][\"bi_count\"]}')"
```

## 关联文件

- `date_utils.py` — 日期统一处理工具
- `segment_analyzer.py` — 段级别分析（strptime + 切片 修复）
- `generate_analysis.py` — 缠论分析 + HTML可视化（比较 + JSON 序列化 修复）