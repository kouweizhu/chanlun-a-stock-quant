"""akshare_fundamental.py — AKShare 基本面数据获取模块 (v1.2)

替代 quick_fundamental.py 中 Baostock 的角色，提供更丰富的财务数据。
输出格式与 quick_fundamental.get_fundamentals() 完全兼容。

数据源:
  - stock_financial_abstract_ths (同花顺) — 核心财务指标，25个字段
  - Baostock fallback — 名称/行业/PE/PB
"""

import json
from date_utils import date_to_str, parse_date_to_datetime
import math
import os
import sys
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

# 复用 quick_fundamental 中的评分和分类函数
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quick_fundamental import (
    calculate_fundamental_score,
    classify_by_industry,
    classify_stock_type,
    safe_float,
)


def get_fundamentals_akshare(symbol: str) -> dict:
    """主函数：通过 AKShare 获取结构化财务数据

    输出格式与 quick_fundamental.get_fundamentals() 完全兼容。
    """
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
        "data_source": "akshare",
        "confidence": 5,
        "error": None,
    }

    errors = []

    # ============================================================
    # 1. 财务摘要 (同花顺) — 核心: 25个财务指标
    # ============================================================
    try:
        df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
        if df is not None and not df.empty:
            # 按报告期升序排列，取最新一期
            latest = df.iloc[-1]
            result["data_date"] = str(latest.get("报告期", ""))

            # ---- 盈利能力 ----
            # v5.3.3(G-1): 补扣非净利润+利润质量(扣非/归母), 对齐 stock-analysis 标准
            _np_snap = _parse_cn_number(latest.get("净利润"))
            _dp_snap = _parse_cn_number(latest.get("扣非净利润"))
            result["profitability"] = {
                "roeAvg": _parse_pct(latest.get("净资产收益率")),
                "npMargin": _parse_pct(latest.get("销售净利率")),
                "gpMargin": _parse_pct(latest.get("销售毛利率")),
                "netProfit": _np_snap,
                "deductedProfit": _dp_snap,
                "profitQuality": (round(_dp_snap / _np_snap, 4)
                                  if _dp_snap is not None and _np_snap and _np_snap > 0 else None),
                "totalRevenue": _parse_cn_number(latest.get("营业总收入")),
                "epsTTM": _parse_float(latest.get("基本每股收益")),
            }

            # ---- 成长能力 ----
            # v5.3.4(B3/审计P1-3): 补 *_pct 百分数展示键，对齐 L2/L3 契约
            # （YOY* 一律小数，*_pct 一律百分点，两套键并存各有消费者）
            _yoy_ni_dec = _parse_pct(latest.get("净利润同比增长率"))
            _yoy_rev_dec = _parse_pct(latest.get("营业总收入同比增长率"))
            result["growth"] = {
                "YOYNI": _yoy_ni_dec,
                "YOYEquity": None,
                "YOYAsset": None,
                "YOYEPSBasic": None,
                "YOYRevenue": _yoy_rev_dec,
                "revenue_yoy_pct": (round(_yoy_rev_dec * 100, 4)
                                    if _yoy_rev_dec is not None else None),
                "profit_yoy_pct": (round(_yoy_ni_dec * 100, 4)
                                   if _yoy_ni_dec is not None else None),
            }

            # 同比对比: 找去年同期报告期
            report_date = str(latest.get("报告期", ""))
            yoy_prev = _find_yoy_prev(df, report_date)
            if yoy_prev is not None:
                eps_now = _parse_float(latest.get("基本每股收益"))
                eps_prev = _parse_float(yoy_prev.get("基本每股收益"))
                if eps_now is not None and eps_prev is not None and eps_prev != 0:
                    result["growth"]["YOYEPSBasic"] = round(
                        (eps_now - eps_prev) / abs(eps_prev), 4
                    )

                bv_now = _parse_float(latest.get("每股净资产"))
                bv_prev = _parse_float(yoy_prev.get("每股净资产"))
                if bv_now is not None and bv_prev is not None and bv_prev != 0:
                    result["growth"]["YOYEquity"] = round(
                        (bv_now - bv_prev) / abs(bv_prev), 4
                    )

            # ---- 连续季度净利润（最近3期，判断增长持续性）----
            # 同花顺摘要按报告期升序排列，取最近3个季度的净利润绝对值
            try:
                q_rows = df.sort_values("报告期", ascending=True)
                q_profits = []
                for _, r in q_rows.tail(9).iterrows():  # v5.3.3(F-4): 取最近9期(~2.25年), 保证最新3期各有去年同期可配对
                    np_val = _parse_cn_number(r.get("净利润"))
                    q_date = str(r.get("报告期", ""))
                    if np_val is not None and len(q_date) >= 10:
                        q_profits.append({"report_date": q_date, "net_profit": np_val})
                if q_profits:
                    result["quarterly_profits"] = q_profits
            except Exception:
                pass

            # ---- 财务健康 ----
            # v5.3.3(G-1): 补周转天数(存货/应收), 季报点评表用
            liab = _parse_pct(latest.get("资产负债率"))
            result["health"] = {
                "liabilityToAsset": liab,
                "assetToEquity": _parse_float(latest.get("产权比率")),
                "currentRatio": _parse_float(latest.get("流动比率")),
                "quickRatio": _parse_float(latest.get("速动比率")),
                "CFOToOR": None,
                "CFOToNP": None,
                "cfPerShare": _parse_float(latest.get("每股经营现金流")),
                "inventory_days": _parse_float(latest.get("存货周转天数")),
                "receivable_days": _parse_float(latest.get("应收账款周转天数")),
            }

            # 如果产权比率缺失，从资产负债率推算
            if result["health"]["assetToEquity"] is None and liab is not None and liab < 1.0:
                result["health"]["assetToEquity"] = round(liab / (1.0 - liab), 2)

            # 用每股经营现金流间接推算 CFO/净利
            cf_ps = result["health"]["cfPerShare"]
            eps = result["profitability"].get("epsTTM")
            if cf_ps is not None and eps is not None and eps != 0:
                result["health"]["CFOToNP"] = round(cf_ps / eps, 4)

            # ---- 多年度数据提取（5年年报） ----
            # v5.3.3(G-1): 对齐 stock-analysis 基本面报告标准——补提同比增速/
            # 扣非净利润/周转天数, 支撑 score_report.md 的趋势表+季报点评表
            multi_year_data = {}
            try:
                yearly_rows = df[df['报告期'].astype(str).str.contains('-12-31', na=False)]
                # v5.4(B-09): 动态5年窗口——硬编码[2021..2025]在2027年起趋势数据冻结
                _this_year = datetime.now().year
                for yr in range(_this_year - 4, _this_year + 1):
                    mask = yearly_rows['报告期'].astype(str).str.startswith(str(yr))
                    matched = yearly_rows[mask]
                    if not matched.empty:
                        row = matched.iloc[0]
                        multi_year_data[str(yr)] = {
                            "roe": _parse_pct(row.get("净资产收益率")),
                            "gp_margin": _parse_pct(row.get("销售毛利率")),
                            "np_margin": _parse_pct(row.get("销售净利率")),
                            "net_profit": _parse_cn_number(row.get("净利润")),
                            "revenue": _parse_cn_number(row.get("营业总收入")),
                            "liability": _parse_pct(row.get("资产负债率")),
                            "eps": _parse_float(row.get("基本每股收益")),
                            "cf_ps": _parse_float(row.get("每股经营现金流")),
                            # v5.3.3(G-1) 新增: 同比/扣非/周转(stock-analysis标准字段)
                            "revenue_yoy": _parse_pct(row.get("营业总收入同比增长率")),
                            "profit_yoy": _parse_pct(row.get("净利润同比增长率")),
                            "deducted_profit": _parse_cn_number(row.get("扣非净利润")),
                            "deducted_yoy": _parse_pct(row.get("扣非净利润同比增长率")),
                            "inventory_days": _parse_float(row.get("存货周转天数")),
                            "receivable_days": _parse_float(row.get("应收账款周转天数")),
                        }
            except Exception:
                pass
            if multi_year_data:
                result["multi_year_data"] = multi_year_data

        else:
            errors.append("stock_financial_abstract_ths 返回空")
    except Exception as e:
        errors.append(f"stock_financial_abstract_ths: {str(e)[:100]}")

    # ============================================================
    # 2. 名称 + 行业 + PE/PB (Baostock — 最稳定的 fallback)
    # ============================================================
    try:
        _baostock_basic_and_valuation(symbol, result)
    except Exception as e:
        errors.append(f"baostock_basic: {str(e)[:80]}")

    # ============================================================
    # 2.5 股息率（v5.3.4-D1：L1 此前恒 None——蓝筹分类与估值评分的
    #     股息率维度静默失效）。乐咕 dv_ttm 为百分数(1.53=1.53%)，
    #     存小数口径与 L2 契约一致。属增强字段，失败不影响主链。
    # ============================================================
    if not result.get("dividend_yield"):
        # 不同 akshare 版本接口名不同（stock_a_indicator_lg / stock_a_gxl_lg），
        # 依次探测；东财系限频时段可能全失败 → 保持 None 不影响主链
        for _fn in ("stock_a_indicator_lg", "stock_a_gxl_lg"):
            try:
                import akshare as _ak_dv
                _getter = getattr(_ak_dv, _fn, None)
                if _getter is None:
                    continue
                _ind = _getter(symbol=symbol)
                if _ind is None or len(_ind) == 0:
                    continue
                _dv_col = ("dv_ttm" if "dv_ttm" in _ind.columns
                           else "dv_ratio" if "dv_ratio" in _ind.columns
                           else None)
                if _dv_col is None:
                    continue
                _sorted = _ind.sort_values("trade_date")  # 显式排序，消除 iloc[-1] 顺序假设
                _dv = safe_float(_sorted.iloc[-1].get(_dv_col))
                if _dv is not None and 0 < _dv < 30:  # 合理性护栏（>30%必为口径异常）
                    result["dividend_yield"] = round(_dv / 100, 6)
                    break
            except Exception:
                continue

    # ============================================================
    # 3. 股票类型 & 评分
    # ============================================================
    result["stock_type_hint"] = classify_stock_type(result)
    # v5.3.3(F-1): 内部调用补传 multi_year_data——原缺参导致此处算出的
    # fundamental_score 永远无趋势数据(ROE标准差/边际改善退静态门槛)
    result["fundamental_score"] = calculate_fundamental_score(
        result, multi_year_data=result.get("multi_year_data"))

    # 设置置信度
    if errors:
        result["confidence"] = max(1, 5 - len(errors))
        if not result["profitability"].get("roeAvg"):
            result["error"] = "; ".join(errors)
            result["confidence"] = 0

    return result


def _find_yoy_prev(df, report_date: str):
    """找去年同期报告期的行

    2026-03-31 → 找 2025-03-31
    2026-06-30 → 找 2025-06-30
    2025-12-31 → 找 2024-12-31
    """
    if not report_date or len(report_date) < 10:
        return None
    try:
        year = int(report_date[:4])
        suffix = report_date[4:]  # -03-31, -06-30, -09-30, -12-31
        yoy_date = f"{year - 1}{suffix}"
        # 在 DataFrame 中查找
        for _, row in df.iterrows():
            if str(row.get("报告期", "")) == yoy_date:
                return row
    except (ValueError, IndexError):
        pass
    return None


# ============================================================
# Baostock 集成 (名称/行业/PE/PB/市值)
# ============================================================

def _baostock_basic_and_valuation(symbol: str, result: dict):
    """一次性获取: 名称、行业、PE、PB、最新价、市值

    v5.3.3(F-2): 全程持有 BS_SESSION_LOCK——Baostock 是连接级会话,
    多线程并发 query 会串包(川投能源 PE=0 事故根因)。任何一段失败都
    显式标记 valuation_degraded, 不再静默放行。
    """
    import baostock as bs
    import baostock_utils

    _fail = []
    with baostock_utils.BS_SESSION_LOCK:
        bs_obj, lg = baostock_utils.ensure_login()
        if lg is None or lg.error_code != "0":
            result["valuation_degraded"] = True
            result["valuation_degraded_reason"] = "Baostock登录失败"
            return

        bs_code = baostock_utils.to_bs_code(symbol)

        # 名称
        try:
            rs = bs_obj.query_stock_basic(bs_code)
            basic = rs.get_data()
            if not basic.empty:
                if not result["name"]:
                    result["name"] = str(basic["code_name"].values[0])
        except Exception as e:
            _fail.append(f"name:{str(e)[:40]}")

        # 行业
        try:
            rs = bs_obj.query_stock_industry(bs_code)
            ind = rs.get_data()
            if not ind.empty:
                if not result["industry"]:
                    result["industry"] = str(ind["industry"].values[0])
        except Exception as e:
            _fail.append(f"industry:{str(e)[:40]}")

        # PE/PB/最新价
        today = datetime.now()
        start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
        end = today.strftime("%Y-%m-%d")

        try:
            rs = bs_obj.query_history_k_data_plus(
                bs_code,
                "date,close,peTTM,pbMRQ,turn",
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="2",
            )
            kdata = rs.get_data()
            if not kdata.empty:
                latest = kdata.iloc[-1]
                pe = safe_float(latest.get("peTTM"))
                pb = safe_float(latest.get("pbMRQ"))
                close = safe_float(latest.get("close"))
                if pe:
                    result["valuation"]["peTTM"] = pe
                if pb:
                    result["valuation"]["pbMRQ"] = pb
                if close:
                    result["valuation"]["latest_price"] = close
        except Exception as e:
            _fail.append(f"pepb:{str(e)[:40]}")

        # 总市值 = 最新价 × 总股本
        if result["valuation"].get("latest_price"):
            try:
                rs = bs_obj.query_stock_basic(bs_code)
                basic = rs.get_data()
                if not basic.empty:
                    total_share = safe_float(basic.get("totalShare"))
                    if total_share and result["valuation"]["latest_price"]:
                        mv = total_share * result["valuation"]["latest_price"]
                        result["market_cap"] = round(mv, 2)
                        result["valuation"]["market_cap_billion"] = round(mv / 1e8, 2)
            except Exception as e:
                _fail.append(f"mktcap:{str(e)[:40]}")

    # v5.3.3(F-2): 缺失可见——PE/PB 任一缺失即标记, 报告层显示"—"而非0
    if "peTTM" not in result.get("valuation", {}) or "pbMRQ" not in result.get("valuation", {}):
        result["valuation_degraded"] = True
        result["valuation_degraded_reason"] = f"PE/PB获取失败({';'.join(_fail) or '字段为空'})"

    # v5.3.4(D1): 市值兜底——baostock 对科创板(688)等 totalShare 为空。
    # 终审A1(2026-08-23): 原 push2 东财直连已被环境层封锁(Python/curl 双路
    # RemoteDisconnected，逐域实测确认)，改用腾讯实时行情 qt.gtimg.cn
    # （与 fetch_tencent_data 同族源，零鉴权）：v[44]=总市值(亿元)。
    # 失败静默（增强字段）。
    if not result.get("market_cap"):
        try:
            import urllib.request as _uq
            _tc = ("sh" if symbol.startswith(("6", "9", "5")) else "sz") + symbol
            _req = _uq.Request(
                f"http://qt.gtimg.cn/q={_tc}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                         "Referer": "https://gu.qq.com/"})
            with _uq.urlopen(_req, timeout=8) as _resp:
                _parts = _resp.read().decode("gbk", "replace").split("~")
            # 腾讯行情字段: [44]=总市值(亿) [45]=流通市值(亿)；防御性取索引
            _mv_yi = float(_parts[44]) if len(_parts) > 44 else 0.0
            if _mv_yi > 0:
                result["market_cap"] = round(_mv_yi * 1e8, 2)
                result["valuation"]["market_cap_billion"] = round(_mv_yi, 2)
        except Exception:
            pass


# ============================================================
# 工具函数
# ============================================================

def _parse_pct(val):
    """解析百分比字符串: '10.57%' → 0.1057

    也处理:
      - 数字类型直接判断
      - 'False' / '' / None → None
      - 负百分比: '-3.21%' → -0.0321
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if math.isnan(val):
            return None
        # v5.4(B-08): 数值输入统一按"百分数本体"契约处理(15 → 0.15)——
        # 旧"|v|≤1视为已是小数"启发式会把真·小于1%的百分数(如 0.8 表示
        # 0.8%)原样放行、被下游当作小数放大100倍。同花顺摘要的数值列
        # 单位就是百分数本体，小数形态(0.008)不出现在该数据源。
        return round(float(val) / 100, 4)
    s = str(val).strip()
    if not s or s.lower() == "false" or s == "--":
        return None
    if s.endswith("%"):
        try:
            return round(float(s[:-1]) / 100, 4)
        except ValueError:
            return None
    try:
        f = float(s)
        # v5.4(B-08): 与数值分支同契约——裸数字一律视为百分数本体(/100),
        # 不再做 ≤1 歧义启发式(见上方数值分支注释)
        return round(f / 100, 4)
    except ValueError:
        return None


def _parse_float(val):
    """解析数值: '21.49' → 21.49, 'False'/''/None → None"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if math.isnan(val):
            return None
        return round(float(val), 4)
    s = str(val).strip()
    if not s or s.lower() == "false" or s == "--":
        return None
    try:
        return round(float(s), 4)
    except ValueError:
        return None


def _parse_cn_number(val):
    """解析中文数字: '547.03亿' → 54703000000, '12.34万' → 123400"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if math.isnan(val):
            return None
        return float(val)
    s = str(val).strip()
    if not s or s.lower() == "false" or s == "--":
        return None

    multiplier = 1
    if s.endswith("亿"):
        multiplier = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        multiplier = 1e4
        s = s[:-1]

    try:
        return round(float(s) * multiplier, 2)
    except ValueError:
        return None


def scan_research_reports(symbol: str, lookback_days: int = 30) -> tuple:
    """扫描个股研报，提取机构共识评级

    Args:
        symbol: 股票代码 (6位)
        lookback_days: 回溯天数 (默认30天)

    Returns:
        (score_delta: int, details: str)
        score_delta: 基本面分调整值 (±5分以内)
        details: 详情字符串
    """
    from datetime import datetime, timedelta

    try:
        df = ak.stock_research_report_em(symbol=symbol)
        if df is None or df.empty:
            return 0, "[研报] 无数据"

        df["日期"] = df["日期"].astype(str)
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        recent = df[df["日期"] >= cutoff]

        if recent.empty:
            return 0, "[研报] 近期无覆盖"

        total = len(recent)
        ratings = recent["东财评级"].value_counts().to_dict()
        buy_count = ratings.get("买入", 0)
        add_count = ratings.get("增持", 0)
        hold_count = ratings.get("持有", 0)
        neutral_count = ratings.get("中性", 0)

        # 买入+增持占比
        positive = buy_count + add_count
        positive_ratio = positive / total if total > 0 else 0

        # 评分逻辑 (基本面分的微调，±5分以内)
        score_delta = 0
        details_parts = []

        # 1. 研报关注度 (近期研报数量)
        if total >= 10:
            score_delta += 2  # 高关注
            details_parts.append(f"高关注({total}篇)")
        elif total >= 5:
            score_delta += 1
            details_parts.append(f"关注({total}篇)")
        elif total >= 1:
            details_parts.append(f"覆盖({total}篇)")
        else:
            return 0, "[研报] 近期无覆盖"

        # 2. 机构共识 (买入+增持占比)
        if positive_ratio >= 0.9:
            score_delta += 3  # 强烈看好
            details_parts.append(f"强共识({positive_ratio:.0%})")
        elif positive_ratio >= 0.7:
            score_delta += 1
            details_parts.append(f"正面({positive_ratio:.0%})")
        elif positive_ratio < 0.5:
            score_delta -= 2  # 分歧较大
            details_parts.append(f"分歧({positive_ratio:.0%})")

        # 3. 盈利预测 (EPS 预期)
        eps_2026 = None
        # v5.4(B-09): 动态年份列——硬编码"2026-盈利预测"2027年起永远取不到值
        _forecast_col = f"{datetime.now().year}-盈利预测-收益"
        for col in [_forecast_col]:
            if col in recent.columns:
                val = recent[col].dropna()
                if not val.empty:
                    try:
                        eps_2026 = float(val.iloc[0])
                    except (ValueError, TypeError):
                        pass

        if eps_2026 is not None and eps_2026 > 0:
            details_parts.append(f"2026EPS预测={eps_2026:.2f}")

        # 构建详情
        detail = f"[研报] {'/'.join(details_parts)}"
        return score_delta, detail

    except Exception as e:
        return 0, f"[研报异常:{str(e)[:30]}]"


if __name__ == "__main__":
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["000001"]
    for sym in symbols:
        try:
            result = get_fundamentals_akshare(sym)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            import traceback

            print(
                json.dumps(
                    {
                        "symbol": sym,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
