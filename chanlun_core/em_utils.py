#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
em_utils.py —— 东财系数据统一入口（自 a-stock-data V3.6.0 移植，Apache-2.0）

设计原则（来自 a-stock-data）：
1. 所有 eastmoney.com 请求一律走 em_get()：串行限流(≥1s+随机抖动) + 会话复用(Keep-Alive) + 默认 UA
2. 东财只用于它独有数据；行情/K线优先走通达信/腾讯（不封IP）
3. 批量场景（A500 全池）把 EM_MIN_INTERVAL 调大到 1.5~2s，严禁并发
4. 被封时用备用源（新浪/交易所官方/财联社，不同风控面）

覆盖端点（P0/P1 移植范围）：
- em_get / eastmoney_datacenter  — 统一限流入口 + 数据中心查询
- norm_ticker / get_prefix       — ticker 归一化（防北交所老号段僵尸数据）
- margin_trading / holder_num_change / stock_fund_flow_120d / block_trade — 资金面
- lockup_expiry / em_stock_monitor — 解禁预警 + 重点监控池（风控否决层）
- tencent_minute_kline / sina_financial_report / fund_flow_backup — 降级源
"""
import json
import random
import re
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# ── 常量 ────────────────────────────────────────────────────────────
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
MONITOR_URL = "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json"
_MONITOR_MARKET = {"1": "SH", "0": "SZ", "B": "BJ"}
CN_TZ = timezone(timedelta(hours=8))

# 东财风控阈值（社区实测 2026-05）：每秒>5次/单IP并发≥10/1分钟≥200 → 临时封IP
EM_MIN_INTERVAL = 1.0          # 两次东财请求最小间隔(秒)；批量筛选建议调大到 1.5~2
_em_last_call = [0.0]          # 模块级上次请求时间戳
_push2his_dead_until = [0.0]   # 终审A1: push2his 封锁期短路截止时间(进程内)

# 沪市指数白名单（000xxx 是沪指数/深个股共用歧义段）
SH_INDEX = {"000001", "000016", "000300", "000905", "000852", "000010", "000688", "000015"}

# ── 会话（requests 可用时）─────────────────────────────────────────
EM_SESSION = None
if _HAS_REQUESTS:
    EM_SESSION = requests.Session()
    EM_SESSION.headers.update({"User-Agent": UA})
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        _adapter = HTTPAdapter(max_retries=Retry(
            total=3, connect=3, backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"]))
        EM_SESSION.mount("https://", _adapter)
        EM_SESSION.mount("http://", _adapter)
    except Exception:
        pass  # 老版本 urllib3 降级为无重试


def set_em_interval(seconds: float):
    """批量场景调大东财请求间隔（A500 全池建议 1.5~2.0）。"""
    global EM_MIN_INTERVAL
    EM_MIN_INTERVAL = max(0.3, float(seconds))


def cn_today() -> str:
    """北京时间的今天（YYYY-MM-DD）。A股的'今天'按北京时间算。"""
    return datetime.now(CN_TZ).date().isoformat()


# ── curl 兜底（v5.3.4 终审A1, 2026-08-23）─────────────────────────
# 环境事实：eastmoney.com 对 Python HTTP 栈(urllib/requests，含换UA/强制IPv4)
# 实施 TLS 指纹级 WAF 封锁(连接即 RST: RemoteDisconnected)，同机 curl.exe
# 实测 HTTP 200 正常。因此传输层失败时统一降级 curl 子进程重试一次——
# 一次性救活全部 em_get 消费者：资金流主源(push2his)/两融/大宗/股东户数/
# 东财新闻搜索/push2市值兜底。仅传输层失败触发；HTTP 4xx/5xx 不走此路。
EM_CURL_FALLBACK = True


class _CurlResponse:
    """curl 子进程结果的 requests.Response 极简仿制（消费面只用 .json()/.text）。"""

    def __init__(self, data: bytes):
        self.content = data
        self.text = data.decode("utf-8", "replace")

    def json(self):
        import json as _json
        return _json.loads(self.text)


def _em_curl_get(full_url: str, headers: dict | None, timeout: int) -> "_CurlResponse":
    """经 curl.exe 发起 GET（子进程，绕过 Python TLS 指纹封锁）。失败抛异常。"""
    import subprocess as _sp
    cmd = ["curl", "-s", "--compressed", "-m", str(max(3, int(timeout)))]
    for k, v in (headers or {"User-Agent": UA}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(full_url)
    p = _sp.run(cmd, capture_output=True, timeout=int(timeout) + 10)
    if p.returncode != 0:
        raise RuntimeError(f"curl exit={p.returncode} stderr={p.stderr[:100]!r}")
    if not p.stdout:
        raise RuntimeError("curl empty body")
    return _CurlResponse(p.stdout)


def em_get(url: str, params: dict | None = None, headers: dict | None = None,
           timeout: int = 15, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session + 默认 UA。

    v5.3.4(终审A1): Python 栈传输层失败(WAF 指纹封锁致 RemoteDisconnected)
    时自动降级 curl 子进程重试；curl 也失败才抛原始 Python 异常（保留根因）。
    """
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        if _HAS_REQUESTS:
            return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
        # 无 requests 时降级 urllib（无节流会话，仅兜底）
        req = urllib.request.Request(full, headers=headers or {"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception as py_err:
        if not EM_CURL_FALLBACK:
            raise
        try:
            return _em_curl_get(full, dict(headers) if headers else {"User-Agent": UA}, timeout)
        except Exception as curl_err:
            # 以原始 Python 错误为主因抛出，保留现场便于诊断
            raise py_err from curl_err
    finally:
        _em_last_call[0] = time.time()


def eastmoney_datacenter(report_name: str, columns: str = "ALL",
                         filter_str: str = "", page_size: int = 50,
                         sort_columns: str = "", sort_types: str = "-1") -> list[dict]:
    """东财数据中心统一查询 — 龙虎榜/解禁/融资融券/大宗/股东户数/分红 共用（已内置限流）"""
    params = {
        "reportName": report_name, "columns": columns,
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


# ── Ticker 归一化 ─────────────────────────────────────────────────
_TICKER_RE = re.compile(r"^(?:(sh|sz|bj)(\d{6})|(\d{6})(?:\.(sh|sz|bj))?)$", re.IGNORECASE)


def _natural_market(digits: str) -> str:
    if digits.startswith("92") or digits[:2] in ("43", "83", "87"):
        return "bj"
    if digits[0] in ("5", "6", "9"):
        return "sh"
    return "sz"


def norm_ticker(code: str, stock_only: bool = False) -> str:
    """任意受支持写法 → 纯 6 位数字代码。不匹配抛 ValueError（绝不静默返回空串）。"""
    raw = str(code).strip()
    m = _TICKER_RE.match(raw)
    if not m:
        raise ValueError(
            f"无法把 {code!r} 解析为 6 位股票代码；"
            f"支持格式：600519 / SH600519 / sh600519 / 600519.SH"
            f"（前缀与后缀二选一，不能同时写）")
    digits = m.group(2) or m.group(3)
    market = (m.group(1) or m.group(4) or "").lower()
    if market:
        if digits.startswith("000"):
            if market == "bj":
                raise ValueError(f"{code!r} 市场标识与号段矛盾：000xxx 不属北交所。")
            if stock_only and market == "sh":
                raise ValueError(f"{code!r} 指向沪市指数而非个股（沪市无 000xxx 个股）。")
        else:
            nat = _natural_market(digits)
            if market != nat:
                raise ValueError(
                    f"{code!r} 的市场标识与号段矛盾：{digits} 属 {nat} 市，而不是 {market} 市。"
                    f"（改用 {nat}{digits} 或去掉市场标识）")
    return digits


def get_prefix(code: str) -> str:
    """6位代码 → 市场前缀（sh/sz/bj）。支持显式前缀 sh/sz/bj 透传。"""
    c = str(code).lower()
    if c.startswith(("sh", "sz", "bj")):
        return c[:2]
    if c.startswith("92"):
        return "bj"
    if c.startswith(("5", "6", "9")):
        return "sh"
    if c.startswith(("4", "8")):
        return "bj"
    if c in SH_INDEX:
        return "sh"
    return "sz"


def _secid(code: str) -> str:
    """6位代码 → 东财 secid（1=沪/0=深/北交所亦 0）。"""
    pre = get_prefix(code)
    if pre == "sh":
        return f"1.{code}"
    return f"0.{code}"


# ── 资金面 / 筹码层 ───────────────────────────────────────────────
def margin_trading(code: str, page_size: int = 30) -> list[dict]:
    """融资融券明细（日级）。返回 [{date, rzye, rzmre, rqye, rzrqye, ...}]"""
    code = norm_ticker(code)
    data = eastmoney_datacenter(
        "RPTA_WEB_RZRQ_GGMX",
        filter_str=f'(SCODE="{code}")',
        page_size=page_size,
        sort_columns="DATE", sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("DATE", ""))[:10],
            "rzye": row.get("RZYE", 0),        # 融资余额(元)
            "rzmre": row.get("RZMRE", 0),      # 融资买入额
            "rzche": row.get("RZCHE", 0),      # 融资偿还额
            "rqye": row.get("RQYE", 0),        # 融券余额(元)
            "rzrqye": row.get("RZRQYE", 0),    # 融资融券余额合计
        })
    return rows


def block_trade(code: str, page_size: int = 20) -> list[dict]:
    """大宗交易记录。返回 [{date, price, close, premium_pct, vol, amount, buyer, seller}]"""
    code = norm_ticker(code)
    data = eastmoney_datacenter(
        "RPT_DATA_BLOCKTRADE",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="TRADE_DATE", sort_types="-1",
    )
    rows = []
    for row in data:
        close = row.get("CLOSE_PRICE") or 0
        deal_price = row.get("DEAL_PRICE") or 0
        premium = ((deal_price / close - 1) * 100) if close else 0
        rows.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "price": deal_price,
            "close": close,
            "premium_pct": round(premium, 2),
            "vol": row.get("DEAL_VOLUME", 0),
            "amount": row.get("DEAL_AMT", 0),
            "buyer": row.get("BUYER_NAME", ""),
            "seller": row.get("SELLER_NAME", ""),
        })
    return rows


def holder_num_change(code: str, page_size: int = 10) -> list[dict]:
    """股东户数变化（季度级）。返回 [{date, holder_num, change_num, change_ratio, avg_shares}]
    股东户数持续减少 = 筹码集中 = 主力吸筹信号"""
    code = norm_ticker(code)
    data = eastmoney_datacenter(
        "RPT_HOLDERNUMLATEST",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="END_DATE", sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("END_DATE", ""))[:10],
            "holder_num": row.get("HOLDER_NUM", 0),
            "change_num": row.get("HOLDER_NUM_CHANGE", 0),
            "change_ratio": row.get("HOLDER_NUM_RATIO", 0),   # 环比%
            "avg_shares": row.get("AVG_FREE_SHARES", 0),      # 户均持股
        })
    return rows


def stock_fund_flow_120d(code: str) -> list[dict]:
    """个股资金流（日级，最近120个交易日）。返回 [{date, main_net, small_net, mid_net, large_net, super_net}]
    单位: 元。走 push2his（东财独有，限流必要）。

    终审A1(2026-08-23): push2his 对 Python/curl 双路封锁期间，本函数每次调用
    都要白耗 ~4.5s(requests 3次重试)。进程内短路：连接层失败后10分钟内直接
    返回[]，让 fund_factors 立即走 sina 兜底；封锁解除后自动恢复探测。"""
    code = norm_ticker(code)
    if time.time() < _push2his_dead_until[0]:
        return []
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": _secid(code),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    try:
        r = em_get(url, params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as e:
        # 终审A1(2026-08-23): push2his 域名对 Python/curl 双路封锁（逐域实测），
        # 本函数在当前环境常态性失败 → fund_factors._flow_rows 自动降级
        # sina fund_flow_backup(全口径, 保守系数)。保留本路径以便封锁解除后自愈。
        _push2his_dead_until[0] = time.time() + 600  # 10分钟内短路后续尝试
        print(f"[em_utils] push2 资金流请求失败 {code}: {e}")
        return []
    klines = d.get("data", {}).get("klines", [])
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({
                "date": parts[0],
                "main_net": float(parts[1]) if parts[1] != "-" else 0,
                "small_net": float(parts[2]) if parts[2] != "-" else 0,
                "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                "large_net": float(parts[4]) if parts[4] != "-" else 0,
                "super_net": float(parts[5]) if parts[5] != "-" else 0,
            })
    return rows


# ── 风控否决层 ────────────────────────────────────────────────────
def lockup_expiry(code: str, trade_date: str | None = None, forward_days: int = 90) -> dict:
    """限售解禁日历。返回 {history: [...], upcoming: [...]}
    upcoming: 未来 forward_days 天待解禁（解禁预警，风控否决信号）。"""
    code = norm_ticker(code)
    trade_date = trade_date or cn_today()
    history_data = eastmoney_datacenter(
        "RPT_LIFT_STAGE",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=15,
        sort_columns="FREE_DATE", sort_types="-1",
    )
    history = []
    for row in history_data:
        history.append({
            "date": str(row.get("FREE_DATE", ""))[:10],
            "type": row.get("FREE_SHARES_TYPE", ""),
            "shares": row.get("FREE_SHARES", 0),             # 本次解禁股数(万股)
            "able_shares": row.get("ABLE_FREE_SHARES", 0),   # 实际可流通股数(万股)
            "ratio": row.get("FREE_RATIO", 0),               # 占总股本比(小数)
        })

    end_str = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=forward_days)).strftime("%Y-%m-%d")
    upcoming_data = eastmoney_datacenter(
        "RPT_LIFT_STAGE",
        filter_str=f'(SECURITY_CODE="{code}")(FREE_DATE>=\'{trade_date}\')(FREE_DATE<=\'{end_str}\')',
        page_size=20,
        sort_columns="FREE_DATE", sort_types="1",
    )
    upcoming = []
    for row in upcoming_data:
        upcoming.append({
            "date": str(row.get("FREE_DATE", ""))[:10],
            "type": row.get("FREE_SHARES_TYPE", ""),
            "shares": row.get("FREE_SHARES", 0),
            "able_shares": row.get("ABLE_FREE_SHARES", 0),
            "ratio": row.get("FREE_RATIO", 0),
        })
    return {"history": history, "upcoming": upcoming}


def em_stock_monitor(only_active: bool = True) -> list[dict]:
    """东财重点监控池（交易所风险警示/重点监控名单 + 生效时间窗，零鉴权静态 JSON）。
    only_active=True 只留今天仍在监控窗口内的。
    返回: [{code, name, market, start, end, link}]"""
    r = em_get(MONITOR_URL, headers={"Referer": "https://vipmoney.eastmoney.com/"}, timeout=20)
    rows = r.json() or []
    today = cn_today()
    out = []
    for x in rows:
        start, end = x.get("VALIDATESTARTDATE", ""), x.get("VALIDATEENDDATE", "")
        if only_active and not (start <= today <= end):
            continue
        raw_mkt = str(x.get("MARKET", "")).upper()
        out.append({
            "code": x.get("STKCODE", ""),
            "name": x.get("STKNAME", ""),
            "market": _MONITOR_MARKET.get(raw_mkt, f"?{raw_mkt}"),
            "start": start, "end": end,
            "link": x.get("LINK_URL", ""),
        })
    return out


# ── 降级源（主源被封时用，不同风控面）────────────────────────────
def tencent_minute_kline(code: str, period: str = "m5", count: int = 320) -> list[dict]:
    """腾讯分钟K线（mootdx 挂掉时的备用源，零鉴权不封IP）。
    period: m1/m5/m15/m30/m60；count ≤ 320。
    返回: [{date, open, close, high, low, volume(手), amount(元)}]
    ⚠️ 腾讯第7字段是换手率基点不是成交额，成交额需自算：量(手)×100×均价。
    """
    code = norm_ticker(code)
    pre = get_prefix(code)
    url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={pre}{code},{period},,{count}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    data = (d.get("data") or {}).get(f"{pre}{code}", {}) or {}
    klines = data.get(f"m{period[1:]}") or data.get(period) or []
    rows = []
    for k in klines:
        # [时间, 开, 收, 高, 低, 量(手), {}, 换手率基点]
        if len(k) < 6:
            continue
        ts, o, c, h, lo, v = k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        avg = (o + h + lo + c) / 4.0
        rows.append({
            "date": ts, "open": o, "close": c, "high": h, "low": lo,
            "volume": v, "amount": v * 100 * avg,  # 手×100股×均价
        })
    return rows


def tencent_daily_kline(code: str, count: int = 800, adjust: str = "qfq") -> list[dict]:
    """腾讯日K线（Baostock/mootdx 挂掉时的备用源，零鉴权不封IP，带前复权）。
    adjust: qfq(前复权,默认)/hfq(后复权)/""(不复权)。
    返回: [{date, open, close, high, low, volume(手), amount(元)}]
    ⚠️ 腾讯连续 5000+ 次后会限流返回空（非封IP），降速或换新浪即可恢复。
    """
    code = norm_ticker(code)
    pre = get_prefix(code)
    adj_suffix = {"qfq": "qfq", "hfq": "hfq"}.get(adjust, "")
    param = f"{pre}{code},day,,,{count},{adj_suffix}" if adj_suffix else f"{pre}{code},day,,,{count}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    data = (d.get("data") or {}).get(f"{pre}{code}", {}) or {}
    # 前复权数据在 qfqday 键下；不复权在 day 键下
    klines = data.get(f"{adj_suffix}day") if adj_suffix else (data.get("day") or [])
    if not klines:
        klines = data.get("day") or []
    rows = []
    for k in klines:
        # [日期, 开, 收, 高, 低, 量(手)]  （前复权时可能带第7个均价字段）
        if len(k) < 6:
            continue
        rows.append({
            "date": str(k[0])[:10], "open": float(k[1]), "close": float(k[2]),
            "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]),
        })
    return rows


def sina_financial_report(code: str, report_type: str = "lrb", num: int = 8) -> list[dict]:
    """新浪财报三表（同花顺/东财 被封时备用）。
    report_type: "fzb"(资产负债) / "lrb"(利润) / "llb"(现金流)
    返回: 按报告期倒序 [{报告期, <科目>: <值>, <科目>_同比: ...}]"""
    code = norm_ticker(code)
    prefix = "sh" if code.startswith("6") else "sz"
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {
        "paperCode": f"{prefix}{code}", "source": report_type,
        "type": "0", "page": "1", "num": str(num),
    }
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    report_list = r.json().get("result", {}).get("data", {}).get("report_list", {}) or {}
    rows = []
    for period in sorted(report_list.keys(), reverse=True)[:num]:
        obj = report_list[period]
        rec = {"报告期": f"{period[:4]}-{period[4:6]}-{period[6:8]}"}
        for it in obj.get("data", []) or []:
            title = it.get("item_title", "")
            if not title or it.get("item_value") is None:
                continue
            rec[title] = it.get("item_value")
            tongbi = it.get("item_tongbi")
            if tongbi not in (None, ""):
                rec[title + "_同比"] = tongbi
        rows.append(rec)
    return rows


def fund_flow_backup(code: str, days: int = 60) -> list:
    """个股资金流备用源（东财被封时用）：新浪，日度四档单净额。
    返回: [{date, close, net_amount, turnover}]"""
    code = norm_ticker(code)
    pre = ("bj" if code.startswith(("92", "8"))
           else "sh" if code.startswith(("6", "9")) else "sz") + code
    u = (f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         f"MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={days}&sort=opendate&asc=0&daima={pre}")
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        t = r.read().decode("utf-8", "ignore")
    arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    return [{"date": x.get("opendate"), "close": x.get("trade"),
             "net_amount": x.get("netamount"), "turnover": x.get("turnover")} for x in arr]


if __name__ == "__main__":
    # 自检：真实数据跑通核心端点（不触发封IP：全部走 em_get 串行限流）
    import sys
    test_code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"=== em_utils 自检 ({test_code}) ===")
    print("cn_today:", cn_today())
    print("norm_ticker(SH600519):", norm_ticker("SH600519"))
    print("norm_ticker(600519.SH):", norm_ticker("600519.SH"))
    print("get_prefix(600519):", get_prefix("600519"))

    print("\n-- margin_trading --")
    try:
        rows = margin_trading(test_code, 5)
        print(f"  {len(rows)} 行, 最新: ", rows[0] if rows else "空")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n-- holder_num_change --")
    try:
        rows = holder_num_change(test_code, 3)
        print(f"  {len(rows)} 行, 最新: ", rows[0] if rows else "空")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n-- stock_fund_flow_120d --")
    try:
        rows = stock_fund_flow_120d(test_code)
        if rows:
            last = rows[-1]
            print(f"  {len(rows)} 行, 最新 {last['date']}: 主力净流入={last['main_net']/1e4:.0f}万")
        else:
            print("  空")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n-- lockup_expiry --")
    try:
        d = lockup_expiry(test_code)
        print(f"  历史 {len(d['history'])} 批, 未来90天待解禁 {len(d['upcoming'])} 批")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n-- em_stock_monitor --")
    try:
        pool = em_stock_monitor()
        print(f"  当前重点监控 {len(pool)} 只, 前3: {[(s['code'], s['name']) for s in pool[:3]]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n-- tencent_minute_kline --")
    try:
        rows = tencent_minute_kline(test_code, "m5", 5)
        print(f"  {len(rows)} 根, 最新: ", rows[-1] if rows else "空")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n-- sina_financial_report(lrb) --")
    try:
        rows = sina_financial_report(test_code, "lrb", 2)
        print(f"  {len(rows)} 期, 最新净利润: ", rows[0].get("净利润", "N/A") if rows else "空")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n自检完成")
