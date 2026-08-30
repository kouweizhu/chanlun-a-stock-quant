"""快速获取结构化财务数据 - 输出 JSON
数据源: 同花顺 hithink-finance-query + hithink-market-query
v2.0 — 2026-05-14
  新增: 扣非净利润(最新+4年趋势)
  新增: 4年趋势分析 + 趋势修正评分
  新增: trend_analysis 输出段供报告使用
"""
import sys, json, os, math, re
from datetime import datetime
from collections import Counter

# v5.3.4(审计P0-4/B1): 基本面评分函数统一——唯一实现为 quick_fundamental.
# calculate_fundamental_score（v5.0口径：健康20/估值10/键名marginal_improvement/
# 年报ROE评分，与 A500 选股链同款）。本文件的旧版实现（健康25/估值25/
# margin_improvement 键名/快照ROE口径）已停止调用，改名保留见
# calculate_fundamental_score_legacy_hithink，待 git 版本管理建立后删除。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quick_fundamental import calculate_fundamental_score as _unified_calc_score

API_KEY = os.environ.get("IWENCAI_API_KEY", "")
FINANCE_SKILL = "hithink-finance-query"
MARKET_SKILL = "hithink-market-query"
API_URL = "https://openapi.iwencai.com/v1/query2data"

# v6.1.2: iwencai 升 L1 主源后的进程级熔断——query2data timeout=30s，
# 无熔断时 key 一旦失效每票都白付超时才降级（--report 并行下 fundamental
# 线程卡死拖累整体）。连续失败≥3次即停试至进程结束；用 dict 避免 global。
_IWENCAI_BREAKER = {"fail_streak": 0, "open": False, "note": ""}


def _iw_mark_success():
    """成功即重置连败计数（半开语义：下一次调用总会再试一次）。"""
    _IWENCAI_BREAKER["fail_streak"] = 0


def _iw_mark_failure(r):
    """计一次 L1 失败；达到阈值开启熔断（仅 stderr 可见，不抛错）。"""
    _b = _IWENCAI_BREAKER
    _b["fail_streak"] += 1
    err = str(((r or {}).get("error") or "无返回数据"))[:80]
    if not _b["open"] and _b["fail_streak"] >= 3:
        _b["open"] = True
        _b["note"] = err[:60]
        print(f"[hithink_fundamental] ⚡ 熔断开启(连败{_b['fail_streak']}次)"
              f"：本进程后续基本面请求直走 AKShare/sina", file=sys.stderr)
    else:
        print(f"[hithink_fundamental] L1 iwencai 失败({_b['fail_streak']}/3): {err}",
              file=sys.stderr)


def call_ithink(query: str, skill_id: str) -> list:
    """调用同花顺 query2data 接口，返回 datas 列表

    v6.1.1 传输层重构：旧实现用 subprocess.run(curl) 管道捕获 stdout，
    在 DSH 沙箱环境子进程 stdio 管道捕获会触发 _readerthread 异常（实测
    Thread-1 Traceback）导致本函数恒失败且静默返回 []。改用 urllib 原生
    请求（2026-08-26 实测该环境下可用），并补齐 skillhub 协议要求的
    完整 X-Claw-* 头（Trace-Id 每请求唯一 64 位 hex）。"""
    if not API_KEY:
        return []
    try:
        import secrets
        import urllib.request

        payload = json.dumps({
            "query": query, "page": "1", "limit": "5",
            "is_cache": "0", "expand_index": "true"
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "X-Claw-Call-Type": "normal",
            "X-Claw-Skill-Id": skill_id,
            "X-Claw-Skill-Version": "1.0.0",
            "X-Claw-Plugin-Id": "none",
            "X-Claw-Plugin-Version": "none",
            "X-Claw-Trace-Id": secrets.token_hex(32),
        }
        req = urllib.request.Request(API_URL, data=payload,
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        _sc = d.get("status_code")
        if _sc not in (0, None):
            # v6.1.2 可见性纪律：非零状态码必须留痕。实测 -1225=查询复杂度
            # 超限(快照>9指标静默返空)，无此打印则外部只见"空数据"无从诊断。
            print(f"[hithink_fundamental] query2data 状态异常({skill_id}): "
                  f"status_code={_sc} msg={d.get('status_msg')}", file=sys.stderr)
        return d.get("datas", []) or []
    except Exception as e:
        # 韧性纪律：失败可见不静默（曾因静默落 sina 兜底致扣非长期空值）
        print(f"[hithink_fundamental] query2data 调用失败({skill_id}): {e}",
              file=sys.stderr)
        return []


def _fetch_dividend_iwencai(symbol):
    """v6.1.1: L1 主源成功后的 iwencai 股息率回填（真增强步, 非兜底）。

    背景（2026-08 接线复核）：旧文档声称 L2“补股息率”，但 L1 早退使该
    承诺永不发生。本函数在 AKShare 主源成功且 dividend_yield 为空、且
    IWENCAI_API_KEY 已接入当前进程环境时，追加一次 market-query 估值
    查询仅回填股息率。任一异常/无 key 静默返回 None，绝不阻断主链。
    查询句式复用 L2 Step4 成熟模板（skillhub: 短词易误路由到通用搜索）。"""
    if not API_KEY:
        return None
    try:
        from date_utils import recent_weekday_keys as _rwk
        _days = _rwk(14)
        mdatas = call_ithink(
            f"{symbol} 市盈率PE 市净率PB 股息率 总市值", MARKET_SKILL)
        if not mdatas:
            print("[hithink_fundamental] iwencai 股息率增强: 无返回, 跳过",
                  file=sys.stderr)
            return None
        _item = mdatas[0]
        _dy = get_val(_item, [f"股息率[{d}]" for d in _days])
        return (_dy / 100) if _dy else None
    except Exception as _e:
        print(f"[hithink_fundamental] iwencai 股息率增强失败(忽略): {_e}",
              file=sys.stderr)
        return None


def _sina_fundamentals_backup(symbol, result):
    """L3 新浪三表兜底源（零鉴权）。
    提取利润表(营收/归母净利/毛利率/净利率/EPS) + 资产负债表(负债率) + 多期趋势。
    返回完整的 result 结构（data_source=sina-backup，confidence=3）。

    ⚠️ v5.3.3-H 已知缺陷(批次G审计确认): 扣非净利润(deductedProfit/
    deducted_profit)/ROE/毛利率/流动比率 恒为 None——新浪接口无对应字段,
    不可作为利润质量分析依据。仅在 AKShare+iwencai 双失败时保底输出。
    """
    try:
        import em_utils

        def _latest(rows, field):
            for r in rows:
                v = r.get(field)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return v
            return None

        lrb = em_utils.sina_financial_report(symbol, "lrb", num=8)
        fzb = em_utils.sina_financial_report(symbol, "fzb", num=8)
        if not lrb:
            return None

        cur = lrb[0]
        revenue = _latest(lrb, "营业收入") or _latest(lrb, "营业总收入")
        net_profit = _latest(lrb, "归属于母公司所有者的净利润") or _latest(lrb, "净利润")
        eps = _latest(lrb, "基本每股收益")
        op_profit = _latest(lrb, "营业利润")
        revenue_yoy = _latest(lrb, "营业收入_同比")
        profit_yoy = _latest(lrb, "归属于母公司所有者的净利润_同比") or _latest(lrb, "净利润_同比")

        # 毛利率/净利率：新浪无直接字段，用利润表推导
        gp_margin = None
        np_margin = None
        if revenue:
            gp_margin = None  # 新浪利润表无营业成本明细时置空，避免伪值
            np_margin = (net_profit / revenue) if net_profit else None

        liability = None
        total_assets = None
        if fzb:
            c = fzb[0]
            total_assets = _latest(fzb, "资产总计")
            liab = _latest(fzb, "负债合计")
            if total_assets and liab:
                liability = liab / total_assets

        # 多期趋势（利润表按时间倒序，取归母净利/营收算趋势）
        # v5.4(B-06): 只取年报(-12-31)——新浪 lrb 按报告期倒序混排季报,
        # 旧实现 lrb[:5] 常被 Q1/Q3 累计值占据年度键, analyze_trend 把
        # Q1 对年报比 → 假"大幅下滑/高增长"污染趋势修正分。
        multi_year = {}
        _annual_seen = 0
        for r in lrb:
            period = r.get("报告期", "")
            yr = period[:4]
            if not yr or not period.endswith("-12-31") or yr in multi_year:
                continue
            _annual_seen += 1
            rev_i = safe_float(r.get("营业收入") or r.get("营业总收入"))
            np_i = safe_float(r.get("归属于母公司所有者的净利润") or r.get("净利润"))
            # v5.3.4(B2): 同比保持小数口径——新浪 _同比 已实盘核验为小数
            # （2026-08-23 平安银行 lrb：营业收入_同比=0.01776 即 1.78%），
            # 与 L1 AKShare(_parse_pct) 及 quick 评分阈值口径一致，不再 *100。
            _rev_yoy_raw = safe_float(r.get("营业收入_同比"))
            _np_yoy_raw = safe_float(r.get("归属于母公司所有者的净利润_同比")
                                     or r.get("净利润_同比"))
            multi_year[yr] = {
                "revenue": rev_i,
                "net_profit": np_i,
                "deducted_profit": None,
                "roe": None,
                "gp_margin": None,
                "np_margin": (np_i / rev_i) if rev_i and np_i else None,
                "liability": None,
                "revenue_yoy": _rev_yoy_raw,
                "profit_yoy": _np_yoy_raw,
            }
            if _annual_seen >= 5:
                break
        result["name"] = symbol
        result["data_date"] = cur.get("报告期", "").replace("-", "")
        result["profitability"] = {
            "roeAvg": None, "npMargin": np_margin, "gpMargin": gp_margin,
            "netProfit": net_profit, "deductedProfit": None,
            "profitQuality": None, "epsTTM": eps, "totalRevenue": revenue,
        }
        # ⚠️ 单位说明（v5.3.4/B5 已实盘核验）：新浪 _同比 是小数（2026-08-23
        # 平安银行 lrb 实测 0.01471 口径成立），YOY* 统一小数、*_pct 为百分点展示。
        result["growth"] = {
            "YOYNI": profit_yoy if isinstance(profit_yoy, (int, float)) else None,
            "YOYRevenue": revenue_yoy if isinstance(revenue_yoy, (int, float)) else None,
            "revenue_yoy_pct": (revenue_yoy * 100) if isinstance(revenue_yoy, (int, float)) else None,
            "profit_yoy_pct": (profit_yoy * 100) if isinstance(profit_yoy, (int, float)) else None,
        }
        # B5 防御性告警：小数口径下 |同比|>5（即>500%）几乎不可能是真实值，
        # 若出现多半是上游单位口径变化，宁可吵闹不可静默错 100 倍。
        for _k, _v in (("YOYNI", profit_yoy), ("YOYRevenue", revenue_yoy)):
            if isinstance(_v, (int, float)) and abs(_v) > 5:
                print(f"[hithink_fundamental] ⚠️ sina {_k}={_v} 疑似百分数口径泄漏"
                      f"（小数口径合理范围应远小于此），请核验 em_utils.sina_financial_report",
                      file=sys.stderr)
        result["health"] = {
            "liabilityToAsset": liability, "currentRatio": None, "quickRatio": None,
            "CFOToOR": None, "CFOToNP": None, "totalAssets": total_assets,
        }
        result["multi_year_data"] = multi_year
        result["trend_analysis"] = analyze_trend(multi_year, result["profitability"])
        # v5.3.4(B1): 统一评分函数（quick_fundamental v5.0 口径），传入多年度数据
        result["fundamental_score"] = _unified_calc_score(
            result, multi_year_data=result.get("multi_year_data"))
        result["stock_type_hint"] = classify_stock_type(result)
        result["data_source"] = "sina-backup"
        result["confidence"] = 3  # 降级源置信度低于主源
        return result
    except Exception as e:
        result["error"] = f"sina-backup fail: {e}"
        return None


def get_val(item, keys):
    """从返回的数据项中提取第一个匹配的值"""
    for k in keys:
        if k in item and item[k] is not None:
            v = item[k]
            try:
                return float(v)
            except (ValueError, TypeError):
                return v
    return None


def get_str(item, keys):
    """提取字符串值"""
    for k in keys:
        if k in item and item[k] is not None:
            return str(item[k])
    return ""


def _snap_val(item, variants, rp1, rp0):
    """v6.1.2 快照字段动态键发现（2026-08-26 实测驱动）。

    网关对快照查询的键名风格漂移已被实测证实——同一返回里混用
      裸键:        营业收入 / 归母净利润 / 营业收入同比增长率
      日期后缀键:  净资产收益率[20260630] / 扣非归母净利润[20260630]
      词形改名:    查询词"扣非净利润"→返回"扣非归母净利润"、ROE→净资产收益率
    且 v5.3.4(D1) 的 latest_report_dates() 日历推断实测落后真实披露期
    （2026-08 底返回 20260331，实际已出 20260630 中报）——写死后缀必漏。

    匹配优先级(逐词形变体):
      1) 词形+[rp1]   2) 词形+[rp0]   3) 词形+任意[yyyymmdd]取最大日期
      4) 裸键精确匹配
    v5.4.1(AUD-C-03): 第3级加 400 自然日新鲜度地板——更陈年的残留键视为
    不存在(宁缺勿错)，防止"营收取2019年报、ROE 取2026中报"式跨报告期
    混拼静默进 profitability。空值路径下游已证明能优雅处理 None。
    返回 (value, matched_key)；matched_key 供 data_date 推断取后缀。"""
    _now = datetime.now()
    for v in variants:
        for suf in (f"[{rp1}]", f"[{rp0}]"):
            k = v + suf
            if k in item and item.get(k) not in (None, ""):
                return get_val(item, [k]), k
        dated, bare = [], None
        for k in item.keys():
            if not isinstance(k, str) or not k.startswith(v):
                continue
            if k == v:
                bare = k
            elif k.startswith(v + "["):
                m = re.fullmatch(r"\[(\d{8})\]", k[len(v):])
                if m:
                    dated.append((m.group(1), k))
        if dated:
            _, k = max(dated)
            # 新鲜度地板: 键日期距今>400自然日 → 视为陈年残留不采纳
            try:
                _kd = datetime.strptime(k[max(0, len(k) - 9):].strip("[]"), "%Y%m%d")
                if (_now - _kd).days > 400:
                    print(f"[hithink_fundamental] _snap_val 陈年键丢弃({k}, "
                          f"{(_now - _kd).days}天前)", file=sys.stderr)
                    dated = []
            except ValueError:
                pass
            if dated:
                val = get_val(item, [k])
                if val is not None:
                    return val, k
        if bare is not None:
            val = get_val(item, [bare])
            if val is not None:
                return val, bare
    return None, ""


def safe_float(val):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


def get_fundamentals(symbol):
    """主函数：结构化财务数据获取（v6.1.2 调度序升级, 2026-08-26）

    三级数据源调度（v6.1.2 起 key 已接入进程环境且错误可见，iwencai 升
    主源与全局「同花顺 query2data 首选」纪律对齐；AKShare 为常驻零 key
    中间层——sitecustomize 接线若失效，最坏情形退化为 v6.1.1 行为）:

      L1 主源   同花顺 query2data（全字段+股息率；进程级熔断连败≥3次停试）
      L2 降级   AKShare stock_financial_abstract_ths + Baostock 估值/行业
                （零 key 永远在线；缺股息率时 L2+ iwencai 增强步回填）
      L3 兜底   新浪三表（扣非/ROE/毛利率缺失, confidence=3）

    返回契约与 v2.0 一致（profitability/growth/health/multi_year_data/
    trend_analysis/fundamental_score...），下游 single_stock_analysis.py
    及报告 agent 无需改动。A500 批量链(pool_screener)不经过本函数，
    仍为 AKShare→Baostock，刻意不纳入 iwencai（限流阈值不适合批量）。
    """
    # ── L1: iwencai 主源（key 接入且熔断未开）──
    if API_KEY:
        if _IWENCAI_BREAKER["open"]:
            print(f"[hithink_fundamental] 熔断中跳过 iwencai → 直走 AKShare"
                  f"(原因: {_IWENCAI_BREAKER['note']})", file=sys.stderr)
        else:
            r = _get_fundamentals_iwencai(symbol)
            if r and not r.get("error"):
                _iw_mark_success()
                return r
            _iw_mark_failure(r)

    # ── L2: AKShare 零 key 中间层 ──
    try:
        import akshare_fundamental as _ak
        r = _ak.get_fundamentals_akshare(symbol)
        # v5.3.4(B4/审计P1-1): 接受条件放宽——任一核心字段非空即采纳 AKShare 全量
        # 数据。旧条件硬性要求 roeAvg 非空，会把负净资产/未披露ROE 的股票整体丢给
        # 最差的 sina 兜底（其扣非/毛利率恒为 None），数据质量净损失。
        _prof = (r or {}).get("profitability") or {}
        _core_ok = any(_prof.get(k) is not None
                       for k in ("netProfit", "totalRevenue", "roeAvg"))
        if r and not r.get("error") and _core_ok:
            _normalized = _normalize_to_hithink(r)
            # v6.1.2: L2 降级接管时若缺股息率，仍以 iwencai 增强步回填
            #（与 v6.1.1 相同的来源透明标注）。
            # v5.4.1(AUD-A-03/C-02): 增强步同样受熔断器门控——key 失效熔断开启
            # 后每只走 L2 的股票仍会白付一次最长 30s 超时。
            if _normalized.get("dividend_yield") is None and not _IWENCAI_BREAKER["open"]:
                _dy = _fetch_dividend_iwencai(symbol)
                if _dy is not None:
                    _normalized["dividend_yield"] = _dy
                    # 来源透明度：标注增强痕迹，报告层可区分展示
                    _normalized["data_source"] = "akshare-ths-primary+iwencai-enh"
            return _normalized
    except Exception as _e:
        print(f"[hithink_fundamental] L2 AKShare 中间层异常，降级 sina: {_e}", file=sys.stderr)

    # ── L2: iwencai 二次尝试（v5.4.1/AUD-A-03: 熔断门控+失败计数对齐）──
    # 旧块无条件重跑整条 iwencai 链(最多8×30s)且不计入熔断——与顶层 L1 承诺
    # "熔断开启后直走 AKShare/sina"相悖。L1 数秒前刚失败过，此处仅保留
    # "AKShare 失败后的最后网络侧机会"语义，受同一熔断器约束。
    if API_KEY and not _IWENCAI_BREAKER["open"]:
        r = _get_fundamentals_iwencai(symbol)
        if r and not r.get("error"):
            return r
        _iw_mark_failure(r)

    # ── L3: sina 三表兜底 ──
    result = {
        "symbol": symbol,
        "name": "",
        "industry": "",
        "industry_classification": "",
        "data_date": "",
        "profitability": {},
        "growth": {},
        "health": {},
        "valuation": {},
        "market_cap": None,
        "dividend_yield": None,
        "stock_type_hint": "",
        "data_source": "hithink-finance-query",
        "confidence": 5,
        "error": None,
        "multi_year_data": {},
        "trend_analysis": {},
    }
    # v5.4(B-21): sina 兜底调用加守卫——_sina_fundamentals_backup 内部虽带
    # timeout，但 DNS 失败/连接拒绝/解析异常仍会抛出；旧实现调用点裸奔，
    # 异常直接冒泡使整次 get_fundamentals 崩溃而非优雅降级到 all-sources-failed。
    try:
        backup = _sina_fundamentals_backup(symbol, result)
    except Exception as _sina_err:  # noqa: BLE001 守卫层必须吞一切降级
        import sys as _sys
        print(f"[hithink_fundamental] L3 sina兜底异常(降级处理): {_sina_err}",
              file=_sys.stderr)
        backup = None
    if backup:
        return backup
    result["error"] = "全部数据源失败(AKShare/iwencai/sina)"
    # v5.4(B-13): 全源失败不得沿用"hithink-finance-query"误导标签——
    # 报告层会按该标签渲染数据来源，用户误以为同花顺数据在手
    result["data_source"] = "all-sources-failed"
    result["confidence"] = 0
    return result


def _normalize_to_hithink(r: dict) -> dict:
    """AKShare 结果对齐 hithink 输出契约（键集/日期格式/趋势分析）"""
    r.setdefault("industry_classification", "")
    r.setdefault("market_cap", None)
    r.setdefault("dividend_yield", None)
    if not r.get("trend_analysis"):
        try:
            r["trend_analysis"] = analyze_trend(
                r.get("multi_year_data") or {}, r.get("profitability") or {})
        except Exception:
            r["trend_analysis"] = {}
    dd = str(r.get("data_date") or "")
    if "-" in dd:
        r["data_date"] = dd.replace("-", "")[:8]
    if not r.get("stock_type_hint"):
        try:
            r["stock_type_hint"] = classify_stock_type(r)
        except Exception:
            r["stock_type_hint"] = ""
    r["data_source"] = "akshare-ths-primary"
    return r


def _get_fundamentals_iwencai(symbol):
    """L1: 同花顺 query2data 主源（v6.1.2 恢复主源地位；字段最全含股息率，
    快照+5年分批+估值三段查询）。失败路径：返回带 error 的 result，由外层
    get_fundamentals 熔断计数并降级 AKShare——本函数内部不再直跳 sina。"""
    result = {
        "symbol": symbol,
        "name": "",
        "industry": "",
        "industry_classification": "",
        "data_date": "",
        "profitability": {},
        "growth": {},
        "health": {},
        "valuation": {},
        "market_cap": None,
        "dividend_yield": None,
        "stock_type_hint": "",
        "data_source": "hithink-finance-query",
        "confidence": 5,
        "error": None,
        "multi_year_data": {},
        "trend_analysis": {},  # 4年趋势分析
    }

    try:
        # ===== Step 1: 最新财务数据 =====
        # v6.1.2 拆分查询（2026-08-26 实测）：网关对快照类查询有复杂度上限，
        # 16指标全量返回 status_code=-1225 静默空 datas；≤8 指标稳定通过，
        # 年度字段(带[yyyymmdd]后缀)9个不受限。拆两次并合并 item 后再提取。
        q1a = (f"{symbol} 营业收入 归母净利润 ROE 销售毛利率 销售净利率 "
               f"资产负债率 营收同比增速 净利润同比增速")
        q1b = (f"{symbol} 每股收益 每股净资产 每股经营现金流 扣非净利润 "
               f"流动比率 速动比率 经营活动现金流净额 总资产")
        fdatas_a = call_ithink(q1a, FINANCE_SKILL)
        fdatas_b = call_ithink(q1b, FINANCE_SKILL) if fdatas_a else []
        # 合并两个 item（b 缺失时降级只用 a——前半段含营收/归母/ROE 核心字段）
        if fdatas_a:
            item = dict(fdatas_a[0])
            if fdatas_b:
                for _k, _v in fdatas_b[0].items():
                    item.setdefault(_k, _v)
            fdatas = [item]
        else:
            fdatas = []
        if not fdatas:
            # v6.1.2 调度序变更：旧实现此处直接跳新浪兜底（绕过 AKShare，
            # iwencai 抖动即坠最差源）；现返回错误交由外层调度器降级零 key
            # 的 AKShare 中间层（外层熔断器同步计一次失败）。sina 仅保留
            # 为终兜底，不再承担 iwencai→sina 直跳。
            result["error"] = "同花顺财务API无返回数据"
            result["confidence"] = 1
            return result

        item = fdatas[0]
        result["name"] = get_str(item, ["股票简称"])

        # v6.1.2: 快照提取全面改走 _snap_val 动态键发现——旧 v5.3.4(D1)
        # 依赖 latest_report_dates() 推断后缀，实测该日历落后真实披露
        # （202608 底仍给 20260331，中报 20260630 键全部落空），且网关存在
        # 裸键/[日期]/词形改名三种风格并存。变体列表含实测别名。
        # （日历推断保留为 _snap_val 的最高优先候选，仅作加速；日历过时由
        #   第3级"任意[yyyymmdd]取最大日期"兜住。）
        from date_utils import latest_report_dates as _lrd
        _rp1, _rp0 = _lrd()
        revenue, _k_rev = _snap_val(item, ["营业收入", "营业总收入"], _rp1, _rp0)
        net_profit, _k_np = _snap_val(item, ["归母净利润"], _rp1, _rp0)
        roe, _k_roe = _snap_val(item, ["净资产收益率", "ROE"], _rp1, _rp0)
        gp_margin, _k_gp = _snap_val(item, ["销售毛利率"], _rp1, _rp0)
        np_margin, _k_npm = _snap_val(item, ["销售净利率"], _rp1, _rp0)
        liability, _k_liab = _snap_val(item, ["资产负债率"], _rp1, _rp0)
        eps, _k_eps = _snap_val(item, ["基本每股收益", "每股收益"], _rp1, _rp0)
        bvps, _k_bvps = _snap_val(item, ["每股净资产"], _rp1, _rp0)
        cfops, _k_cfops = _snap_val(item, ["每股经营活动产生的现金流量净额",
                                           "每股经营现金流净额"], _rp1, _rp0)
        revenue_yoy, _k_ryoy = _snap_val(item, ["营业收入同比增长率"], _rp1, _rp0)
        profit_yoy, _k_pyoy = _snap_val(item, ["归母净利润同比增长率",
                                               "净利润同比增长率"], _rp1, _rp0)
        current_ratio, _k_cr = _snap_val(item, ["流动比率"], _rp1, _rp0)
        quick_ratio, _k_qr = _snap_val(item, ["速动比率"], _rp1, _rp0)
        total_assets, _k_ta = _snap_val(item, ["总资产"], _rp1, _rp0)
        cfo, _k_cfo = _snap_val(item, ["经营活动产生的现金流量净额"], _rp1, _rp0)
        deducted_profit, _k_ded = _snap_val(
            item, ["扣非归母净利润", "扣非净利润"], _rp1, _rp0)

        # 判断数据日期（v6.1.2: 直接取实际命中键的 [yyyymmdd] 后缀，
        # 不再信任日历推断；全裸键命中时留空由下游兜底）
        # v5.4.1(AUD-C-03): 四锚字段后缀不一致时显式警告——跨报告期混拼
        # (如年报营收×中报ROE)虽经新鲜度地板缓解仍可能发生，必须留痕可诊断。
        _anchor_dates = set()
        for _kk in (_k_rev, _k_np, _k_roe, _k_gp):
            if _kk and "[" in _kk:
                _mm = re.search(r"\[(\d{8})\]", _kk)
                if _mm:
                    _anchor_dates.add(_mm.group(1))
                    if "data_date" not in result or not result.get("data_date"):
                        result["data_date"] = _mm.group(1)
        if len(_anchor_dates) > 1:
            print(f"[hithink_fundamental] ⚠ 快照字段报告期不一致({_anchor_dates})，"
                  f"data_date 取首锚={result.get('data_date')}", file=sys.stderr)

        # ===== Step 2: 多年度趋势数据（分批查询，2026-05-30修复） =====
        # 同花顺API单次查询字段数有限制，按年份分批查
        # v5.3.4(D1): 年份窗口动态化——原硬编码 [2021..2025] 每过一年就少一年数据
        from date_utils import recent_year_window as _ryw
        years_multi = _ryw(5)
        multi_year = {}

        def _pct2dec(v):
            """v5.3.4(B2/审计P0-5): iwencai 返回百分数(15.3=15.3%)→小数(0.153)。
            multi_year_data 契约口径统一为**小数**（与 L1/_parse_pct 及 quick 评分
            函数的年报ROE阈值 >0.20 一致）；显示端负责 *100。"""
            return (v / 100) if isinstance(v, (int, float)) else None

        for year in years_multi:
            yr = str(year)
            # v5.4.1(P3): 当年年报尚未披露(还没到12月31日)时跳过该年查询——
            # 旧实现白打一次 API（该年 [yr]1231 键必然为空），且在 1~11 月
            # 每只股票都浪费一次配额。以当前月份判定(12月仍保留一次查询,
            # 给"部分公司提前披露"留通道)。
            if int(yr) == datetime.now().year and datetime.now().month < 12:
                continue
            q2 = (f"{symbol} 营业收入[{yr}1231] 归母净利润[{yr}1231] "
                  f"净资产收益率[{yr}1231] 销售毛利率[{yr}1231] "
                  f"销售净利率[{yr}1231] 资产负债率[{yr}1231] "
                  f"扣非归母净利润[{yr}1231] "
                  f"营业收入同比增长率[{yr}1231] 归母净利润同比增长率[{yr}1231]")
            ydatas = call_ithink(q2, FINANCE_SKILL)
            if ydatas:
                yitem = ydatas[0]
                multi_year[yr] = {
                    "revenue": get_val(yitem, [f"营业收入[{yr}1231]"]),
                    "net_profit": get_val(yitem, [f"归母净利润[{yr}1231]"]),
                    "deducted_profit": get_val(yitem, [f"扣非归母净利润[{yr}1231]"]),
                    "roe": _pct2dec(get_val(yitem, [f"净资产收益率[{yr}1231]"])),
                    "gp_margin": _pct2dec(get_val(yitem, [f"销售毛利率[{yr}1231]"])),
                    "np_margin": _pct2dec(get_val(yitem, [f"销售净利率[{yr}1231]"])),
                    "liability": _pct2dec(get_val(yitem, [f"资产负债率[{yr}1231]"])),
                    "revenue_yoy": _pct2dec(get_val(yitem, [f"营业收入同比增长率[{yr}1231]"])),
                    "profit_yoy": _pct2dec(get_val(yitem, [f"归母净利润同比增长率[{yr}1231]"])),
                }
        result["multi_year_data"] = multi_year

        # ===== Step 3: 组装数据结构 =====

        # ---- 盈利能力 ----
        profit_quality = None
        if net_profit and net_profit > 0 and deducted_profit:
            profit_quality = deducted_profit / net_profit  # 扣非/归母 比率
        result["profitability"] = {
            "roeAvg": roe / 100 if roe else None,
            "npMargin": np_margin / 100 if np_margin else None,
            "gpMargin": gp_margin / 100 if gp_margin else None,
            "netProfit": net_profit,
            "deductedProfit": deducted_profit,
            "profitQuality": profit_quality,  # 利润质量：扣非/归母
            "epsTTM": eps,
            "totalRevenue": revenue,
        }

        # ---- 成长能力 ----
        result["growth"] = {
            "YOYNI": profit_yoy / 100 if profit_yoy else None,
            "YOYRevenue": revenue_yoy / 100 if revenue_yoy else None,
            "revenue_yoy_pct": revenue_yoy,
            "profit_yoy_pct": profit_yoy,
        }

        # ---- 财务健康 ----
        cf_to_or = None
        if revenue and revenue > 0 and cfo:
            cf_to_or = cfo / revenue
        cf_to_np = None
        if net_profit and net_profit > 0 and cfo:
            cf_to_np = cfo / net_profit

        result["health"] = {
            "liabilityToAsset": liability / 100 if liability else None,
            "currentRatio": current_ratio,
            "quickRatio": quick_ratio,
            "CFOToOR": cf_to_or,
            "CFOToNP": cf_to_np,
            "totalAssets": total_assets,
        }

        # ===== Step 3.5: 估值历史分位（Baostock PE/PB 历史序列，v6.2 新增 2026-08-29） =====
        # 用于估值评分的极端分位惩罚。Baostock 查询 peTTM/pbMRQ 历史序列，
        # 用序列末值（最新交易日）作为当前PE/PB计算历史分位。
        # 失败静默（valuation_percentiles 留空，不惩罚）。
        result["valuation_percentiles"] = {}
        try:
            import baostock_utils
            import numpy as np
            _bs_code = ("sh." if symbol.startswith(("6", "9", "5")) else "sz.") + symbol
            # v6.2(2026-08-29): 必须持 BS_SESSION_LOCK——与 DataManager 的 Baostock
            # 调用共用同一把锁，否则并行编排下 session 串包死锁（紫光股份实测 13min+）。
            bs, _ = baostock_utils.ensure_login()
            with baostock_utils.BS_SESSION_LOCK:
                _rs = bs.query_history_k_data_plus(
                    _bs_code, "date,peTTM,pbMRQ",
                    start_date="2019-01-01", end_date="2026-12-31",
                    frequency="d", adjustflag="2")
                _pe_hist, _pb_hist = [], []
                while _rs.next():
                    _row = _rs.get_row_data()
                    try:
                        if _row[1]:
                            _pe_hist.append(float(_row[1]))
                        if _row[2]:
                            _pb_hist.append(float(_row[2]))
                    except (ValueError, IndexError):
                        pass
            _pe_valid = np.array([v for v in _pe_hist if 0 < v < 500])
            _pb_valid = np.array([v for v in _pb_hist if 0 < v < 50])
            # 当前PE/PB取历史序列末值（最新交易日）；为空则用 iwencai 的 pe/pb 兜底
            _cur_pe = _pe_valid[-1] if len(_pe_valid) > 0 else (pe if pe else None)
            _cur_pb = _pb_valid[-1] if len(_pb_valid) > 0 else (pb if pb else None)
            if _cur_pe and 0 < _cur_pe < 500 and len(_pe_valid) > 0:
                result["valuation_percentiles"]["pe_p"] = round(
                    float((_pe_valid <= _cur_pe).mean() * 100), 1)
            if _cur_pb and 0 < _cur_pb < 50 and len(_pb_valid) > 0:
                result["valuation_percentiles"]["pb_p"] = round(
                    float((_pb_valid <= _cur_pb).mean() * 100), 1)
        except Exception as _vp_err:
            print(f"[hithink_fundamental] 估值分位计算跳过: {_vp_err}", file=sys.stderr)

        # ===== Step 4: 估值数据 =====
        q3 = f"{symbol} 市盈率PE 市净率PB 股息率 总市值 动态市盈率"
        mdatas = call_ithink(q3, MARKET_SKILL)
        if mdatas:
            mitem = mdatas[0]
            # v5.3.4(D1): 估值快照日期动态化——原硬编码 [20260514]/[20260513]
            # 是时间炸弹。估值键按交易日快照存储，用最近工作日候选列表遍历，
            # 节假日自动跳到下一候选（get_val 取第一个非空）。
            from date_utils import recent_weekday_keys as _rwk
            _day_keys = _rwk(14)
            pe = get_val(mitem, [f"市盈率(pe,ttm)[{d}]" for d in _day_keys])
            pb = get_val(mitem, [f"市净率[{d}]" for d in _day_keys])
            div_yield = get_val(mitem, [f"股息率[{d}]" for d in _day_keys])
            mcap = get_val(mitem, [f"总市值[{d}]" for d in _day_keys])
            price = get_val(mitem, ["最新价"])

            result["valuation"]["peTTM"] = pe
            result["valuation"]["pbMRQ"] = pb
            result["valuation"]["market_cap_billion"] = round(mcap / 1e8, 2) if mcap else None
            if price:
                result["valuation"]["latest_price"] = price
            if mcap:
                result["market_cap"] = mcap
            if div_yield:
                result["dividend_yield"] = div_yield / 100

        # ===== Step 5: 行业分类 =====
        name = result.get("name", "")
        if "眼科" in name or "医疗" in name:
            result["industry"] = "医疗器械"
            result["industry_classification"] = "医药生物"
        elif "银行" in name:
            result["industry"] = "银行"
            result["industry_classification"] = "金融"
        elif "证券" in name:
            result["industry"] = "证券"
            result["industry_classification"] = "金融"
        elif "保险" in name:
            result["industry"] = "保险"
            result["industry_classification"] = "金融"

        # v6.2(2026-08-29): 行业兜底——上述硬编码仅覆盖少数关键词，绝大多数
        # 股票（如紫光股份）industry 仍为""。用 Baostock query_stock_industry 补行业
        # （稳定可用，akshare_fundamental.py 同款；不用 AKShare 东财接口因其无常超时
        # 重试导致长挂起，2026-08-29 紫光股份实测单调用卡14分钟）。
        if not result.get("industry_classification"):
            try:
                import baostock_utils
                # v6.2(2026-08-29): 必须持 BS_SESSION_LOCK——与 DataManager 的 Baostock
                # 调用共用同一把锁，否则并行编排下 session 串包死锁。
                bs, _ = baostock_utils.ensure_login()
                with baostock_utils.BS_SESSION_LOCK:
                    _rs = bs.query_stock_industry(("sh." if symbol.startswith(("6", "9", "5")) else "sz.") + symbol)
                    _ind_rows = []
                    while _rs.next():
                        _ind_rows.append(_rs.get_row_data())
                if _ind_rows:
                    _bs_ind = _ind_rows[0][3] if len(_ind_rows[0]) > 3 else (_ind_rows[0][1] if len(_ind_rows[0]) > 1 else "")
                    if _bs_ind:
                        result["industry"] = _bs_ind
                        # 证监会行业分类（C39计算机、通信...）或东财行业→一级分类粗映射
                        _cls_map = {
                            "计算机": "科技", "电子": "科技", "通信": "科技",
                            "医药生物": "医药", "农林牧渔": "周期",
                            "银行": "金融", "非银金融": "金融", "房地产": "周期",
                            "钢铁": "周期", "有色金属": "周期", "化工": "周期",
                            "汽车": "周期", "机械设备": "周期", "电气设备": "成长",
                            "食品饮料": "蓝筹", "家用电器": "蓝筹", "商贸零售": "周期",
                            "公用事业": "蓝筹", "交通运输": "周期", "建筑装饰": "周期",
                            "国防军工": "科技", "传媒": "成长", "社会服务": "周期",
                        }
                        _mapped = _cls_map.get(_bs_ind)
                        if not _mapped:
                            for _k, _v in _cls_map.items():
                                if _k in _bs_ind:
                                    _mapped = _v
                                    break
                        result["industry_classification"] = _mapped or _bs_ind
            except Exception as _ind_err:
                print(f"[hithink_fundamental] 行业兜底失败(保持空): {_ind_err}",
                      file=sys.stderr)

        # ===== Step 6: 趋势分析 =====
        result["trend_analysis"] = analyze_trend(multi_year, result["profitability"])

        # ===== Step 7: 计算基本面评分 =====
        # v5.3.4(B1): 统一至 quick_fundamental 版（v5.0口径），与 L1/A500 同源可比；
        # 传入多年度数据以启用年报ROE评分+趋势修正+成长持续性子项。
        # v6.2(2026-08-29): 传入估值历史分位（Baostock K线计算）以启用极端分位惩罚。
        _vp = result.get("valuation_percentiles") or {}
        result["fundamental_score"] = _unified_calc_score(
            result, multi_year_data=result.get("multi_year_data"),
            valuation_percentiles=_vp or None)
        result["stock_type_hint"] = classify_stock_type(result)

        return result

    except Exception as e:
        import traceback
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        result["confidence"] = 0
        return result


def analyze_trend(multi_year, profitability):
    """分析4年趋势，返回趋势判定和说明

    v5.3.4(B2/审计P0-5): multi_year_data 契约口径已统一为**小数**
    （roe/gp_margin/np_margin/liability/双yoy 均为 0.153=15.3% 形式，
    与 L1 AKShare 及 quick 评分函数一致）。本函数所有百分比展示一律
    `值*100:.1f%`；趋势方向/得分逻辑基于差值，与口径无关。
    """
    analysis = {
        "roe_trend": {"direction": "未知", "score": 0, "detail": ""},
        "revenue_trend": {"direction": "未知", "score": 0, "detail": ""},
        "margin_trend": {"direction": "未知", "score": 0, "detail": ""},
        "liability_trend": {"direction": "未知", "score": 0, "detail": ""},
        "profit_quality": {"score": 0, "detail": ""},
        "overall_trend": "未知",
        "overall_score": 0,
    }

    # 提取年度数据（v5.3.4(D1): 年份窗口动态化，取实际键中最近5个自然年）
    _trend_years = sorted([y for y in multi_year.keys() if str(y).isdigit()])[-5:]
    roes = []
    revenues = []
    margins = []
    liabilities = []
    deducted_ratios = []
    for yr in _trend_years:
        if yr in multi_year:
            y = multi_year[yr]
            if y.get("roe") is not None:
                roes.append((yr, y["roe"]))
            if y.get("revenue") is not None:
                revenues.append((yr, y["revenue"]))
            if y.get("gp_margin") is not None:
                margins.append((yr, y["gp_margin"]))
            if y.get("liability") is not None:
                liabilities.append((yr, y["liability"]))
            if y.get("net_profit") and y.get("net_profit") > 0 and y.get("deducted_profit"):
                deducted_ratios.append(y["deducted_profit"] / y["net_profit"])

    def trend_direction(values):
        """判定趋势方向：上升/下降/波动（v2：加入整体方向+纯单边判定）"""
        if len(values) < 3:
            return "数据不足", 0
        changes = [values[i][1] - values[i-1][1] for i in range(1, len(values))]
        up_count = sum(1 for c in changes if c > 0)
        down_count = sum(1 for c in changes if c < 0)
        total_change = values[-1][1] - values[0][1]  # 首尾差值（整体方向）

        # 纯单边：所有变化同方向
        if down_count == len(changes):
            return "持续下降", -1
        if up_count == len(changes):
            return "持续上升", 1

        # 混合方向：看多数方向+最后变化+整体方向
        if down_count > up_count and changes[-1] < 0 and total_change < 0:
            return "震荡下降", -0.5
        elif up_count > down_count and changes[-1] > 0 and total_change > 0:
            return "震荡上升", 0.5
        elif down_count >= 2 and changes[-1] > 0:
            return "先降后升", 0
        elif up_count >= 2 and changes[-1] < 0:
            return "先升后降", 0

        # 幅度占主导：整体方向决定趋势
        if total_change > abs(values[0][1]) * 0.05:  # 整体升幅>5%
            return "近期回升", 0.5
        elif total_change < -abs(values[0][1]) * 0.05:  # 整体降幅>5%
            return "近期下降", -0.5
        elif changes[-1] > 0:
            return "近期回升", 0.5
        elif changes[-1] < 0:
            return "近期下降", -0.5
        return "波动", 0

    # ---- ROE趋势 ----
    if len(roes) >= 3:
        direction, sign = trend_direction(roes)
        analysis["roe_trend"]["direction"] = direction
        analysis["roe_trend"]["values"] = [v for _, v in roes]
        if "持续下降" in direction:
            analysis["roe_trend"]["score"] = -5
            analysis["roe_trend"]["detail"] = f"ROE从{roes[0][1]*100:.1f}%持续降至{roes[-1][1]*100:.1f}%，盈利能力趋势恶化"
        elif "先降后升" in direction:
            analysis["roe_trend"]["score"] = 2
            analysis["roe_trend"]["detail"] = f"ROE先降后升，出现拐点信号"
        elif "持续上升" in direction:
            analysis["roe_trend"]["score"] = 3
            analysis["roe_trend"]["detail"] = f"ROE从{roes[0][1]*100:.1f}%持续升至{roes[-1][1]*100:.1f}%，盈利能力增强"
        elif "近期回升" in direction:
            analysis["roe_trend"]["score"] = 2
            analysis["roe_trend"]["detail"] = f"ROE近期回升至{roes[-1][1]*100:.1f}%"
        elif "近期下降" in direction:
            analysis["roe_trend"]["score"] = -2
            analysis["roe_trend"]["detail"] = f"ROE近期降至{roes[-1][1]*100:.1f}%"
        elif "震荡上升" in direction:
            # v5.3.4(B2补): 原缺失震荡/波动分支导致 detail 为空（对齐 quick 版语义）
            analysis["roe_trend"]["score"] = 1
            analysis["roe_trend"]["detail"] = f"ROE震荡上升至{roes[-1][1]*100:.1f}%，波动较大"
        elif "震荡下降" in direction:
            analysis["roe_trend"]["score"] = -1
            analysis["roe_trend"]["detail"] = f"ROE震荡下降至{roes[-1][1]*100:.1f}%，波动较大"
        else:
            analysis["roe_trend"]["score"] = 0
            analysis["roe_trend"]["detail"] = f"ROE趋势不明，最新{roes[-1][1]*100:.1f}%"
    else:
        analysis["roe_trend"]["detail"] = "ROE数据不足(需3年以上)"

    # ---- 营收趋势 ----
    if len(revenues) >= 3:
        direction, sign = trend_direction(revenues)
        analysis["revenue_trend"]["direction"] = direction
        if "持续下降" in direction:
            analysis["revenue_trend"]["score"] = -3
            analysis["revenue_trend"]["detail"] = f"营收持续下降"
        elif "持续上升" in direction:
            analysis["revenue_trend"]["score"] = 3
            analysis["revenue_trend"]["detail"] = f"营收持续增长"
        elif "先升后降" in direction:
            analysis["revenue_trend"]["score"] = -2
            analysis["revenue_trend"]["detail"] = f"营收增速放缓"
        elif "近期回升" in direction:
            analysis["revenue_trend"]["score"] = 2
            analysis["revenue_trend"]["detail"] = f"营收近期回升"
        else:
            analysis["revenue_trend"]["score"] = 0
            analysis["revenue_trend"]["detail"] = f"营收趋势平稳"

    # ---- 毛利率趋势 ----
    if len(margins) >= 3:
        direction, sign = trend_direction(margins)
        analysis["margin_trend"]["direction"] = direction
        if "持续下降" in direction:
            analysis["margin_trend"]["score"] = -4
            analysis["margin_trend"]["detail"] = f"毛利率从{margins[0][1]*100:.1f}%持续降至{margins[-1][1]*100:.1f}%，定价权或成本控制恶化"
        elif "先降后升" in direction:
            analysis["margin_trend"]["score"] = 2
            analysis["margin_trend"]["detail"] = f"毛利率先降后升，出现拐点"
        elif "持续上升" in direction:
            analysis["margin_trend"]["score"] = 3
            analysis["margin_trend"]["detail"] = f"毛利率持续提升，定价权增强"
        elif "近期回升" in direction:
            analysis["margin_trend"]["score"] = 2
            analysis["margin_trend"]["detail"] = f"毛利率近期回升至{margins[-1][1]*100:.1f}%"
        elif "近期下降" in direction:
            analysis["margin_trend"]["score"] = -2
            analysis["margin_trend"]["detail"] = f"毛利率近期降至{margins[-1][1]*100:.1f}%"

    # ---- 负债率趋势 ----
    if len(liabilities) >= 3:
        direction, sign = trend_direction(liabilities)
        analysis["liability_trend"]["direction"] = direction
        if "持续上升" in direction:
            if liabilities[-1][1] > 0.60:  # v5.3.4(B2): 小数口径，60%=0.60
                analysis["liability_trend"]["score"] = -3
                analysis["liability_trend"]["detail"] = f"负债率持续上升至{liabilities[-1][1]*100:.1f}%，财务杠杆加大"
            else:
                analysis["liability_trend"]["score"] = -1
                analysis["liability_trend"]["detail"] = f"负债率微升至{liabilities[-1][1]*100:.1f}%"
        elif "持续下降" in direction:
            analysis["liability_trend"]["score"] = 2
            analysis["liability_trend"]["detail"] = f"负债率持续下降至{liabilities[-1][1]*100:.1f}%，财务结构优化"
        else:
            analysis["liability_trend"]["score"] = 0
            analysis["liability_trend"]["detail"] = f"负债率稳定在{liabilities[-1][1]*100:.1f}%左右"

    # ---- 利润质量（扣非/归母比率） ----
    if deducted_ratios:
        avg_ratio = sum(deducted_ratios) / len(deducted_ratios)
        if avg_ratio >= 0.95:
            analysis["profit_quality"]["score"] = 3
            analysis["profit_quality"]["detail"] = (f"扣非净利润占归母净利润{avg_ratio*100:.0f}%，"
                                                    f"利润质量高（非经常性损益占比小）")
        elif avg_ratio >= 0.85:
            analysis["profit_quality"]["score"] = 1
            analysis["profit_quality"]["detail"] = (f"扣非净利润占归母净利润{avg_ratio*100:.0f}%，"
                                                    f"利润质量一般")
        else:
            analysis["profit_quality"]["score"] = -2
            analysis["profit_quality"]["detail"] = (f"扣非净利润仅占归母净利润{avg_ratio*100:.0f}%，"
                                                    f"利润依赖非经常性损益")
        analysis["profit_quality"]["avg_ratio"] = round(avg_ratio, 4)

    # 最新扣非质量
    latest_pq = profitability.get("profitQuality")
    if latest_pq is not None:
        analysis["profit_quality"]["latest_ratio"] = round(latest_pq, 4)

    # ---- 综合 ----
    total_trend_score = sum([
        analysis["roe_trend"]["score"],
        analysis["revenue_trend"]["score"],
        analysis["margin_trend"]["score"],
        analysis["liability_trend"]["score"],
        analysis["profit_quality"]["score"],
    ])
    analysis["overall_score"] = total_trend_score

    if total_trend_score >= 5:
        analysis["overall_trend"] = "趋势向好"
    elif total_trend_score >= 0:
        analysis["overall_trend"] = "趋势平稳"
    elif total_trend_score >= -8:
        analysis["overall_trend"] = "趋势走弱"
    else:
        analysis["overall_trend"] = "趋势恶化"

    # ---- 结构性断点检测（v6.2 新增，2026-08-29） ----
    # 检测疑似重大资本运作/并表导致的财务指标跳变（如举债收购导致负债率跳升、
    # 增发导致每股净资产跳变）。遍历所有相邻年对，任一跳变超阈值即告警。
    structural_breaks = []
    for i in range(1, len(liabilities)):
        liab_pp_change = (liabilities[i][1] - liabilities[i-1][1]) * 100  # 百分点变化
        if abs(liab_pp_change) >= 20:
            structural_breaks.append({
                "metric": "资产负债率",
                "change_pp": round(liab_pp_change, 1),
                "from_year": liabilities[i-1][0],
                "to_year": liabilities[i][0],
                "alert": "疑似重大资本运作（举债收购/并表）导致负债率跳变，跨期对比需注明口径变化"
            })
    # 每股净资产跳变检测（multi_year 含 book_value_per_share 时触发；
    # 紫光股份案例：L1 iwencai 路径 multi_year 不含 bvps，故此告警需 AKShare 补充）
    _bv_series = []
    for yr in _trend_years:
        if yr in multi_year and multi_year[yr].get("bvps") is not None:
            _bv_series.append((yr, multi_year[yr]["bvps"]))
    for i in range(1, len(_bv_series)):
        bv_prev, bv_last = _bv_series[i-1][1], _bv_series[i][1]
        if bv_prev and abs(bv_last - bv_prev) / abs(bv_prev) > 0.50:
            structural_breaks.append({
                "metric": "每股净资产",
                "change_pct": round((bv_last / bv_prev - 1) * 100, 1),
                "from_year": _bv_series[i-1][0],
                "to_year": _bv_series[i][0],
                "alert": "疑似增发/并购导致每股净资产跳变，EPS/ROE历史可比性下降"
            })
    analysis["structural_breaks"] = structural_breaks

    return analysis


def classify_stock_type(data):
    """根据财务数据给出股票类型倾向"""
    roe = data.get("profitability", {}).get("roeAvg")
    np_margin = data.get("profitability", {}).get("npMargin")
    yoy_ni = data.get("growth", {}).get("YOYNI")
    industry = data.get("industry", "")
    market_cap = data.get("market_cap", 0)

    blue_chip_industries = ["银行", "保险", "证券", "白酒", "食品", "消费", "金融",
                            "公用事业", "交通运输", "白色家电"]
    growth_industries = ["科技", "医药", "新能源", "半导体", "软件", "人工智能",
                         "通信", "医疗器械", "安防"]
    cycle_industries = ["化工", "钢铁", "有色", "煤炭", "房地产", "地产",
                        "农牧", "建材", "石油"]

    industry_type = None
    for bi in blue_chip_industries:
        if bi in industry:
            industry_type = "蓝筹"
            break
    if not industry_type:
        for gi in growth_industries:
            if gi in industry:
                industry_type = "成长"
                break
    if not industry_type:
        for ci in cycle_industries:
            if ci in industry:
                industry_type = "周期"
                break

    hints = []
    if industry_type:
        hints.append(industry_type)

    if roe is not None and yoy_ni is not None:
        if roe > 0.20 and yoy_ni > 0.20:
            if industry_type != "蓝筹":
                hints.append("成长")
        elif roe > 0.15 and np_margin is not None and np_margin > 0.15:
            hints.append("蓝筹")

    if market_cap and market_cap > 1000e8:
        hints.append("蓝筹")
    elif market_cap and market_cap < 100e8:
        hints.append("成长")

    if not hints:
        return "混合"
    counter = Counter(hints)
    return counter.most_common(1)[0][0]


def calculate_fundamental_score_legacy_hithink(data):
    """[DEPRECATED v5.3.4 审计P0-4/B1] 旧版四维各25分评分。

    与 quick_fundamental.calculate_fundamental_score（现唯一实现：健康20/
    估值10/键名 marginal_improvement/年报ROE口径）存在权重、键名、口径三重
    分歧，曾导致同一股票经不同数据层级返回不可比的分数与字典结构。
    已停止调用；按"丢失的细节需外移"原则暂存原文，待 git 版本管理建立后删除。
    请勿在新代码中引用。"""
    scores = {
        "profitability_score": 0,
        "growth_score": 0,
        "health_score": 0,
        "valuation_score": 0,
        "total_score": 0,
        "margin_improvement": 0,
        "trend_correction": 0,       # 趋势修正
        "trend_correction_detail": "", # 修正说明
        "roe_std": None,
        "revenue_volatility": None,
    }

    profitability = data.get("profitability", {})
    growth = data.get("growth", {})
    health = data.get("health", {})
    valuation = data.get("valuation", {})
    trend = data.get("trend_analysis", {})

    roe = profitability.get("roeAvg")
    np_margin = profitability.get("npMargin")
    gp_margin = profitability.get("gpMargin")
    eps = profitability.get("epsTTM")

    yoy_ni = growth.get("YOYNI")
    yoy_rev = growth.get("YOYRevenue")

    liability = health.get("liabilityToAsset")
    cf_to_or = health.get("CFOToOR")

    pe = valuation.get("peTTM")
    pb = valuation.get("pbMRQ")
    div_yield = data.get("dividend_yield")
    market_cap = data.get("market_cap")

    industry = data.get("industry", "")

    # ===== 1. 盈利能力 (25分) =====
    p_score = 0
    if roe is not None:
        if roe > 0.20: p_score += 10
        elif roe > 0.15: p_score += 8
        elif roe > 0.10: p_score += 5
        elif roe > 0.05: p_score += 3
        else: p_score += 1
    else:
        p_score += 5

    if np_margin is not None:
        if np_margin > 0.20: p_score += 8
        elif np_margin > 0.10: p_score += 5
        elif np_margin > 0.05: p_score += 3
        else: p_score += 1
    else:
        p_score += 4

    if gp_margin is not None:
        if gp_margin > 0.40: p_score += 7
        elif gp_margin > 0.20: p_score += 4
        elif gp_margin > 0.10: p_score += 2
        else: p_score += 0
    else:
        if np_margin is not None and np_margin > 0.15:
            p_score += 5
        else:
            p_score += 3

    scores["profitability_score"] = min(25, p_score)

    # ===== 2. 成长性 (25分) =====
    g_score = 0
    if yoy_ni is not None:
        if yoy_ni > 0.30: g_score += 10
        elif yoy_ni > 0.15: g_score += 7
        elif yoy_ni > 0: g_score += 4
        elif yoy_ni > -0.10: g_score += 2
        else: g_score += 0
    else:
        g_score += 5

    if yoy_rev is not None:
        if yoy_rev > 0.15: g_score += 8
        elif yoy_rev > 0.05: g_score += 5
        elif yoy_rev > 0: g_score += 3
        else: g_score += 0
    else:
        g_score += 4

    if eps is not None:
        if eps > 0.50: g_score += 7
        elif eps > 0.20: g_score += 5
        elif eps > 0.10: g_score += 3
        elif eps > 0: g_score += 1
        else: g_score += 0
    else:
        g_score += 4

    scores["growth_score"] = min(25, g_score)

    # ===== 3. 财务健康 (25分) =====
    h_score = 0
    is_bank = "银行" in industry or "保险" in industry

    if liability is not None:
        if is_bank:
            h_score += 8
        elif liability < 0.30:
            h_score += 10
        elif liability < 0.50:
            h_score += 8
        elif liability < 0.70:
            h_score += 5
        elif liability < 0.85:
            h_score += 3
        else:
            h_score += 0
    else:
        h_score += 5

    if cf_to_or is not None:
        if cf_to_or > 0.20: h_score += 10
        elif cf_to_or > 0.10: h_score += 7
        elif cf_to_or > 0: h_score += 4
        elif cf_to_or > -0.10: h_score += 2
        else: h_score += 0
    else:
        h_score += 5

    current_ratio = health.get("currentRatio")
    if current_ratio is not None and not is_bank:
        if current_ratio > 2.0: h_score += 7
        elif current_ratio > 1.5: h_score += 5
        elif current_ratio > 1.0: h_score += 3
        else: h_score += 0
    else:
        h_score += 4

    scores["health_score"] = min(25, h_score)

    # ===== 4. 估值水平 (25分) =====
    v_score = 0

    if pe is not None and pe > 0 and not is_bank:
        if pe < 10: v_score += 10
        elif pe < 20: v_score += 8
        elif pe < 30: v_score += 5
        elif pe < 50: v_score += 3
        else: v_score += 0
    elif pe is not None and is_bank:
        if pe < 6: v_score += 10
        elif pe < 10: v_score += 8
        else: v_score += 5
    else:
        v_score += 5

    if pb is not None and pb > 0:
        if pb < 1.0: v_score += 8
        elif pb < 2.0: v_score += 6
        elif pb < 4.0: v_score += 4
        elif pb < 8.0: v_score += 2
        else: v_score += 0
    else:
        v_score += 4

    if div_yield is not None:
        if div_yield > 0.04: v_score += 7
        elif div_yield > 0.03: v_score += 5
        elif div_yield > 0.02: v_score += 4
        elif div_yield > 0.01: v_score += 2
        else: v_score += 0
    else:
        v_score += 4

    scores["valuation_score"] = min(25, v_score)

    # ===== 5. 边际改善 (15分) =====
    mi_score = 0

    if yoy_rev is not None and yoy_ni is not None:
        if yoy_ni > yoy_rev > 0:
            mi_score += 5
        elif yoy_ni > 0 and yoy_rev > 0:
            mi_score += 3
        elif yoy_ni > 0:
            mi_score += 1

    if gp_margin is not None and np_margin is not None:
        if gp_margin > 0.25 and np_margin > 0.10:
            mi_score += 5
        elif gp_margin > 0.15 and np_margin > 0.05:
            mi_score += 3
        elif gp_margin > 0.10:
            mi_score += 1

    if cf_to_or is not None and yoy_ni is not None:
        if cf_to_or > 0.10 and yoy_ni > 0:
            mi_score += 5
        elif cf_to_or > 0:
            mi_score += 2

    scores["margin_improvement"] = mi_score

    # ===== 6. 趋势修正 =====
    trend_correction = trend.get("overall_score", 0)
    scores["trend_correction"] = trend_correction
    scores["trend_correction_detail"] = trend.get("overall_trend", "")

    # 总评分 = snapshot(4维度) + 边际改善 + 趋势修正
    snapshot_total = min(25, p_score) + min(25, g_score) + min(25, h_score) + min(25, v_score)
    scores["total_score"] = min(100, max(0, snapshot_total + mi_score + trend_correction))

    return scores


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "300015"
    result = get_fundamentals(symbol)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))