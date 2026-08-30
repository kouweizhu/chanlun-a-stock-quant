# Excel 持仓表自动检测模式

`position_monitor.py` 中使用的 Excel 读取模式，自动适配有表头/无表头两种格式。

## 问题

用户可能用两种方式填写持仓 Excel：
- **无表头**: 第一行直接是 `002415, 海康威视, 2026-04-15, 185.0, 900, 三买`
- **有表头**: 第一行是 `代码, 名称, 买入日期, 买入价, 股数, 买入理由`

Pandas `read_excel` 默认会把第一行当表头，导致无表头格式的第一条记录丢失。

## 自动检测模式

```python
import pandas as pd

df = pd.read_excel(path, header=None, dtype={0: str})
#                                         ↑ 关键：代码列强制字符串，
#                                         避免 "002415" → 2415

# 智能检测表头
first_val = str(df.iloc[0, 0]).strip()
has_header = not (first_val.isdigit() and 4 <= len(first_val) <= 6)

if has_header:
    headers = [str(c).strip() for c in df.iloc[0]]
    df = df.iloc[1:]
    df.columns = headers
else:
    default_cols = ['code', 'name', 'entry_date', 'entry_price', 'shares', 'reason']
    df.columns = default_cols[:df.shape[1]]

# 列名标准化（兼容中英文）
col_map = {
    '代码': 'code', '品种代码': 'code', 'stock_code': 'code', 'code': 'code',
    '名称': 'name', '品种简称': 'name', 'stock_name': 'name', 'name': 'name',
}
rename_map = {c: col_map[c] for c in df.columns if c in col_map}
df = df.rename(columns=rename_map)

# 代码统一补零到 6 位
df['code'] = df['code'].astype(str).str.replace(r'\.(SZ|SH)$', '', regex=True).str.zfill(6)
```

## 关键点

1. **`dtype={0: str}`** — 防止 Pandas 把 `002415` 读成 `2415`（丢失前导零）
2. **检测阈值**: `4 <= len(first_val) <= 6` — A股代码 4-6 位均可识别
3. **列名标准化**: 同时兼容 `代码` / `品种代码` / `stock_code` / `code`
4. **代码规范化**: 去后缀 (`.SZ`/`.SH`) + zfill(6)

## 适用场景

任何需要用户自由维护的 Excel 持仓/自选股文件的 Python 脚本都可复用此模式。
