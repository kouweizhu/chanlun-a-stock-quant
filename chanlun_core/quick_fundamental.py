"""快速获取结构化财务数据 - 输出 JSON
数据源优先级: Baostock(Python库) → Agent层 investoday MCP(桥接文件) → 失败标记
"""
import sys, json, os, math
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime, timedelta
sys.path.insert(0, r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core")
import baostock_utils  # noqa: E402 — print redirect + session manager
from baostock_utils import to_bs_code  # noqa: E402

def get_fundamentals(symbol):
    """主函数：获取结构化财务数据"""
    
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
        "data_source": "baostock",
        "confidence": 5,  # 1-5
        "error": None
    }
    
    try:
        bs, lg = baostock_utils.ensure_login()
        if lg is None or lg.error_code != '0':
            result["error"] = f"Baostock login failed: {lg.error_msg if lg else 'session invalid'}"
            result["confidence"] = 1
            return result
        
        bs_code = to_bs_code(symbol)
        
        # ---- 1. 基本信息 & 行业 ----
        rs = bs.query_stock_basic(bs_code)
        basic = rs.get_data()
        if not basic.empty:
            result["name"] = basic["code_name"].values[0]
        
        rs = bs.query_stock_industry(bs_code)
        ind = rs.get_data()
        if not ind.empty:
            result["industry"] = ind["industry"].values[0]
            result["industry_classification"] = ind["industryClassification"].values[0]
        
        # ---- 2. 最新年度财务数据 (取最近有数据的年份) ----
        today = datetime.now()
        current_year = today.year
        
        profit_data = None
        growth_data = None
        balance_data = None
        cashflow_data = None
        
        # 尝试最近3年的Q4(年报)，找有数据的
        for y in range(current_year, current_year - 3, -1):
            if profit_data is None:
                rs = bs.query_profit_data(bs_code, year=y, quarter=4)
                d = rs.get_data()
                if not d.empty:
                    profit_data = d
            
            if growth_data is None:
                rs = bs.query_growth_data(bs_code, year=y, quarter=4)
                d = rs.get_data()
                if not d.empty:
                    growth_data = d
            
            if balance_data is None:
                rs = bs.query_balance_data(bs_code, year=y, quarter=4)
                d = rs.get_data()
                if not d.empty:
                    balance_data = d
            
            if cashflow_data is None:
                rs = bs.query_cash_flow_data(bs_code, year=y, quarter=4)
                d = rs.get_data()
                if not d.empty:
                    cashflow_data = d
        
        # ---- 3. 盈利能力 ----
        if profit_data is not None:
            p = profit_data.iloc[0]
            result["data_date"] = p.get("statDate", "")
            result["profitability"] = {
                "roeAvg": safe_float(p.get("roeAvg")),
                "npMargin": safe_float(p.get("npMargin")),
                "gpMargin": safe_float(p.get("gpMargin")),
                "netProfit": safe_float(p.get("netProfit")),
                "epsTTM": safe_float(p.get("epsTTM")),
                "totalRevenue": safe_float(p.get("MBRevenue")),
                "totalShare": safe_float(p.get("totalShare")),
            }
        
        # ---- 4. 成长能力 ----
        if growth_data is not None:
            g = growth_data.iloc[0]
            result["growth"] = {
                "YOYNI": safe_float(g.get("YOYNI")),         # 净利润同比
                "YOYEquity": safe_float(g.get("YOYEquity")), # 净资产同比
                "YOYAsset": safe_float(g.get("YOYAsset")),   # 总资产同比
                "YOYEPSBasic": safe_float(g.get("YOYEPSBasic")),  # EPS同比
            }
        
        # ---- 5. 财务健康 ----
        if balance_data is not None:
            b = balance_data.iloc[0]
            result["health"] = {
                "liabilityToAsset": safe_float(b.get("liabilityToAsset")),  # 资产负债率
                "assetToEquity": safe_float(b.get("assetToEquity")),       # 权益乘数
                "currentRatio": safe_float(b.get("currentRatio")),         # 流动比率
            }
        
        if cashflow_data is not None:
            cf = cashflow_data.iloc[0]
            result["health"]["CFOToOR"] = safe_float(cf.get("CFOToOR"))    # 经营现金流/营收
            result["health"]["CFOToNP"] = safe_float(cf.get("CFOToNP"))    # 经营现金流/净利
        
        # ---- 6. 估值 ----
        # PE/PB 从最近日线数据获取
        try:
            end = today.strftime("%Y-%m-%d")
            start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
            rs = bs.query_history_k_data_plus(
                bs_code, 'date,close,peTTM,pbMRQ', 
                start_date=start, end_date=end, frequency='d',
                adjustflag='2'
            )
            kdata = rs.get_data()
            if not kdata.empty:
                latest = kdata.iloc[-1]
                close = safe_float(latest.get("close"))
                pe = safe_float(latest.get("peTTM"))
                pb = safe_float(latest.get("pbMRQ"))
                result["valuation"]["peTTM"] = pe
                result["valuation"]["pbMRQ"] = pb
                result["valuation"]["latest_price"] = close
                
                # 总市值 = 最新价 × 总股本
                total_share = result.get("profitability", {}).get("totalShare", 0)
                if total_share and close:
                    result["market_cap"] = round(total_share * close, 2)
                    result["valuation"]["market_cap_billion"] = round(result["market_cap"] / 1e8, 2)
        except Exception as e:
            result["valuation"]["pe_pb_error"] = str(e)
        
        # ---- 7. 股息率 ----
        try:
            # 查最近3年的每股税前股息，取平均值
            div_sum = 0
            div_count = 0
            for y in range(current_year - 1, current_year - 4, -1):
                rs = bs.query_dividend_data(bs_code, year=y, yearType='operate')
                dd = rs.get_data()
                if not dd.empty and not dd.empty:
                    cash_ps = safe_float(dd["dividCashPsBeforeTax"].values[0])
                    if cash_ps and cash_ps > 0:
                        div_sum += cash_ps
                        div_count += 1
            
            if div_count > 0:
                avg_dividend = div_sum / div_count
                latest_price = result.get("valuation", {}).get("latest_price", 0)
                if latest_price and latest_price > 0:
                    result["dividend_yield"] = round(avg_dividend / latest_price, 4)
                    result["valuation"]["dividend_per_share"] = round(avg_dividend, 4)
        except Exception as e:
            pass  # 股息数据缺失不致命
        
        # ---- 8. 基本面评分（v3.0 主Agent直接计算，不再委托子Agent） ----
        result["fundamental_score"] = calculate_fundamental_score(result)
        
        # ---- 9. 股票类型提示 ----
        result["stock_type_hint"] = classify_stock_type(result)
        
        return result
        
    except Exception as e:
        import traceback
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        result["confidence"] = 0
        return result


def safe_float(val):
    """安全转浮点"""
    if val is None or val == '' or (isinstance(val, float) and math.isnan(val)):
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


def classify_stock_type(data):
    """根据财务数据给出股票类型倾向
    
    优先级：行业 > ROE+成长组合 > 市值
    行业判定为主，财务指标仅在行业模糊时做补充。
    """
    roe = data.get("profitability", {}).get("roeAvg")
    np_margin = data.get("profitability", {}).get("npMargin")
    yoy_ni = data.get("growth", {}).get("YOYNI")
    industry = data.get("industry", "")
    market_cap = data.get("market_cap", 0)

    # ---- 第一层：行业判定（决定性） ----
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

    # ---- 第二层：财务指标微调（不覆盖行业，只在无行业或模糊时生效） ----
    hints = []
    if industry_type:
        hints.append(industry_type)  # 行业类型作为基础

    # ROE + 成长组合判断（仅在行业非蓝筹时考虑调整为成长）
    if roe is not None and yoy_ni is not None:
        if roe > 0.20 and yoy_ni > 0.20:  # 高ROE + 高增长 → 成长特征
            if industry_type != "蓝筹":  # 银行保险即使高增长也不是成长股
                hints.append("成长")
        elif roe > 0.15 and np_margin is not None and np_margin > 0.15:  # 高ROE + 高利润率 → 蓝筹特征
            hints.append("蓝筹")

    # 市值
    if market_cap and market_cap > 1000e8:  # >1000亿
        hints.append("蓝筹")
    elif market_cap and market_cap < 100e8:
        hints.append("成长")

    if not hints:
        return "混合"

    from collections import Counter
    counter = Counter(hints)
    return counter.most_common(1)[0][0]


def classify_by_industry(industry_str: str) -> str:
    """轻量版：仅根据行业字符串分类（不需要财务数据）
    
    用于 validate_tech_score.py 等无财务数据上下文的场景。
    映射规则与 classify_stock_type 的行业层完全一致。
    """
    blue_chip_industries = ["银行", "保险", "证券", "白酒", "酒", "食品", "消费", "金融",
                            "公用事业", "交通运输", "白色家电"]
    growth_industries = ["科技", "医药", "新能源", "半导体", "软件", "人工智能",
                         "通信", "医疗器械", "安防"]
    cycle_industries = ["化工", "钢铁", "有色", "煤炭", "房地产", "地产",
                        "农牧", "建材", "石油"]

    for bi in blue_chip_industries:
        if bi in industry_str:
            return "蓝筹"
    for gi in growth_industries:
        if gi in industry_str:
            return "成长"
    for ci in cycle_industries:
        if ci in industry_str:
            return "周期"
    return "其他"


def calculate_fundamental_score(data, multi_year_data=None):
    """计算基本面四维度评分（各25分，总分100）+ 趋势修正
    支持传入 multi_year_data 做趋势评分和ROE标准差计算。
    """
    scores = {
        "profitability_score": 0,  # 25
        "growth_score": 0,          # 25
        "health_score": 0,          # 20
        "valuation_score": 0,       # 10
        "marginal_improvement": 0,  # 15
        "total_score": 0,           # 100
        "roe_std": None,
        "revenue_volatility": None,
        "trend_correction": 0,
        "trend_correction_detail": "",
        "growth_detail": "",
    }
    
    profitability = data.get("profitability", {})
    growth = data.get("growth", {})
    health = data.get("health", {})
    valuation = data.get("valuation", {})
    
    roe = profitability.get("roeAvg")
    np_margin = profitability.get("npMargin")
    gp_margin = profitability.get("gpMargin")
    eps = profitability.get("epsTTM")
    revenue = profitability.get("totalRevenue")
    
    yoy_ni = growth.get("YOYNI")
    yoy_equity = growth.get("YOYEquity")
    
    liability = health.get("liabilityToAsset")
    cf_to_or = health.get("CFOToOR")
    
    pe = valuation.get("peTTM")
    pb = valuation.get("pbMRQ")
    div_yield = data.get("dividend_yield")
    market_cap = data.get("market_cap")
    
    industry = data.get("industry", "")
    
    # ===== 1. 盈利能力 (25分) =====
    # v5.3.3(F-1): ROE 评分口径统一为最近年报——AKShare 源快照取最新报告期,
    # 中报/季报 ROE 未年化, 直接与年报分档门槛比较会系统性低估
    # (川投能源: 中报 5.06% vs 年报 11.0%, 同一只股票两源差一倍)。
    # 有年报数据时用年报 ROE 打分, 快照值仍保留展示。
    _annual_roe = None
    if multi_year_data:
        for _yr in sorted(multi_year_data.keys(), reverse=True):
            _r = (multi_year_data.get(_yr) or {}).get("roe")
            if _r is not None:
                _annual_roe = float(_r)
                break
    roe_basis = "annual" if _annual_roe is not None else "interim"
    _roe_scoring = _annual_roe if _annual_roe is not None else roe

    p_score = 0
    _p_details = []
    # ROE(年报口径): >20%=10, >15%=8, >10%=5, >5%=3, else 1
    if _roe_scoring is not None:
        if _roe_scoring > 0.20: p_score += 10
        elif _roe_scoring > 0.15: p_score += 8
        elif _roe_scoring > 0.10: p_score += 5
        elif _roe_scoring > 0.05: p_score += 3
        else: p_score += 1
        if roe_basis == "annual":
            _p_details.append(f"ROE按年报口径{_annual_roe*100:.1f}%评分")
        else:
            _p_details.append("无年报数据, ROE按快照口径评分(注意可能未年化)")
    else:
        p_score += 5  # 银行等特殊行业ROE偏低但非恶化的，给默认分

    # 净利率: >20%=8, >10%=5, >5%=3
    # v5.3.3(F-3): 净利率>100% = 投资收益型公司口径失真(投资收益计入利润
    # 不计入营收, 如川投能源346%/雅砻江水电权益法), 该项不再参与分档,
    # 改用年报ROE替代评估并在明细标注, 防止白拿满分。
    margin_note = ""
    if np_margin is not None and np_margin > 1.0:
        if _roe_scoring is not None:
            if _roe_scoring > 0.15: p_score += 6
            elif _roe_scoring > 0.10: p_score += 5
            elif _roe_scoring > 0.05: p_score += 4
            else: p_score += 2
        else:
            p_score += 4
        margin_note = f"净利率{np_margin*100:.0f}%>100%(投资收益型失真), 已改用ROE评估"
        _p_details.append(margin_note)
    elif np_margin is not None:
        if np_margin > 0.20: p_score += 8
        elif np_margin > 0.10: p_score += 5
        elif np_margin > 0.05: p_score += 3
        else: p_score += 1
    else:
        p_score += 4
    
    # 毛利率: >40%=7, >20%=4, >10%=2
    if gp_margin is not None:
        if gp_margin > 0.40: p_score += 7
        elif gp_margin > 0.20: p_score += 4
        elif gp_margin > 0.10: p_score += 2
        else: p_score += 0
    else:
        # 银行类无毛利率，用净利率替代部分
        if np_margin is not None and np_margin > 0.15:
            p_score += 5
        else:
            p_score += 3
    
    scores["profitability_score"] = min(25, p_score)
    scores["profitability_details"] = _p_details
    scores["roe_basis"] = roe_basis
    scores["annual_roe"] = _annual_roe  # v5.3.3(F-1): 年报ROE值, 报告层展示用
    scores["margin_note"] = margin_note
    
    # ===== 2. 成长性 (25分) =====
    # v5.0 重构：短期同比(15) + 持续性与趋势(10)
    g_score = 0
    
    # ── 2.1 短期同比 (15分) ──
    # 净利润同比: >30%=7, >15%=5, >0%=3, >-10%=1
    if yoy_ni is not None:
        if yoy_ni > 0.30: g_score += 7
        elif yoy_ni > 0.15: g_score += 5
        elif yoy_ni > 0: g_score += 3
        elif yoy_ni > -0.10: g_score += 1
        else: g_score += 0  # 负增长严重
    else:
        g_score += 3  # 未知给中间值
    
    # 净资产同比: >10%=4, >5%=2, >0%=1
    if yoy_equity is not None:
        if yoy_equity > 0.10: g_score += 4
        elif yoy_equity > 0.05: g_score += 2
        elif yoy_equity > 0: g_score += 1
        else: g_score += 0
    else:
        g_score += 2
    
    # EPS绝对值趋势（用EPS增速替代）
    eps_yoy = growth.get("YOYEPSBasic")
    if eps_yoy is not None:
        if eps_yoy > 0.15: g_score += 4
        elif eps_yoy > 0: g_score += 2
        elif eps_yoy > -0.10: g_score += 1
        else: g_score += 0
    else:
        g_score += 2
    
    # ── 2.2 持续性与趋势 (10分) ──
    growth_details = []
    
    # (a) 连续三个季度利润增长 (3分)
    # v5.3.2(D-3/F-b): 改为同比口径——原绝对值环比(Q2≥Q1≥Q3)在季节性行业
    # (Q4计提集中/春节停工)几乎必然失败, 系统性低估。用"近3期合计 vs 去年
    # 同期3期合计"单比值判定, 季节性天然中性; 报告期不连续/不足6期时退回
    # 环比并在明细标注局限。
    q_profits = data.get("quarterly_profits") or []
    _used_yoy = False
    if len(q_profits) >= 6:
        # v5.3.3(F-4): 同月日配对算法——原"i vs i+3"日期对齐校验在混合期型
        # (最新3期为 年报+Q1+H1 之类)时必然失败, 恒退化到环比 fallback
        # (季节性失真)。改为按"月日后缀"配对: 最新3期各找去年同月日值,
        # 全部配齐才做同比合计比较, 否则仍退环比并标注。
        def _rd(s):
            return str(s.get("report_date", ""))[:10]
        rows = []
        for q in q_profits:
            rd = _rd(q)
            np_v = q.get("net_profit")
            if rd and len(rd) == 10 and np_v is not None:
                try:
                    rows.append((rd, float(np_v)))
                except (TypeError, ValueError):
                    pass
        rows.sort(key=lambda t: t[0], reverse=True)
        latest3 = rows[:3]
        by_date = dict(rows)
        cur_vals, prev_vals = [], []
        for rd, np_v in latest3:
            try:
                prev_rd = f"{int(rd[:4]) - 1}{rd[4:]}"
            except ValueError:
                prev_rd = ""
            pv = by_date.get(prev_rd)
            if pv is None:
                cur_vals, prev_vals = [], []
                break
            cur_vals.append(np_v)
            prev_vals.append(pv)
        if cur_vals and prev_vals:
            cur3 = sum(cur_vals)
            prev3 = sum(prev_vals)
            if prev3 > 0:
                if cur3 > prev3:
                    g_score += 3
                    growth_details.append("近3期利润合计同比增长(+3)")
                elif cur3 > prev3 * 0.95:
                    g_score += 1
                    growth_details.append("近3期利润合计同比略降<5%(+1)")
                else:
                    growth_details.append("近3期利润合计同比下降(+0)")
                _used_yoy = True
    if not _used_yoy and len(q_profits) >= 3:
        last3 = q_profits[-3:]
        np_vals = [q["net_profit"] for q in last3 if q.get("net_profit") is not None]
        if len(np_vals) >= 3:
            inc_q1 = np_vals[1] >= np_vals[0]
            inc_q2 = np_vals[2] >= np_vals[1]
            if inc_q1 and inc_q2:
                g_score += 3
                growth_details.append("连续三季利润环比增长(+3,未含季节性校正)")
            elif inc_q1 or inc_q2:
                g_score += 1
                growth_details.append("近两季利润环比递增(+1,未含季节性校正)")
    
    # (b) 连续3年ROE上升 (4分)
    if multi_year_data and len(multi_year_data) >= 3:
        years = sorted(multi_year_data.keys())
        roe_series = []
        for yr in years:
            roe = multi_year_data[yr].get("roe")
            if roe is not None:
                roe_series.append(roe)
        if len(roe_series) >= 3:
            rises = sum(1 for i in range(1, len(roe_series)) if roe_series[i] > roe_series[i-1])
            if rises == len(roe_series) - 1:
                g_score += 4
                growth_details.append(f"连续{len(roe_series)}年ROE上升(+4)")
            elif rises >= len(roe_series) - 2:
                g_score += 2
                growth_details.append("ROE整体上行(近3年2升)(+2)")
            else:
                growth_details.append("ROE趋势平稳(+0)")
    
    # (c) 近2年营收连续增长 (3分)
    if multi_year_data and len(multi_year_data) >= 3:
        years = sorted(multi_year_data.keys())
        rev_series = []
        for yr in years:
            rev = multi_year_data[yr].get("revenue")
            if rev is not None:
                rev_series.append(rev)
        if len(rev_series) >= 3:
            rises = sum(1 for i in range(1, len(rev_series)) if rev_series[i] > rev_series[i-1])
            if rises == len(rev_series) - 1:
                g_score += 3
                growth_details.append(f"连续{len(rev_series)}年营收增长(+3)")
            elif rises >= len(rev_series) - 2:
                g_score += 1
                growth_details.append("营收整体上行(近3年2升)(+1)")
    
    # (d) 单季高增长但趋势平 → 存疑标注（不加分）
    if yoy_ni is not None and yoy_ni > 0.30 and multi_year_data and len(multi_year_data) >= 3:
        years = sorted(multi_year_data.keys())
        roe_series = [multi_year_data[yr].get("roe") for yr in years if multi_year_data[yr].get("roe") is not None]
        if len(roe_series) >= 3:
            roe_flat = all(roe_series[i] <= roe_series[i-1] * 1.05 for i in range(1, len(roe_series)))
            if roe_flat:
                growth_details.append("单季高增但ROE趋势平: 存疑(+0)")
    
    scores["growth_score"] = min(25, g_score)
    
    # ===== 3. 财务健康 (20分) =====
    h_score = 0
    
    # 资产负债率（行业敏感）
    is_bank = "银行" in industry or "保险" in industry
    if liability is not None:
        if is_bank:
            # 银行保险：负债率>90%是正常的，主要看不良率趋势
            h_score += 6  # 给基础分（v5.0 从8降到6，不再无脑给高分）
        elif liability < 0.30:
            h_score += 8
        elif liability < 0.50:
            h_score += 6
        elif liability < 0.70:
            h_score += 4
        elif liability < 0.85:
            h_score += 2
        else:
            h_score += 0  # >85%风险高
    else:
        h_score += 4
    
    # 经营现金流/营收: >0.2优秀, >0良好
    if cf_to_or is not None:
        if cf_to_or > 0.20: h_score += 8
        elif cf_to_or > 0.10: h_score += 6
        elif cf_to_or > 0: h_score += 3
        elif cf_to_or > -0.10: h_score += 1
        else: h_score += 0
    else:
        h_score += 4
    
    # 短期偿债能力（流动比率）
    # v5.3.2(D-3/F-a): 原用"权益乘数"——与资产负债率数学恒等
    # (assetToEquity = 1/(1-liability)), 两项合计占14/20权重重复计量同一
    # 风险。换成独立的流动比率(currentRatio, 数据源已有字段):
    # ≥2 充足 +6 | 1.5~2 良好 +4 | 1~1.5 一般 +2 | <1 紧张 +0
    current_ratio = health.get("currentRatio")
    if current_ratio is not None and not is_bank:
        if current_ratio >= 2.0:
            h_score += 6
        elif current_ratio >= 1.5:
            h_score += 4
        elif current_ratio >= 1.0:
            h_score += 2
        else:
            h_score += 0
    else:
        h_score += 3
    
    scores["health_score"] = min(20, h_score)
    
    # ===== 4. 估值水平 (10分) =====
    # v5.0：估值降权（25→10），因为行业差异大，绝对PE/PB参考意义有限
    v_score = 0
    
    # PE（行业相对：低于行业均值即视为相对便宜）
    # 简化处理：用绝对PE的分档，但权重降低
    if pe is not None and pe > 0 and not is_bank:
        if pe < 15: v_score += 4
        elif pe < 25: v_score += 3
        elif pe < 40: v_score += 2
        elif pe < 60: v_score += 1
        else: v_score += 0  # >60倍高估
    elif pe is not None and is_bank:
        # 银行PE通常极低
        if pe < 6: v_score += 4
        elif pe < 10: v_score += 3
        else: v_score += 1
    else:
        v_score += 2
    
    # PB（破净/接近破净可能有安全边际）
    if pb is not None and pb > 0:
        if pb < 1.0: v_score += 3  # 破净可能是低估但也可能是陷阱
        elif pb < 2.0: v_score += 2
        elif pb < 4.0: v_score += 1
        else: v_score += 0
    else:
        v_score += 1
    
    # 股息率
    if div_yield is not None:
        if div_yield > 0.04: v_score += 3  # >4%优秀
        elif div_yield > 0.03: v_score += 2
        elif div_yield > 0.02: v_score += 1
        else: v_score += 0
    else:
        v_score += 2
    
    scores["valuation_score"] = min(10, v_score)
    
    # ===== 5. 边际改善 (15分) =====
    # 慢牛中"改善中的公司"涨幅 > "绝对值最好的公司"
    # 三个维度：增长质量 + 利润率健康 + 现金流验证
    mi_score = 0
    
    # 5.1 增长驱动力 (5分): 利润增速 > 收入增速 → 效率提升
    yoy_rev = growth.get("YOYRevenue")
    yoy_ni_check = growth.get("YOYNI")
    if yoy_rev is not None and yoy_ni_check is not None:
        if yoy_ni_check > yoy_rev > 0:
            mi_score += 5  # 利润跑赢收入 → 边际改善
        elif yoy_ni_check > 0 and yoy_rev > 0:
            mi_score += 3  # 双增长
        elif yoy_ni_check > 0:
            mi_score += 1  # 利润增长但收入可能收缩
    
    # 5.2 利润率边际变化 (5分)
    # v5.3.2(D-5/F-c): 原"毛利率>25%且净利率>10%即+5"是静态质量门槛——
    # 与盈利能力维度重复计分, 且与"边际改善"名实不符(改善应看变化量)。
    # 改为 ROE 边际变化: 近一年上升+5 | 近一年平但前年升+3 | 下降+0;
    # 无多年度数据时退回原静态门槛。
    roe_series_mi = []
    if multi_year_data and len(multi_year_data) >= 2:
        _mi_years = sorted(multi_year_data.keys())
        roe_series_mi = [multi_year_data[yr].get("roe") for yr in _mi_years
                         if multi_year_data[yr].get("roe") is not None]
    if len(roe_series_mi) >= 2:
        up_last = roe_series_mi[-1] > roe_series_mi[-2]
        up_prev = len(roe_series_mi) >= 3 and roe_series_mi[-2] > roe_series_mi[-3]
        if up_last:
            mi_score += 5  # 盈利能力正在改善 → 真边际改善
        elif up_prev:
            mi_score += 3  # 此前在改善、近一年走平
        # 双降: +0
    elif gp_margin is not None and np_margin is not None:
        if gp_margin > 0.25 and np_margin > 0.10:
            mi_score += 5  # 高利润+高净利(静态门槛, 无多年度数据)
        elif gp_margin > 0.15:
            mi_score += 3  # 中等毛利率
        elif np_margin > 0.05:
            mi_score += 1
    
    # 5.3 现金流验证 (5分): 利润有现金流支撑才是真增长
    cf_to_np = health.get("CFOToNP")
    if cf_to_np is not None:
        if cf_to_np > 1.0:
            mi_score += 5  # 经营现金流 > 净利润
        elif cf_to_np > 0.5:
            mi_score += 3
        elif cf_to_np > 0:
            mi_score += 1
    
    scores["marginal_improvement"] = min(15, mi_score)

    # ===== 6. 趋势修正（基于多年度数据） =====
    trend_correction = 0
    trend_detail = ""
    if multi_year_data and len(multi_year_data) >= 3:
        trend_analysis = analyze_trend(multi_year_data)
        trend_correction = trend_analysis.get("overall_score", 0)
        trend_detail = trend_analysis.get("overall_trend", "")
        # ROE标准差
        roe_vals = []
        for yr in sorted(multi_year_data.keys()):
            roe = multi_year_data[yr].get("roe")
            if roe is not None:
                roe_vals.append(roe)
        if len(roe_vals) >= 3:
            import statistics
            scores["roe_std"] = round(statistics.stdev(roe_vals), 4)
        # 营收波动率（标准差/均值）
        rev_vals = []
        for yr in sorted(multi_year_data.keys()):
            rev = multi_year_data[yr].get("revenue")
            if rev is not None:
                rev_vals.append(rev)
        if len(rev_vals) >= 3:
            mean_rev = sum(rev_vals) / len(rev_vals)
            if mean_rev > 0:
                scores["revenue_volatility"] = round(statistics.stdev(rev_vals) / mean_rev, 4)

    scores["trend_correction"] = trend_correction
    scores["trend_correction_detail"] = trend_detail

    # ===== 总分 (上限100) =====
    # v5.0 权重：盈利25 + 成长25 + 健康20 + 估值10 + 边际改善15 = 95
    # 留出5分给趋势修正，避免边际改善/趋势修正被 min(100) 截断
    base_total = (
        scores["profitability_score"] + scores["growth_score"] +
        scores["health_score"] + scores["valuation_score"] +
        scores["marginal_improvement"])
    scores["total_score"] = min(100, max(0, base_total + trend_correction))
    # 记录成长持续性明细
    if growth_details:
        scores["growth_detail"] = "; ".join(growth_details)

    return scores


def trend_direction(values):
    """判定趋势方向：上升/下降/波动（v2：加入整体方向+纯单边判定）
    values: list of (year_or_label, value) tuples
    """
    if len(values) < 3:
        return "数据不足", 0
    changes = [values[i][1] - values[i-1][1] for i in range(1, len(values))]
    up_count = sum(1 for c in changes if c > 0)
    down_count = sum(1 for c in changes if c < 0)
    total_change = values[-1][1] - values[0][1]

    # 纯单边
    if down_count == len(changes):
        return "持续下降", -1
    if up_count == len(changes):
        return "持续上升", 1

    # 混合方向
    if down_count > up_count and changes[-1] < 0 and total_change < 0:
        return "震荡下降", -0.5
    elif up_count > down_count and changes[-1] > 0 and total_change > 0:
        return "震荡上升", 0.5
    elif down_count >= 2 and changes[-1] > 0:
        return "先降后升", 0
    elif up_count >= 2 and changes[-1] < 0:
        return "先升后降", 0

    # 幅度主导
    if total_change > abs(values[0][1]) * 0.05:
        return "近期回升", 0.5
    elif total_change < -abs(values[0][1]) * 0.05:
        return "近期下降", -0.5
    elif changes[-1] > 0:
        return "近期回升", 0.5
    elif changes[-1] < 0:
        return "近期下降", -0.5
    return "波动", 0


def analyze_trend(multi_year, profitability=None):
    """分析多年度趋势，返回趋势判定和各项评分（v5.4 B-17 收敛为委托）

    v5.4(B-17) 双实现收敛：本模块旧实现与 hithink_fundamental.analyze_trend
    长期漂移（签名/年份窗口/扣非质量项各走各路），现统一委托 hithink 版本
    —— v5.3.4 小数口径 + 动态年份窗口的 canonical 实现。
    旧本地实现已删除（git 历史可查）；保留 profitability 可选参数以兼容
    canonical 签名，原有单参数调用方不受影响。惰性导入防循环依赖
    （hithink 模块顶层反向 import 本模块）。
    """
    from hithink_fundamental import analyze_trend as _canonical_analyze_trend
    return _canonical_analyze_trend(multi_year or {}, profitability or {})


if __name__ == "__main__":
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["000001"]
    for sym in symbols:
        try:
            result = get_fundamentals(sym)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            import traceback
            print(json.dumps({
                "symbol": sym, "error": str(e), 
                "traceback": traceback.format_exc()
            }, ensure_ascii=False))
