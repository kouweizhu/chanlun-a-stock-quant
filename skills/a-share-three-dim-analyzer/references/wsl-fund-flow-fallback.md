# WSL 资金面数据回退方案

## 问题

WSL 环境下以下数据源全部 RemoteDisconnected：
- AKShare 的 `stock_individual_fund_flow` 等接口
- push2.eastmoney.com 直连 HTTP API（urllib / httpx 均失败）

根本原因：WSL 的网络栈对东方财富部分 CDN 节点连接不稳定，非代理/频率限制问题。

## 解决方案：mx-data

mx-data skill 的 `get_data.py` 走不同的内部 API 通道，WSL 下稳定可用。

### 查询命令

```bash
cd C:/Users/13120/.agents/skills/mx-data

# 近5日逐日资金流向（含四层结构+DDX）
python scripts/get_data.py --query "安琪酵母 600298 近5个交易日每日主力资金净流入 超大单 大单 中单 小单 DDX"

# DDX/DDY 深度分析
python scripts/get_data.py --query "伊利股份 600887 主力资金净流入 DDX DDY 近20个交易日"

# 指定日期区间
python scripts/get_data.py --query "中炬高新 600872 2026年4月20日到2026年5月14日 每日主力资金净流入 超大单 大单 中单 小单"
```

### 输出解析模板

```python
import openpyxl

wb = openpyxl.load_workbook(xlsx_path, data_only=True)
for sn in wb.sheetnames:
    ws = wb[sn]
    for row in ws.iter_rows(max_row=min(ws.max_row, 15), values_only=False):
        vals = [str(c.value)[:22] if c.value is not None else '' for c in row]
        print('  '.join(vals))
```

### xlsx Sheet 结构

| Sheet 名称模式 | 内容 |
|---|---|
| `XXX的中单净流入量、小单净流入量等` | 逐日四层资金量+金额，完整明细 |
| `XXX的(区间)主力净流入资金` | 逐日主力/超大单/大单/中单/小单净额汇总 |
| `XXX当前的超大单净额` | 当日四层结构 + DDX |
| `XXX当前的主力流出、主力流入` | 当日主力总流入/流出 |

### 关键字段映射

从 Sheet1（逐日明细）提取：
- `主力净流入资金` = 超大单净额 + 大单净额
- `超大单净流入资金` = 机构/大资金方向
- `大单净流入资金` = 大户方向
- `小单净流入资金` = 散户方向
- `DDX` = 当日大单净买入量/流通盘（从 Sheet3 取）

### 多股查询注意

mx-data 在多股查询时可能静默丢弃实体（见 mx-data skill 的 multi-stock-query-verification.md）。每次查询后必须验证 xlsx sheet 名称覆盖了所有请求的股票。缺失的单独重查。
