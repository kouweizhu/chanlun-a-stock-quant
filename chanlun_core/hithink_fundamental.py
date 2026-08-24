"""快速获取结构化财务数据 - 输出 JSON
数据源: 同花顺 hithink-finance-query + hithink-market-query
v2.0 — 2026-05-14
  新增: 扣非净利润(最新+4年趋势)
  新增: 4年趋势分析 + 趋势修正评分
  新增: trend_analysis 输出段供报告使用
"""
import sys, json, os, math, subprocess
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


def call_ithink(query: str, skill_id: str) -> list:
    """调用同花顺 query2data 接口，返回 datas 列表"""
    if not API_KEY:
        return []
    try:
        resp = subprocess.run(
            ["curl", "-s", "-X", "POST", API_URL,
             "-H", "Content-Type: application/json",
             "-H", f"Authorization: Bearer {API_KEY}",
             "-H", f"X-Claw-Skill-Id: {skill_id}",
             "-H", "X-Claw-Skill-Version: 1.0.0",
             "-d", json.dumps({
                 "query": query, "page": "1", "limit": "5",
                 "is_cache": "0", "expand_index": "true"
             })],
            capture_output=True, text=True, timeout=30
        )
        d = json.loads(resp.stdout)
        return d.get("datas", [])
    except Exception as e:
        return []


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


def safe_float(val):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


def get_fundamentals(symbol):
    """主函数：结构化财务数据获取（v5.3.3-H 数据源重构, 2026-08-23）

    三级数据源调度（批次G经验: AKShare 同花顺摘要字段最全——扣非/双同比/
    周转天数一应俱全; sina 兜底源的扣非/ROE/毛利率为硬编码 None 不可靠）:

      L1 主源   AKShare stock_financial_abstract_ths + Baostock 估值/行业
                （零 key, akshare_fundamental.get_fundamentals_akshare）
      L2 增强   同花顺 query2data（仅 IWENCAI_API_KEY 配置时; 补股息率等）
      L3 兜底   新浪三表（零鉴权, 扣非/ROE/毛利率缺失, confidence=3）

    返回契约与 v2.0 一致（profitability/growth/health/multi_year_data/
    trend_analysis/fundamental_score...），下游 single_stock_analysis.py
    及报告 agent 无需改动。
    """
    # ── L1: AKShare 主源 ──
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
            return _normalize_to_hithink(r)
    except Exception as _e:
        print(f"[hithink_fundamental] L1 AKShare 主源异常，降级: {_e}", file=sys.stderr)

    # ── L2: iwencai 增强（仅有 key 时）──
    if API_KEY:
        r = _get_fundamentals_iwencai(symbol)
        if r and not r.get("error"):
            return r

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
    """L2: 原 v2.0 同花顺 query2data 主源逻辑（现降级为 key 可用时的增强源）"""
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
        q1 = (f"{symbol} 营业收入 归母净利润 ROE 销售毛利率 销售净利率 资产负债率 "
              "营收同比增速 净利润同比增速 每股收益 每股净资产 每股经营现金流 "
              "扣非净利润 流动比率 速动比率 经营活动现金流净额 总资产")
        fdatas = call_ithink(q1, FINANCE_SKILL)
        if not fdatas:
            # 同花顺主源失败 → 降级到新浪三表（em_utils.sina_financial_report，零鉴权）
            backup = _sina_fundamentals_backup(symbol, result)
            if backup:
                result = backup
                return result
            result["error"] = "同花顺财务API无返回数据"
            result["confidence"] = 1
            return result

        item = fdatas[0]
        result["name"] = get_str(item, ["股票简称"])

        # v5.3.4(D1): 报告期键动态化——原硬编码 [20260331]/[20251231] 是时间炸弹
        # （2026年9月起一季报键将查不到任何数据）。按披露日历推断最新两期。
        from date_utils import latest_report_dates as _lrd
        _rp1, _rp0 = _lrd()
        _K = [f"[{_rp1}]", f"[{_rp0}]"]  # 键后缀模板

        revenue = get_val(item, [f"营业收入{_K[0]}", f"营业收入{_K[1]}"])
        net_profit = get_val(item, [f"归母净利润{_K[0]}", f"归母净利润{_K[1]}"])
        roe = get_val(item, [f"净资产收益率{_K[0]}", f"净资产收益率{_K[1]}"])
        gp_margin = get_val(item, [f"销售毛利率{_K[0]}", f"销售毛利率{_K[1]}"])
        np_margin = get_val(item, [f"销售净利率{_K[0]}", f"销售净利率{_K[1]}"])
        liability = get_val(item, [f"资产负债率{_K[0]}", f"资产负债率{_K[1]}"])
        eps = get_val(item, [f"基本每股收益{_K[0]}", f"基本每股收益{_K[1]}"])
        bvps = get_val(item, [f"每股净资产{_K[0]}", f"每股净资产{_K[1]}"])
        cfops = get_val(item, [f"每股经营活动产生的现金流量净额{_K[0]}",
                                f"每股经营活动产生的现金流量净额{_K[1]}"])
        revenue_yoy = get_val(item, [f"营业收入同比增长率{_K[0]}",
                                     f"营业收入同比增长率{_K[1]}"])
        profit_yoy = get_val(item, [f"归母净利润同比增长率{_K[0]}",
                                    f"归母净利润同比增长率{_K[1]}"])
        current_ratio = get_val(item, [f"流动比率{_K[0]}", f"流动比率{_K[1]}"])
        quick_ratio = get_val(item, [f"速动比率{_K[0]}", f"速动比率{_K[1]}"])
        total_assets = get_val(item, [f"总资产{_K[0]}", f"总资产{_K[1]}"])
        cfo = get_val(item, [f"经营活动产生的现金流量净额{_K[0]}",
                              f"经营活动产生的现金流量净额{_K[1]}"])
        deducted_profit = get_val(item, [f"扣非归母净利润{_K[0]}",
                                         f"扣非归母净利润{_K[1]}"])

        # 判断数据日期（v5.3.4-D1: 与上面动态键一致）
        if get_val(item, [f"营业收入{_K[0]}"]) is not None:
            result["data_date"] = _rp1
        elif get_val(item, [f"营业收入{_K[1]}"]) is not None:
            result["data_date"] = _rp0

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

        # ===== Step 6: 趋势分析 =====
        result["trend_analysis"] = analyze_trend(multi_year, result["profitability"])

        # ===== Step 7: 计算基本面评分 =====
        # v5.3.4(B1): 统一至 quick_fundamental 版（v5.0口径），与 L1/A500 同源可比；
        # 传入多年度数据以启用年报ROE评分+趋势修正+成长持续性子项。
        result["fundamental_score"] = _unified_calc_score(
            result, multi_year_data=result.get("multi_year_data"))
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