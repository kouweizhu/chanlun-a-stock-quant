# AKShare 接口字段映射与坑位记录
# 来源：2026-05-06 FinGenius 便携工具移植实战

## 一、股票基本信息接口

### `ak.stock_individual_info_em(symbol)`
- **用途**：获取单只股票基本信息（推荐，速度快）
- **关键字段**：
  - `最新` → **当前价格**（⚠️ 不是 "最新价" 或 "收盘价"）
  - `股票简称` → 股票名称
  - `总市值` → 总市值（单位：元，需 ÷1亿 转换为亿）
  - `动态市盈率` → PE
  - `行业` → 所属行业
- **坑**：字段名 `最新` 容易写错成 `最新价`，导致 key 未命中，返回 0
- **坑2**：市值原始值单位为元，需手动转换：`float(总市值) / 1_0000_0000` 得到亿

### `ak.stock_zh_a_spot_em()`
- **用途**：全量A股实时行情
- **⚠️ 性能警告**：下载全部A股（约5300只），耗时约50-60秒
- **建议**：仅在需要全市场统计（涨跌家数、平均涨跌幅）时使用
- **替代方案**：单股查询用 `stock_individual_info_em`

## 二、筹码分布接口

### `ak.stock_cyq_em(symbol, adjust)`
- **用途**：筹码分布原始数据（第一层数据源）
- **返回字段**：日期、价格、成交量、成交额、筹码比例
- **注意**：`symbol` 参数不接受带前缀的代码（如 sh/sz/bj），需要先清洗

### 回退：`ak.stock_zh_a_hist(symbol, period, start_date, end_date, adjust)`
- **用途**：历史行情估算筹码（第二层数据源）
- **估算公式**：`筹码比例 = min(换手率 × 0.1, 10.0)`

## 三、资金流向接口

### `ak.stock_individual_fund_flow(stock, market)`
- **用途**：个股资金流向（主力/散户/超大单）
- **参数**：`market="sh"` 或 `market="sz"`
- **关键字段**：`主力净流入-净额`

### `ak.stock_market_fund_flow()`
- **用途**：全市场资金流向
- **注意**：无参数，返回最近N天数据

## 四、龙虎榜接口（⚠️ 当前版本全部问题）

| 接口 | 错误 | 状态 |
|:-----|:-----|:-----|
| `ak.stock_lhb_detail_em(start_date, end_date)` | `TypeError: 'NoneType' object is not subscriptable` | ❌ |
| `ak.stock_lhb_detail_daily_sina(date)` | `KeyError: '股票代码'` | ❌ |
| `ef.stock.get_daily_billboard(start_date, end_date)` | `TypeError: 'module' object is not callable` | ❌ |
| `ak.stock_lhb_yybph_em()` | — | ✅ 降级可用 |

### `ak.stock_lhb_yybph_em()` — ✅ 可用（降级替代）
- **用途**：龙虎榜营业部排行
- **限制**：返回营业部排行而非个股龙虎榜，仅能判断整体活跃度

## 五、板块/情绪接口

### `ak.stock_board_concept_name_em()`
- **用途**：概念板块列表

### `ak.stock_board_industry_name_em()`
- **用途**：行业板块列表（也用于计算市场广度）

## 六、三层回退模式（通用模板）

```python
def _get_data_with_fallback(code, days):
    # 第一层：东方财富专用接口
    try:
        df = ak.some_em_interface(symbol=code)
        if not df.empty:
            return {"data": df, "source": "em_api"}
    except Exception:
        pass
    # 第二层：历史行情估算
    try:
        df = ak.stock_zh_a_hist(symbol=code, ...)
        if not df.empty:
            return {"data": estimated_from_hist(df), "source": "estimated"}
    except Exception:
        pass
    # 第三层：默认兜底
    return {"data": default_values(), "source": "fallback"}
```

## 七、东方财富连接失败（Connection aborted/RemoteDisconnected）

### 现象
AKShare 调用 `stock_cyq_em`、`stock_individual_fund_flow` 等东方财富源接口时抛出：
```
ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

### 原因
东方财富对 AKShare 的请求频率敏感，高频调用会触发连接重置。此问题在 WSL2 环境下比原生 Windows 更频繁。

### 应对策略（v2 — 2026-06-10）

**第4层回退：直接 HTTP API（替代 AKShare 封装）**

当 AKShare 所有接口均失败时，使用 Python `requests` 直接调用东方财富 push2 API。已实测可用（2026-06-10 海康威视分析案例）。

```python
import socket, requests
socket.setdefaulttimeout(60)

def get_stock_quote(code):
    \"\"\"基础行情 — 最稳定，几乎从不失败\"\"\"
    url = 'https://push2.eastmoney.com/api/qt/stock/get'
    params = {
        'secid': f'0.{code}',
        'fields': 'f43,f44,f45,f46,f47,f48,f49,f50,f57,f58,f60,f62,f115,f117,f162,f167,f168,f169,f170,f171,f172'
    }
    r = requests.get(url, params=params, timeout=20,
                     headers={'User-Agent': 'Mozilla/5.0',
                              'Referer': 'https://quote.eastmoney.com/'})
    d = r.json().get('data', {})
    return {
        'price': d.get('f43', 0) / 100,
        'high': d.get('f44', 0) / 100,
        'low': d.get('f45', 0) / 100,
        'change_pct': d.get('f170', 0) / 100,  # %
        'amount_yi': d.get('f48', 0) / 1e8,    # 亿元
        'market_cap_yi': d.get('f117', 0) / 1e8,
        'pe_dynamic': d.get('f162', 0) / 100,
        'up_count': d.get('f171', 0),
        'down_count': abs(d.get('f169', 0)),
    }

def get_market_indices():
    \"\"\"主要指数行情 — 稳定\"\"\"
    url = 'https://push2.eastmoney.com/api/qt/ulist.np/get'
    params = {'fltt': 2, 'fields': 'f2,f3,f4,f12,f14',
              'secids': '1.000001,0.399001,0.399006,0.399005'}
    r = requests.get(url, params=params, timeout=20,
                     headers={'User-Agent': 'Mozilla/5.0'})
    return [{'name': i['f14'], 'close': i['f2'], 'chg': i['f3']}
            for i in r.json().get('data',{}).get('diff',[])]
```

### 频次控制关键

| 规则 | 说明 |
|:-----|:------|
| 单次查询后 | sleep 至少 2秒 |
| 连续失败后 | 等待 5-10秒再重试 |
| 使用 socket timeout | `socket.setdefaulttimeout(60)` |
| 关键 header | 加 `Referer: https://quote.eastmoney.com/` 提高成功率 |

> ⚠️ **WAF 冷却实测补充（2026-08-24, DS-01 调试事故）**：短 UA（裸
> `Mozilla/5.0`）+ 高频连续探测会触发 push2his 的 **IP 级冷却封禁**——
> 症状是 RemoteDisconnected，且**连独立裸 requests、全部域名（主域+编号
> 子域）、http/https 一起断**，持续可达数十分钟以上。调试纪律：
> ① UA 用完整浏览器串；② 探测间隔 ≥30s；③ 一旦全体断连立即停手等冷却，
> 不要换域名/协议继续试（只会续期）；④ K线接口 klines 行序为
> date,open,**close,high**,low,volume——close/high 次序极易搞反。
> DataManager.fetch_push2_data 已内置完整UA+双域名轮换+2s间隔。

### push2 API 字段映射（已验证）

| 字段 | 含义 | 转换 |
|:-----|:-----|:-----|
| f43 | 最新价 | ÷100 |
| f44 | 最高价 | ÷100 |
| f45 | 最低价 | ÷100 |
| f47 | 成交量 | 手 |
| f48 | 成交额 | 元，÷1e8得亿元 |
| f57 | 股票代码 | 字符串 |
| f58 | 股票名称 | 字符串 |
| f60 | 昨收价 | ÷100 |
| f117 | 总市值 | 元，÷1e8得亿 |
| f162 | 动态市盈率 | ÷100 |
| f169 | 跌家数(沪) | 取绝对值 |
| f170 | 涨跌幅 | ÷100得% |
| f171 | 涨家数(沪) | 正数 |

### 整体回退策略（2026-06-10 升级版）

```
analyze.py 调用 → Layer 1: AKShare源 → Layer 2: 历史估算
  → Layer 3: 默认兜底 → Layer 4(手动): push2直连HTTP(成功率70%+)
```

## 八、WSL2 网络兼容性

| 数据源 | WSL2是否可用 |
|:-----|:-----|
| 东方财富 push2 直连API | ✅ 推荐（比AKShare稳定） |
| 东方财富/新浪 (AKShare) | ⚠️ 频次敏感，易断开 |
| efinance | ⚠️ 包可能未安装 |
| GitHub/OpenAI/DeepSeek API | ❌ 国外线路不通 |
