#!/usr/bin/env python
"""
generate_report.py — A股三维分析报告生成器 v1.0

从 single_stock_analysis.py 的合并 JSON 读取数据，使用 Jinja2 模板
渲染完整 Markdown 分析报告。

用法:
    python generate_report.py --input result.json --output report.md
    python generate_report.py --code 600872 --name "中炬高新"  # 内部调 single_stock_analysis
    python generate_report.py --code 600872 --name "中炬高新" --save-db  # 写 SQLite
"""

import sys
import os
import json
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from jinja2 import Template

# 确保能找到 chanlun_core 模块
_CHANLUN_CORE = r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core"
if _CHANLUN_CORE not in sys.path:
    sys.path.insert(0, _CHANLUN_CORE)

# ============================================================
# 报告路径配置
# ============================================================
OUTPUT_DIR_LOCAL = os.path.expanduser("~/.hermes/profiles/commander/analysis_reports")
OUTPUT_DIR_WIN = "D:/常用文件/analysis_reports"

# ============================================================
# 辅助函数
# ============================================================

def safe(val, fmt=".2f", default="—"):
    """安全格式化数字，None/NaN 时显示默认值"""
    if val is None:
        return default
    try:
        v = float(val)
        if v != v:  # NaN check
            return default
        return f"{v:{fmt}}" if fmt else str(v)
    except (ValueError, TypeError):
        return default


def trend_arrow(val) -> str:
    """趋势箭头符号，支持字符串或 dict 格式"""
    if isinstance(val, dict):
        val = val.get("direction", "")
    mapping = {
        "up": "↑",
        "down": "↓",
        "stable": "→",
        "持续上升": "↑ 持续增长",
        "持续下降": "↓ 持续下降",
        "先升后降": "↑↓ 先升后降",
        "先降后升": "↓↑ 先降后升",
        "波动": "↕ 波动",
        "平稳": "→ 平稳",
    }
    return mapping.get(val, val or "→")


def grade_from_score(score: float) -> str:
    if score >= 80:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 50:
        return "D"
    return "E"


def decision_from_score(score: float, stock_type: str = "蓝筹",
                        growth_score: float = 0, news_score: float = 0) -> str:
    """根据评分+类型给出决策"""
    if score >= 80:
        return "强力推荐"
    elif score >= 70:
        return "推荐"
    elif score >= 60:
        if stock_type == "成长" and growth_score >= 15:
            return "关注（成长性≥15可上调）"
        if stock_type == "周期" and news_score >= 30:
            return "关注（消息面≥30可上调）"
        return "关注"
    elif score >= 50:
        return "观望"
    return "回避"


def position_from_score(score: float, fund_score: float, tech_score: float,
                        veto_triggered: bool) -> str:
    if veto_triggered:
        return "0% (否决)"
    if score < 50:
        return "0%"
    if score < 60:
        return "0%（观望）"
    if score < 70:
        if fund_score < 40:
            return "10%-20%（轻仓，基本面安全垫不足）"
        return "10%-20%（试探）"
    if score < 80:
        if fund_score >= 60 and tech_score >= 70:
            return "30%-50%（重仓）"
        if fund_score < 40:
            return "10%-20%（轻仓，基本面安全垫不足）"
        return "20%-30%"
    # >= 80
    return "30%-50%"


def compute_stock_type_probs(fund_data: dict) -> dict:
    """概率化分类（与 SKILL.md Step 4 规则一致）

    v5.3.4(C4) 刻度与键名适配：
    - roe_std / revenue_volatility 来自 quick 统一评分器，均为**小数**刻度
      （ROE标准差≈0.03、营收变异系数≈0.25）；旧百分点阈值(<3, >5, >30)
      在新口径下恒真/恒假，分类概率曾系统性偏移。
    - rev_growth 原读取原始模块中不存在的 revenueGrowth 键恒为 None——
      改读 B3 新增的 *_pct 展示键（百分点），缺失时 YOYRevenue(小数)×100 兜底。
    """
    industry = (fund_data.get("industry") or "").lower()
    industry_class = (fund_data.get("industry_classification") or "").lower()
    multi = fund_data.get("multi_year_data", {})
    roe_std = fund_data.get("fundamental_score", {}).get("roe_std")
    revenue_vol = fund_data.get("fundamental_score", {}).get("revenue_volatility")
    market_cap = fund_data.get("market_cap") or 0
    div_yield = fund_data.get("dividend_yield") or 0

    # 蓝筹加分
    blue = 10
    if any(k in industry + industry_class for k in ["消费", "金融", "公用事业", "银行", "保险"]):
        blue += 10
    if roe_std is not None and roe_std < 0.03:  # ROE波动<3个百分点
        blue += 15
    # 营收增速（百分点刻度）
    _grow_raw = fund_data.get("growth", {}) or {}
    rev_growth = _grow_raw.get("revenue_yoy_pct")
    if rev_growth is None and _grow_raw.get("YOYRevenue") is not None:
        rev_growth = round(_grow_raw["YOYRevenue"] * 100, 2)
    if rev_growth is not None and abs(rev_growth) < 10:
        blue += 5
    # v5.4.1(AUD-A-02): 契约单位为元——旧写 >1000/<100 丢 1e8 量纲:
    # "千亿蓝筹"分支恒真(所有股 blue+=10)、"百亿以下小盘成长"分支恒假。
    # 对齐兄弟实现 hithink_fundamental.py:924 的 1000e8/100e8 口径。
    if market_cap and market_cap > 1000e8:
        blue += 10
    if div_yield and div_yield > 3:
        blue += 15

    # 成长加分
    growth = 10
    if any(k in industry + industry_class for k in ["科技", "医药", "新能源", "半导体", "计算机"]):
        growth += 10
    if rev_growth is not None and rev_growth > 15:
        growth += 15
    if roe_std is not None and roe_std > 0.05:  # ROE波动>5个百分点
        growth += 10
    if market_cap and market_cap < 100e8:
        growth += 15
    if div_yield is not None and div_yield < 0.5:
        growth += 10

    # 周期加分
    cycle = 10
    if any(k in industry + industry_class for k in ["化工", "地产", "农牧", "钢铁", "有色",
                                                      "采掘", "建材", "有色金属"]):
        cycle += 10
    if revenue_vol is not None and revenue_vol > 0.30:  # 营收变异系数>30%
        cycle += 10
    # ROE交替检测（简化：roe_std > 5个百分点 且趋势异常）
    if roe_std is not None and roe_std > 0.05:
        # 检查 multi_year_data 是否有正负交替（v5.3.4-D1: 实际键降序取最近4年）
        roe_years = []
        for yr in sorted([y for y in multi.keys() if str(y).isdigit()], reverse=True)[:4]:
            if yr in multi and multi[yr].get("roe") is not None:
                roe_years.append(multi[yr]["roe"])
        if len(roe_years) >= 3:
            # 检测是否有上升和下降交替
            # v5.3.4(B2): multi_year.roe 已统一小数口径，交替幅度阈值 2个百分点=0.02
            # （旧值 2 在小数口径下永不触发，周期加分静默失效）
            changes = [roe_years[i] - roe_years[i+1] for i in range(len(roe_years)-1)]
            if any(c > 0.02 for c in changes) and any(c < -0.02 for c in changes):
                cycle += 10

    # 兜底：任一类<10%按10%计算
    blue = max(blue, 10)
    growth = max(growth, 10)
    cycle = max(cycle, 10)

    total = blue + growth + cycle
    return {
        "蓝筹": round(blue / total * 100, 1),
        "成长": round(growth / total * 100, 1),
        "周期": round(cycle / total * 100, 1),
    }


def compute_veto_check(chanlun_data: dict, fund_data: dict, negative_data: dict,
                       news_data: dict = None, name: str = "", code: str = "") -> dict:
    """否决检查（v5.3.4-C2/C3: #3 立案/造假真实匹配；#4 skip≠无负面）

    v5.4.1(AUD-A-01): name/code 用于 #3 新闻明细行的条目级相关性过滤
    (entry_match.relevant_detail_lines 两链统一口径)。"""
    checks = {}
    veto_triggered = False
    veto_reason = ""
    downgrade = 0

    # 1. 日线顶背驰+一卖
    daily = chanlun_data.get("daily", {})
    macd = daily.get("macd_status", {})
    points = daily.get("buy_sell_points", [])
    has_top_divergence = False
    # v5.4(B-11): 一卖有效性前置校验——旧实现对全历史 buy_sell_points 无时间
    # 界 any(), 多年前的一卖至今永久参与否决(中国中免案例复发条件)。有效口径
    # 对齐 pool_scanner E-1 的卖出压制纪律: 仅近10自然日内的一卖才参与本检查。
    from datetime import timedelta as _td
    _SELL_VALID_DAYS = 10
    _sell_cutoff = (datetime.now() - _td(days=_SELL_VALID_DAYS)).strftime("%Y-%m-%d")
    _recent_first_sells = [
        p for p in points
        if p.get("type") == "sell" and p.get("level") == 1
        and str(p.get("date", ""))[:10] >= _sell_cutoff
    ]
    has_first_sell = bool(_recent_first_sells)
    # 简化顶背驰检测：MACD趋势向下 + 死叉
    if macd:
        if macd.get("macd_trend") == "down" and macd.get("dif_dea") == "dead_cross":
            has_top_divergence = True

    checks["顶背驰+一卖"] = {
        "triggered": has_top_divergence and has_first_sell,
        "detail": (f"顶背驰={'是' if has_top_divergence else '否'}, "
                   f"一卖(近{_SELL_VALID_DAYS}日)={'是' if has_first_sell else '否'}"
                   + (f" [{', '.join(str(p.get('date',''))[:10] for p in _recent_first_sells[:3])}]"
                      if _recent_first_sells else ""))
    }
    if has_top_divergence and has_first_sell:
        veto_triggered = True
        veto_reason = "日线顶背驰+一卖"

    # 2. ROE连降3年+负债率>70%
    multi = fund_data.get("multi_year_data", {})
    roe_years = []
    # v5.3.4(D1): 年份窗口动态化——取 multi_year 实际键中最近4个自然年
    _veto_years = sorted([y for y in multi.keys() if str(y).isdigit()])[-4:]
    for yr in _veto_years:
        if yr in multi and multi[yr].get("roe") is not None:
            roe_years.append(multi[yr]["roe"])
    roe_decline_3y = len(roe_years) >= 4 and all(
        roe_years[i] >= roe_years[i+1] for i in range(len(roe_years)-1)
    )
    health = fund_data.get("health", {})
    # v5.3.4(B2收尾): 契约键名为 liabilityToAsset 且为小数口径——旧代码读
    # 不存在的 liabilityRatio 恒得 0，"ROE连降+高负债"否决从未可能触发
    _liab = health.get("liabilityToAsset")
    liability = (_liab * 100) if isinstance(_liab, (int, float)) else 0
    high_liability = liability > 70

    # 银行/保险行业酌情
    industry = (fund_data.get("industry") or "").lower()
    is_bank_ins = any(k in industry for k in ["银行", "保险"])
    if roe_decline_3y and high_liability:
        d = 1 if is_bank_ins else 2
        downgrade = max(downgrade, d)

    checks["ROE连降+负债率高"] = {
        "triggered": roe_decline_3y and high_liability,
        "detail": f"ROE{'连降' if roe_decline_3y else '未连降'}, 负债率{liability:.1f}%{' > 70%' if high_liability else ''}",
        "downgrade": downgrade,
    }
    if roe_decline_3y and high_liability:
        if veto_reason:
            veto_reason += " + ROE连降+高负债"
        else:
            veto_reason = f"ROE连降3年+负债率>{liability:.0f}%"

    # 3. 立案调查/财务造假
    # v5.3.4(C2/审计): 曾硬编码 {"triggered": False, "detail": "未发现"}——
    # 否决链形同虚设。现对负面明细(l3/l2_details)与新闻明细行做关键词真实匹配。
    # v5.4.1(AUD-A-01·2026-08-27): 新闻明细行改走 entry_match.relevant_detail_lines
    # 两链统一条目级口径——旧实现对全量行(含[正面]标题与CCTV全市场行)裸匹配,
    # 探针实锤"[东财新闻][正面] 证监会召开座谈会"即整票回避。词表同步收紧:
    # "证监会"移除("被证监会立案调查"已命中"立案调查", 信息零损失)。
    ng = negative_data or {}
    _severe_kw = ["立案调查", "立案侦查", "财务造假", "信息披露违规",
                  "退市风险", "强制退市"]
    _sev_texts = []
    for _d in (ng.get("l3_details") or []) + (ng.get("l2_details") or []):
        if isinstance(_d, dict):
            _sev_texts.append(str(_d.get("title", "")))
            _sev_texts.extend(str(h) for h in (_d.get("neg_hits") or []))
    if news_data:
        # v5.4(B-10): 改读真实随行的 detail 多行文本(死键 relevant_lines 已废)
        # v5.4.1(AUD-A-01): 仅[负面]/[混合]+本股名称/代码或个股级源白名单的行参与
        try:
            from entry_match import relevant_detail_lines as _rdl
        except ImportError:  # 单文件部署兜底: 直连路径
            from chanlun_core.entry_match import relevant_detail_lines as _rdl
        _detail_txt = str(news_data.get("detail") or "")
        _sev_texts.extend(_rdl(_detail_txt, name=name, code=code))
    _fraud_hit = sorted({kw for kw in _severe_kw
                         if any(kw in t for t in _sev_texts)})
    checks["立案调查/财务造假"] = {
        "triggered": bool(_fraud_hit),
        "detail": (f"命中: {'、'.join(_fraud_hit)}" if _fraud_hit else "未发现"),
    }
    if _fraud_hit:
        veto_triggered = True
        veto_reason = (veto_reason + " + 立案/造假线索"
                       if veto_reason else "立案调查/财务造假线索")

    # 4. 消息面L3负面
    # v5.3.4(C3): skip≠无负面——检查未执行时必须显式说明并提示人工复核，
    # 不得渲染成"无负面信号"误导决策。
    has_l3 = ng.get("l3_count", 0) > 0
    has_l2 = ng.get("l2_count", 0) > 0
    _src = str(ng.get("source") or "")
    if _src.startswith("skip"):
        checks["消息面L3负面"] = {
            "triggered": False,
            "detail": f"⚠️负面检查未执行({ng.get('error') or '数据源不可用'})，需人工复核",
        }
    else:
        checks["消息面L3负面"] = {
            "triggered": has_l3,
            "detail": f"L3={ng.get('l3_count',0)}, L2={ng.get('l2_count',0)}" if (has_l3 or has_l2) else "已检查，无负面信号",
        }
    if has_l3:
        veto_triggered = True
        if veto_reason:
            veto_reason += " + L3负面"
        else:
            veto_reason = "消息面L3级负面"

    return {
        "checks": checks,
        "veto_triggered": veto_triggered,
        "veto_reason": veto_reason,
        "downgrade": downgrade,
    }


def compute_composite(tech_score: float, fund_score: float, news_score: float,
                      stock_type_probs: dict) -> dict:
    """计算综合评分"""
    # 默认权重
    w_tech, w_fund, w_news = 0.40, 0.30, 0.30

    # 锚定规则：技术分<30时调整
    if tech_score < 30:
        w_tech, w_fund = 0.30, 0.40
    # v5.4(B-19) 注：旧模板曾渲染"缠论置信度锚定 w_tech=0.45 技术面+5%"
    # 分支，但本函数从未实现该路径（恒不触发=死模板）。已同步移除模板分支；
    # 若未来要真实现，需先拍板置信度阈值与权重守恒方案(0.45/0.30/0.25)。

    composite = round(tech_score * w_tech + fund_score * w_fund + news_score * w_news, 1)

    # 确定主要类型
    main_type = max(stock_type_probs, key=stock_type_probs.get)
    grade = grade_from_score(composite)
    decision = decision_from_score(composite, main_type,
                                    growth_score=fund_score if main_type == "成长" else 0,
                                    news_score=news_score if main_type == "周期" else 0)

    return {
        "tech_score": tech_score,
        "fund_score": fund_score,
        "news_score": news_score,
        "w_tech": w_tech,
        "w_fund": w_fund,
        "w_news": w_news,
        "composite": composite,
        "grade": grade,
        "decision": decision,
        "main_type": main_type,
    }


def extract_tech_details(chanlun_data: dict, analysis_date: str = None) -> dict:
    """提取技术面详细数据"""
    daily = chanlun_data.get("daily", {})
    zhous = daily.get("zhongshus", [])
    bis = daily.get("last_5_bis", [])
    points = daily.get("buy_sell_points", [])
    macd = daily.get("macd_status", {})
    price = daily.get("current_price")

    # 最新中枢
    latest_zs = zhous[-1] if zhous else None
    prev_zs = zhous[-2] if len(zhous) >= 2 else None

    # 最近买点
    recent_buy = None
    for p in reversed(points):
        if p.get("type") == "buy":
            recent_buy = p
            break

    # 计算评分明细
    score = 50  # 基础分
    score_items = [("基础分", 50, "中性")]

    if bis:
        last_bi = bis[-1]
        if last_bi.get("direction") == "up":
            score += 15
            score_items.append(("最新笔向上", 15, "趋势向上"))
        elif last_bi.get("direction") == "down":
            # 检查是否破前低
            prev_low = None
            for b in reversed(bis[:-1]):
                if b.get("direction") == "down":
                    prev_low = min(b.get("start_price", 0), b.get("end_price", 0))
                    break
            if prev_low and last_bi.get("end_price", 0) < prev_low:
                score -= 3
                score_items.append(("破前低", -3, "下跌恶化"))
            else:
                score += 10
                score_items.append(("向下笔不破前低", 10, "回调良性"))

    if latest_zs and price:
        if price > latest_zs.get("zg", 0):
            score += 20
            score_items.append(("突破中枢上沿", 20, "强势突破"))
        elif price > latest_zs.get("zd", 0):
            # 中枢内：根据价格在中枢中的位置分档
            zs_mid = (latest_zs["zg"] + latest_zs["zd"]) / 2
            if price >= zs_mid:
                score += 15
                score_items.append(("中枢中轴上方", 15, "偏强盘整"))
            else:
                score += 5
                score_items.append(("中枢中轴下方", 5, "偏弱盘整"))
        else:
            score -= 8
            score_items.append(("跌破中枢下沿", -8, "三卖风险"))

    if macd:
        if macd.get("dif_dea") == "golden_cross":
            score += 10
            score_items.append(("MACD金叉", 10, "动能转正"))
        else:
            score -= 5
            score_items.append(("MACD死叉", -5, "动能偏空"))
        if macd.get("macd_trend") == "up":
            score += 10
            score_items.append(("MACD趋势向上", 10, "动量支持"))

    # 买点信号时效性检查
    if recent_buy:
        from datetime import datetime, timedelta
        buy_date_str = str(recent_buy.get("date", ""))[:10]
        ref_date_str = (analysis_date or "")[:10]
        days_ago = 999
        if buy_date_str and ref_date_str:
            try:
                bd = datetime.strptime(buy_date_str, "%Y-%m-%d")
                rd = datetime.strptime(ref_date_str, "%Y-%m-%d")
                days_ago = (rd - bd).days
            except ValueError:
                pass
        if days_ago <= 30:
            score += 25
            score_items.append((f"近1月买点(+25)", 25, f"{recent_buy.get('level','')}买@{buy_date_str}"))
        elif days_ago <= 90:
            score += 15
            score_items.append((f"近3月买点(+15)", 15, f"{recent_buy.get('level','')}买@{buy_date_str}"))
        elif days_ago <= 180:
            score += 8
            score_items.append((f"半年内买点(+8)", 8, f"{recent_buy.get('level','')}买@{buy_date_str}"))
        # >180天：不加分，仅备注
    else:
        score -= 5
        score_items.append(("无买点信号", -5, "无有效买入信号"))

    # 最近3笔全是向下→扣分
    down_count = sum(1 for b in bis[-3:] if b.get("direction") == "down")
    if down_count >= 3:
        score -= 5
        score_items.append(("连续3笔向下", -5, "持续下跌"))

    score = max(-30, min(100, score))
    confidence = 3  # 默认

    return {
        "current_price": price,
        "latest_zhongshu": latest_zs,
        "prev_zhongshu": prev_zs,
        "last_5_bis": bis[-5:] if len(bis) >= 5 else bis,
        "macd_status": macd,
        "buy_sell_points": points[-5:] if len(points) >= 5 else points,
        "recent_buy": recent_buy,
        "score": score,
        "score_items": score_items,
        "confidence": confidence,
    }


def extract_fund_details(fund_data: dict) -> dict:
    """提取基本面详细数据"""
    prof = fund_data.get("profitability", {})
    grow = fund_data.get("growth", {})
    heal = fund_data.get("health", {})
    valu = fund_data.get("valuation", {})
    fs = fund_data.get("fundamental_score", {})
    multi = fund_data.get("multi_year_data", {})
    trend = fund_data.get("trend_analysis", {})
    data_date = fund_data.get("data_date", "")

    # 判断季报标签
    quarter_label = "最新期"
    if data_date:
        if data_date.endswith("0331"):
            quarter_label = f"Q1 {data_date[:4]}"
        elif data_date.endswith("0630"):
            quarter_label = f"中报 {data_date[:4]}"
        elif data_date.endswith("0930"):
            quarter_label = f"Q3 {data_date[:4]}"
        elif data_date.endswith("1231"):
            quarter_label = data_date[:4]

    # 利润质量计算
    deducted = prof.get("deductedProfit")
    net_p = prof.get("netProfit")
    profit_quality = None
    if deducted is not None and net_p and float(net_p) != 0:
        profit_quality = abs(float(deducted) / float(net_p))

    # ── 归一化 multi_year_data 字段名（下划线→驼峰） ──
    multi_normalized = {}
    if multi:
        for yr, data in multi.items():
            if data and isinstance(data, dict):
                multi_normalized[yr] = {
                    "revenue": data.get("revenue"),
                    "netProfit": data.get("net_profit"),
                    "deductedProfit": data.get("deducted_profit"),
                    "roe": data.get("roe"),
                    "gpMargin": data.get("gp_margin"),
                    "npMargin": data.get("np_margin"),
                    "liabilityRatio": data.get("liability"),
                    "revenueGrowth": data.get("revenue_yoy"),
                    "profitGrowth": data.get("profit_yoy"),
                    "profitQuality": data.get("profit_quality"),
                }

    return {
        "profitability": prof,
        "growth": {
            "revenueGrowth": grow.get("revenue_yoy_pct") or grow.get("YOYRevenue"),
            "profitGrowth": grow.get("profit_yoy_pct") or grow.get("YOYNI"),
            "revenue_yoy_pct": grow.get("revenue_yoy_pct"),
            "profit_yoy_pct": grow.get("profit_yoy_pct"),
        },
        "health": {
            "liabilityRatio": heal.get("liabilityToAsset"),
            "currentRatio": heal.get("currentRatio"),
            "quickRatio": heal.get("quickRatio"),
            "cfoToRevenue": heal.get("CFOToOR"),
            "cfoToNetProfit": heal.get("CFOToNP"),
            "totalAssets": heal.get("totalAssets"),
        },
        "valuation": {
            "peTTM": valu.get("peTTM"),
            "pbMRQ": valu.get("pbMRQ"),
            "marketCap": valu.get("market_cap_billion"),
        },
        "fundamental_score": fs,
        "multi_year_data": multi_normalized or multi,
        "trend_analysis": trend,
        "data_date": data_date,
        "quarter_label": quarter_label,
        "profit_quality": profit_quality,
    }


def extract_news_details(news_data: dict, stock_name: str = "", stock_code: str = "") -> dict:
    """提取消息面详细数据，含按来源统计+个股相关性标记

    Args:
        news_data: news_scanner 返回的原始数据
        stock_name: 股票名称（用于过滤个股相关消息）
        stock_code: 股票代码（用于过滤个股相关消息）
    """
    detail = news_data.get("detail", "")
    source = news_data.get("source", "")
    score = news_data.get("score", 50)

    # 解析 detail：第一行是 "[X源] ..." 汇总，后面是 "[来源][正/负] 标题" 明细
    detail_lines = detail.split(chr(10)) if detail else []
    summary_line = detail_lines[0] if detail_lines else ""
    msg_lines = detail_lines[1:] if len(detail_lines) > 1 else []

    # ── 按来源统计 ──
    per_source = {}  # {label: {"total": N, "relevant": N}}
    relevant_lines = []
    for line in msg_lines:
        if not line.strip():
            continue
        # 解析来源标签：行首形如 "[东财新闻]"
        src_label = "未知"
        if line.startswith("["):
            src_end = line.find("]", 1)
            if src_end > 0:
                src_label = line[1:src_end]
        if src_label not in per_source:
            per_source[src_label] = {"total": 0, "relevant": 0}
        per_source[src_label]["total"] += 1

        # 判断是否个股相关：同花顺新闻/公告/Tavily 默认个股相关
        # 东财新闻/CCTV财经/涨停池/雪球热搜 需含股票名或代码才算个股相关
        auto_relevant = {"同花顺新闻", "同花顺公告", "Tavily"}
        if src_label in auto_relevant:
            per_source[src_label]["relevant"] += 1
            relevant_lines.append(line)
        else:
            # 检查行内是否含股票名称或代码
            is_relevant = False
            if stock_name and stock_name in line:
                is_relevant = True
            if stock_code and stock_code in line:
                is_relevant = True
            if is_relevant:
                per_source[src_label]["relevant"] += 1
                relevant_lines.append(line)
            # 不相关的行业/宏观消息不纳入明细表

    # ── 构建来源汇总表 ──
    # 统计各来源的个股相关说明
    source_relevance_hints = {
        "东财新闻": "按代码关键词搜索，大部分为行业/板块数据；仅含股票名/代码的条目标记为个股相关",
        "CCTV财经": "按代码关键词搜索，均为时政/宏观新闻，非个股消息",
        "涨停池": "全市场涨停统计，非个股消息",
        "雪球热搜": "全市场热度排行，非个股消息",
        "同花顺新闻": "同花顺AI搜索，个股相关",
        "同花顺公告": "公司公告，个股相关",
        "Tavily": "Tavily网络搜索，个股相关",
    }
    per_source_table = []
    for label, stats in sorted(per_source.items()):
        hint = source_relevance_hints.get(label, "")
        per_source_table.append({
            "label": label,
            "total": stats["total"],
            "relevant": stats["relevant"],
            "hint": hint,
        })

    # ── 汇总说明 ──
    # 统计非个股相关的来源
    noise_sources = [s["label"] for s in per_source_table if s["relevant"] < s["total"]]
    noise_note = ""
    if noise_sources:
        noise_parts = []
        for label in noise_sources:
            s = next(x for x in per_source_table if x["label"] == label)
            noise_parts.append(f"{label}{s['total']}条中仅{s['relevant']}条个股相关")
        noise_note = f"注：{'；'.join(noise_parts)}，其余为行业/宏观新闻，与个股无关。"

    return {
        "source": source,
        "score": score,
        "reason": summary_line,
        "detail": detail,
        "summary_line": summary_line,
        "msg_lines": msg_lines,
        "relevant_lines": relevant_lines,         # 个股相关的消息明细
        "per_source_table": per_source_table,      # 按来源统计 + 相关性
        "noise_note": noise_note,                   # 汇总说明
        "pos_count": 0,
        "neg_count": 0,
        "total_articles": len(msg_lines),
        "relevant_count": len(relevant_lines),
    }


# ============================================================
# Jinja2 模板
# ============================================================

REPORT_TEMPLATE = """# {{ name }}（{{ code }}）三维分析报告

**分析日期**: {{ analysis_date }} | **当前价**: {{ '%.2f'|format(tech.current_price) if tech.current_price else '--' }} | **市值**: {{ mc_label }}
**行业**: {{ industry_name or '未知' }} | **类型**: {{ industry_cls or '' }}

---

## 快速结论

```
综合 {{ composite.composite }} → {{ composite.decision }}
```

**核心矛盾**: {{ core_conflict }}

---

## 评分总览

| 维度 | 评分 | 权重 | 加权得分 |
|:----|:----:|:----:|:--------:|
{% for dim in score_rows %}
| {{ dim.name }} | **{{ dim.score }}** | {{ dim.weight }}% | {{ dim.weighted }} |
{% endfor %}
| **综合** | **{{ composite.composite }}** | 100% | — |

**权重说明**: 默认权重(技术40%/基本面30%/消息面30%){% if composite.w_tech != 0.4 %}，锚定调整：技术面{{ composite.w_tech*100|round|int }}%/基本面{{ composite.w_fund*100|round|int }}%（技术分<30防御性锚定）{% endif %}。缠论置信度={{ tech.confidence }}。

---

## 否决检查

| 否决条件 | 结果 | 说明 |
|:---------|:----:|:-----|
{% for check_name, check in veto.checks.items() %}
| {{ check_name }} | {{ '🔴 **触发**' if check.triggered else '✅ 不触发' }}{% if check.downgrade %} (降{{ check.downgrade }}档){% endif %} | {{ check.detail }} |
{% endfor %}

{% if veto.veto_triggered %}
**否决处理**: {{ veto.veto_reason }} → 直接回避。
{% elif veto.downgrade > 0 %}
**否决处理**: ROE趋势下降+负债率>70% → 降级{{ veto.downgrade }}档。
{% else %}
**否决处理**: 无触发，正常评分。
{% endif %}

---

## 技术面分析（评分: {{ tech.score }}）

### 缠论结构

| 项目 | 数据 |
|:----|:------|
{% if tech.latest_zhongshu %}
| 最新中枢 | {% if tech.latest_zhongshu %}[{{ tech.latest_zhongshu.start }}~{{ tech.latest_zhongshu.end }}] ZG={{ '%.2f'|format(tech.latest_zhongshu.zg) }}, ZD={{ '%.2f'|format(tech.latest_zhongshu.zd) }} ({{ tech.latest_zhongshu.bi_count }}笔){% else %}—（无完整中枢）{% endif %} |
| 当前价 vs 中枢 | {% if not tech.latest_zhongshu %}—{% elif tech.current_price and tech.current_price > tech.latest_zhongshu.zg %}**{{ '%.2f'|format(tech.current_price) }} > ZG {{ '%.2f'|format(tech.latest_zhongshu.zg) }} → 中枢上方，突破态势**{% elif tech.current_price and tech.current_price > tech.latest_zhongshu.zd %}**{{ '%.2f'|format(tech.current_price) }} > ZD {{ '%.2f'|format(tech.latest_zhongshu.zd) }} → 中枢内盘整**{% elif tech.current_price %}**{{ '%.2f'|format(tech.current_price) }} < ZD {{ '%.2f'|format(tech.latest_zhongshu.zd) }} → 跌破中枢下沿，三卖风险**{% else %}—{% endif %} |
{% if tech.prev_zhongshu %}
| 前一个中枢 | [{{ tech.prev_zhongshu.start }}~{{ tech.prev_zhongshu.end }}] ZG={{ '%.2f'|format(tech.prev_zhongshu.zg) }}, ZD={{ '%.2f'|format(tech.prev_zhongshu.zd) }} |
{% endif %}
{% endif %}

### 最近5笔

| # | 方向 | 区间 | 价格区间 |
|:-:|:----:|:----|:--------:|
{% for bi in tech.last_5_bis %}
| {{ loop.index }} | {{ '↑' if bi.direction == 'up' else '↓' }} | {{ bi.start_date[5:10] }}→{{ bi.end_date[5:10] }} | {{ '%.2f'|format(bi.start_price) }}→{{ '%.2f'|format(bi.end_price) }} |
{% endfor %}

### MACD状态

| 指标 | 数值 | 判断 |
|:----|:----:|:----:|
{% if tech.macd_status %}
| DIF | {{ '%.3f'|format(tech.macd_status.dif) if tech.macd_status.dif else '—' }} | — |
| DEA | {{ '%.3f'|format(tech.macd_status.dea) if tech.macd_status.dea else '—' }} | — |
| MACD柱 | {{ '%.3f'|format(tech.macd_status.macd) if tech.macd_status.macd else '—' }} | {{ '柱体正向' if tech.macd_status.macd and tech.macd_status.macd > 0 else '柱体负向' }}且趋势{{ '向上' if tech.macd_status.macd_trend == 'up' else '向下' }} |
| 金叉/死叉 | {{ '金叉' if tech.macd_status.dif_dea == 'golden_cross' else '死叉' }} | {{ '短期动能转正' if tech.macd_status.dif_dea == 'golden_cross' else '短期动能偏空' }} |
| MACD趋势 | {{ '向上' if tech.macd_status.macd_trend == 'up' else '向下' }} | {{ '动量支持' if tech.macd_status.macd_trend == 'up' else '动量在减弱' }} |
{% endif %}

### 评分明细

| 项目 | 分值 | 说明 |
|:----|:----:|:-----|
{% for item in tech.score_items %}
| {{ item[0] }} | {{ '%+d'|format(item[1]) if item[1] != 50 else '%d'|format(item[1]) }} | {{ item[2] }} |
{% endfor %}
| **总分** | **{{ tech.score }}** | 置信度={{ tech.confidence }} |

---

## 基本面分析（评分: {{ fund.fundamental_score.adjusted_total or fund.fundamental_score.total_score }}/100{{ ', 含趋势修正%+d'|format(fund.fundamental_score.trend_correction) if fund.fundamental_score.trend_correction else '' }}）

### 4年财务趋势 + 最新季报（{{ fund.quarter_label }}）

| 指标 | 2022 | 2023 | 2024 | 2025 | **{{ fund.quarter_label }}** | 趋势 |
|:----|:----|:----|:----|:----|:----------:|:----|
{% set my = fund.multi_year_data %}
{% macro fy(year, key, fmt='.2f') %}{{ (my[year][key]|float / 1e8)|round(2) if my.get(year) and my[year].get(key) is not none and my[year][key]|string != 'nan' else '—' }}{% endmacro %}
{% macro fv(val, fmt='.2f') %}{{ (val|float / 1e8)|round(2) if val is not none and val|string != 'nan' and val != 0 else '—' }}{% endmacro %}
{% macro fp(val, fmt='.1f') %}{{ ('%.' + fmt[1:])|format(val) if val is not none and val|string != 'nan' else '—' }}{% endmacro %}
{%- for row in fin_rows %}
| {{ row.indicator }} | {{ row.years[0] }} | {{ row.years[1] }} | {{ row.years[2] }} | {{ row.years[3] }} | **{{ row.latest }}** | {{ row.trend }} |
{%- endfor %}

{% if quarter_commentary %}
### {{ fund.quarter_label }} 季报点评

**{{ quarter_commentary.summary }}**

| 指标 | {{ fund.quarter_label }} | 同比变化 | 评价 |
|:----|:--------:|:--------:|:-----|
{% for row in quarter_commentary.rows %}
| {{ row.name }} | {{ row.value }} | **{{ row.change }}** | {{ row.rating }} |
{% endfor %}

**核心判断：** {{ quarter_commentary.judgment }}
{% endif %}

### 四维度评分

| 维度 | 评分(/25) | 说明 |
|:----|:---------:|:-----|
| 盈利能力 | **{{ fund.fundamental_score.profitability_score }}** | ROE{{ '%.2f%%'|format(fund.profitability.roeAvg * 100) if fund.profitability.roeAvg else '' }}，毛利率{{ '%.2f%%'|format(fund.profitability.gpMargin * 100) if fund.profitability.gpMargin else '' }} |
| 成长性 | **{{ fund.fundamental_score.growth_score }}** | 营收增长{{ '%.1f%%'|format(fund.growth.revenueGrowth) if fund.growth.revenueGrowth else '' }} |
| 财务健康 | **{{ fund.fundamental_score.health_score }}** | 负债率{{ '%.1f%%'|format(fund.health.liabilityRatio * 100) if fund.health.liabilityRatio else '' }} |
| 估值水平 | **{{ fund.fundamental_score.valuation_score }}** | PE~{{ '%.1f'|format(fund.valuation.peTTM) if fund.valuation.peTTM else '' }}{% if fund.fundamental_score.valuation_percentiles.pe_p is defined and fund.fundamental_score.valuation_percentiles.pe_p %}({{ fund.fundamental_score.valuation_percentiles.pe_p }}%分位){% endif %} PB~{{ '%.1f'|format(fund.valuation.pbMRQ) if fund.valuation.pbMRQ else '' }}{% if fund.fundamental_score.valuation_percentiles.pb_p is defined and fund.fundamental_score.valuation_percentiles.pb_p %}({{ fund.fundamental_score.valuation_percentiles.pb_p }}%分位){% endif %}{% if fund.fundamental_score.valuation_penalty_detail %} ⚠️{{ fund.fundamental_score.valuation_penalty_detail }}{% endif %} |
{% if fund.fundamental_score.trend_correction %}
| **趋势修正** | **{{ '%+d'|format(fund.fundamental_score.trend_correction) }}** | {{ fund.fundamental_score.trend_correction_detail or '' }} |
{% endif %}

{% if fund.trend_analysis.structural_breaks %}
> ⚠️ **财务结构性断点告警**（跨期口径变化）：
{% for brk in fund.trend_analysis.structural_breaks %}
> - **{{ brk.metric }}**：{{ brk.from_year }}→{{ brk.to_year }} 变化 {{ '%+.1f'|format(brk.change_pp) if brk.change_pp is defined else '%+.1f%%'|format(brk.change_pct) }}pp — {{ brk.alert }}
{% endfor %}
{% endif %}

---

## 消息面分析（评分: {{ news.score }}）

### 数据源汇总

| 数据源 | 条数 | 个股相关 | 说明 |
|:------|:----:|:--------:|:-----|
{% for src in news.per_source_table %}
| {{ src.label }} | {{ src.total }}条 | {{ src.relevant }}/{{ src.total }}条 | {{ src.hint }} |
{% endfor %}
| **合计** | **{{ news.total_articles }}条** | **{{ news.relevant_count }}条相关** | **仅个股相关条目参与评分** |

{% if news.noise_note %}
{{ news.noise_note }}
{% endif %}

### 消息明细

{% if news.relevant_lines %}
{% for msg in news.relevant_lines[:20] %}
- {{ msg }}
{% endfor %}
{% if news.relevant_lines|length > 20 %}
- ... 共 {{ news.relevant_lines|length }} 条个股相关消息，仅显示前20条
{% endif %}
{% else %}
无个股相关消息明细
{% endif %}

### 负面信号检查

{% set neg_skipped = (negative.source or '').startswith('skip') %}
{% if neg_skipped %}
> ⚠️ **负面检查未执行**（{{ negative.error or '数据源不可用' }}）——下表计数不代表"无负面"，请人工复核近期公告与新闻。

{% endif %}
| 级别 | 数量 | 内容 |
|:----|:----:|:-----|
| L3(致命) | {{ negative.l3_count }} | {% if negative.l3_details %}{{ negative.l3_details[0].title[:50] }}{% else %}—{% endif %} |
| L2(重大) | {{ negative.l2_count }} | {% if negative.l2_details %}{{ negative.l2_details[0].title[:50] }}{% else %}—{% endif %} |
| L1(普通) | {{ negative.l1_count }} | — |

---

## 概率化分类

| 类型 | 概率 | 依据 |
|:----|:----:|:-----|
{% for type_name, prob in stock_types.items() %}
| **{{ type_name }}** | **{{ prob }}%** | {{ type_basis.get(type_name, '') }} |
{% endfor %}

**判定**: {{ type_judgment }}

---

## 推理链

```
{{ reasoning_chain }}
```

---

## 可执行观察清单

| 条件 | 触发后操作 | 当前状态 |
|:----|:----------|:--------:|
{% for obs in observations %}
| {{ obs.condition }} | {{ obs.action }} | {{ obs.status }} |
{% endfor %}

---

## 仓位建议

| 项目 | 建议 |
|:----|:----:|
| **决策** | **{{ composite.decision }}** |
| 仓位 | {{ position_str }} |
{% if stop_loss_info %}
| 止损 | {{ stop_loss_info }} |
{% endif %}
{% if next_focus %}
| 下次关注 | {{ next_focus }} |
{% endif %}

---

## 数据来源

{% for source in data_sources %}
{{ source }}
{% endfor %}
"""


# ============================================================
# 报告生成函数
# ============================================================

def generate_report(analysis_result: dict, output_path: str = None) -> str:
    """从分析结果生成 Markdown 报告

    Args:
        analysis_result: single_stock_analysis.py 输出的完整 JSON
        output_path: 报告保存路径（None 则不保存）

    Returns:
        str: 生成的 Markdown 报告内容
    """
    code = analysis_result.get("symbol", "000000")
    name = analysis_result.get("name", code)
    analysis_date = analysis_result.get("analysis_date", datetime.now().strftime("%Y-%m-%d"))
    date_short = analysis_date[:10]

    modules = analysis_result.get("modules", {})

    # ── 提取各模块数据 ──
    chanlun_data = modules.get("chanlun", {})
    fund_data = modules.get("fundamental", {})
    news_data = modules.get("news", {})
    negative_data = modules.get("negative", {})
    html_data = modules.get("html", {})

    # ── 计算辅助数据 ──
    tech = extract_tech_details(chanlun_data, analysis_date=analysis_date)
    fund = extract_fund_details(fund_data)

    # 市值标签
    mc = fund_data.get("market_cap")
    mc_label = f"~{mc/1e8:.0f}亿" if mc and mc > 0 else "—"
    # 行业信息
    industry_name = fund_data.get("industry") or ""
    industry_cls = fund_data.get("industry_classification") or ""

    # 概率分类
    stock_types = compute_stock_type_probs(fund_data)
    main_type = max(stock_types, key=stock_types.get)
    type_judgment = f"偏向{main_type}"
    if stock_types.get("成长", 0) > 40 and stock_types.get("周期", 0) > 30:
        type_judgment = f"成长/周期混合型，偏向{main_type}"
    elif stock_types.get("蓝筹", 0) > 50:
        type_judgment = f"蓝筹特征明显"

    # 类型依据（简化）
    industry = (fund_data.get("industry") or "").lower()
    type_basis = {}
    if stock_types["蓝筹"] > 15:
        type_basis["蓝筹"] = f"行业+{industry if industry else '' }"
    if stock_types["成长"] > 15:
        type_basis["成长"] = f"营收增速+ROE波动"
    if stock_types["周期"] > 15:
        type_basis["周期"] = f"行业特征"

    # 否决检查
    # v5.4.1(AUD-A-01): 传 name/code 供 veto#3 新闻明细行条目级过滤
    veto = compute_veto_check(chanlun_data, fund_data, negative_data,
                              news_data=modules.get("news", {}),
                              name=name, code=code)

    # 评分表行（fund 是 dict，用 [] 不用 .）
    fund_total = (fund["fundamental_score"].get("adjusted_total") or
                  fund["fundamental_score"].get("total_score", 50))
    news_score_val = news_data.get("score", 50)
    composite = compute_composite(tech["score"], fund_total, news_score_val, stock_types)
    if veto["veto_triggered"]:
        composite["decision"] = "回避"

    score_rows = [
        {"name": "技术面", "score": tech["score"],
         "weight": round(composite["w_tech"] * 100),
         "weighted": f"{tech['score'] * composite['w_tech']:.1f}"},
        {"name": "基本面", "score": fund_total,
         "weight": round(composite["w_fund"] * 100),
         "weighted": f"{fund_total * composite['w_fund']:.1f}"},
        {"name": "消息面", "score": news_score_val,
         "weight": round(composite["w_news"] * 100),
         "weighted": f"{news_score_val * composite['w_news']:.1f}"},
    ]

    # 核心矛盾
    core_conflict = "待分析"
    if tech["score"] < 50 and fund_total < 40:
        core_conflict = "技术面空头 + 基本面恶化"
    elif tech["score"] >= 70 and fund_total >= 60:
        core_conflict = "技术面强势 + 基本面稳健"
    elif tech["score"] >= 70 and fund_total < 40:
        core_conflict = f"技术面({tech['score']})信号好但基本面({fund_total})极差，仅轻仓"
    elif tech["score"] < 50 and fund_total >= 60:
        core_conflict = f"基本面({fund_total})优秀但技术面({tech['score']})弱，等买点"
    elif news_score_val >= 70 and tech["score"] < 50 and fund_total < 40:
        core_conflict = f"消息面利好({news_score_val})无法抵消技术面+基本面同时恶化"
    else:
        core_conflict = f"技术{tech['score']}/基本面{fund_total}/消息面{news_score_val}"

    # 季报点评
    quarter_commentary = generate_quarter_commentary(fund, name)

    # 推理链
    w_tech_pct = composite["w_tech"] * 100
    w_fund_pct = composite["w_fund"] * 100
    w_news_pct = composite["w_news"] * 100
    reasoning_chain = _build_reasoning_chain(composite, tech, fund_total, fund,
                                              news_score_val, veto, name, code)

    # 观察清单
    observations = _build_observations(tech, fund, composite)

    # 仓位
    veto_bool = veto["veto_triggered"] or False
    position_str = position_from_score(composite["composite"], fund_total,
                                        tech["score"], veto_bool)

    # 数据来源列表
    data_sources = []
    if "error" not in chanlun_data.get("daily", {}):
        data_sources.append(f"- 技术面: `quick_chanlun.py {code}`（Baostock）")
    if fund_data.get("data_source") and "error" not in fund_data:
        data_sources.append(f"- 基本面: `hithink_fundamental.py {code}`（同花顺API v2.0）")
    if news_data.get("source"):
        data_sources.append(f"- 消息面: `news_detail_report.py --code {code} --name {name}`（同花顺API）")
    if negative_data.get("source"):
        data_sources.append(f"- 负面信号: `check_negative_news.py --stocks {code} --name {name} --json`（同花顺API，无key自动降级多源扫描）")
    if html_data.get("html_path"):
        data_sources.append(f"- HTML可视化: `{html_data['html_path']}`")

    # ── 预计算财务表格数据（避免 Jinja2 复杂嵌套） ──
    _my = fund.get("multi_year_data", {})
    def _gv(yr, key):
        """从 multi_year_data 取数值"""
        d = _my.get(yr, {})
        if isinstance(d, dict):
            return d.get(key)
        return None

    def _ny(yr, key):
        """取数值，None 返回 '—'"""
        v = _gv(yr, key)
        if v is not None and str(v) != 'nan':
            return v
        return None

    def _fs(v):
        """格式化数值为两位小数"""
        return f"{v:.2f}" if v is not None else "—"

    def _fp(v):
        """格式化百分比为一位小数（自动处理小数 vs 百分数不一致）"""
        if v is not None:
            try:
                v = float(v)
                if abs(v) <= 1 and v != 0:
                    v = v * 100
            except (ValueError, TypeError):
                pass
        return f"{v:.1f}%" if v is not None else "—"

    def _fe(v):
        """格式化亿"""
        if v is not None and isinstance(v, (int, float)) and v != 0:
            try:
                return f"{abs(v)/1e8:.2f}" if abs(v) > 1e6 else f"{v:.2f}"
            except:
                return "—"
        return "—"

    # 组装财务表格数据
    fin_rows = []
    # v5.3.4(D1): 年份列动态化——原硬编码 ["2022".."2025"] 每过一年就过期
    from date_utils import recent_year_window as _ryw
    yr_labels = [str(y) for y in _ryw(4)]

    # 营收
    fin_rows.append({
        "indicator": "营收(亿)",
        "years": [_fe(_ny(y, "revenue")) for y in yr_labels],
        "latest": _fe(fund["profitability"].get("totalRevenue")),
        "trend": trend_arrow(fund["trend_analysis"].get("revenue_trend", "")),
    })
    # 营收同比
    fin_rows.append({
        "indicator": "营收同比",
        "years": ["—"] + [_fp(_ny(y, "revenueGrowth")) for y in yr_labels[1:]],
        "latest": _fp(fund["growth"].get("revenueGrowth")),
        "trend": "—",
    })
    # 归母净利
    fin_rows.append({
        "indicator": "归母净利(亿)",
        "years": [_fe(_ny(y, "netProfit")) for y in yr_labels],
        "latest": _fe(fund["profitability"].get("netProfit")),
        "trend": trend_arrow(fund["trend_analysis"].get("profit_trend", "")),
    })
    # 净利同比
    fin_rows.append({
        "indicator": "净利同比",
        "years": ["—"] + [_fp(_ny(y, "profitGrowth")) for y in yr_labels[1:]],
        "latest": _fp(fund["growth"].get("profitGrowth")),
        "trend": "—",
    })
    # 扣非净利
    fin_rows.append({
        "indicator": "扣非净利(亿)",
        "years": [_fe(_ny(y, "deductedProfit")) for y in yr_labels],
        "latest": _fe(fund["profitability"].get("deductedProfit")),
        "trend": "—",
    })
    # ROE
    fin_rows.append({
        "indicator": "ROE(%)",
        "years": [_fp(_ny(y, "roe")) for y in yr_labels],
        "latest": _fp(fund["profitability"].get("roeAvg")),
        "trend": trend_arrow(fund["trend_analysis"].get("roe_trend", "")),
    })
    # 毛利率
    fin_rows.append({
        "indicator": "毛利率(%)",
        "years": [_fp(_ny(y, "gpMargin")) for y in yr_labels],
        "latest": _fp(fund["profitability"].get("gpMargin")),
        "trend": trend_arrow(fund["trend_analysis"].get("margin_trend", "")),
    })
    # 净利率
    fin_rows.append({
        "indicator": "净利率(%)",
        "years": [_fp(_ny(y, "npMargin")) for y in yr_labels],
        "latest": _fp(fund["profitability"].get("npMargin")),
        "trend": trend_arrow(fund["trend_analysis"].get("margin_trend", "")),
    })
    # 负债率
    fin_rows.append({
        "indicator": "负债率(%)",
        "years": [_fp(_ny(y, "liabilityRatio")) for y in yr_labels],
        "latest": _fp(fund["health"].get("liabilityRatio")),
        "trend": trend_arrow(fund["trend_analysis"].get("health_trend", "")),
    })
    # 利润质量
    pq_latest = fund.get("profit_quality")
    pq_latest_fmt = f"{pq_latest*100:.0f}%" if pq_latest else "—"
    fin_rows.append({
        "indicator": "利润质量",
        "years": [_fp(_ny(y, "profitQuality")) if _ny(y, "profitQuality") else "—" for y in yr_labels],
        "latest": pq_latest_fmt,
        "trend": "—",
    })

    # 预计算历史同比数据（避免 Jinja2 深层嵌套）
    # v5.3.4(B2): multi_year 同比已统一小数口径，复用 _fp 的自动检测格式化
    # （旧实现无检测，B2 前靠百分数源数据碰巧正确）
    def _fmt_gv(v):
        return _fp(v)
    try:
        # v5.3.4(D1): 年份动态化——gp_/pp_ 变量名沿用模板引用，语义为
        # "倒数第三/二/一年"（yr_labels 已按当前年份窗口生成）
        gp_2023 = _fmt_gv(_my.get(yr_labels[-3], {}).get("revenueGrowth"))
        gp_2024 = _fmt_gv(_my.get(yr_labels[-2], {}).get("revenueGrowth"))
        gp_2025 = _fmt_gv(_my.get(yr_labels[-1], {}).get("revenueGrowth"))
        pp_2023 = _fmt_gv(_my.get(yr_labels[-3], {}).get("profitGrowth"))
        pp_2024 = _fmt_gv(_my.get(yr_labels[-2], {}).get("profitGrowth"))
        pp_2025 = _fmt_gv(_my.get(yr_labels[-1], {}).get("profitGrowth"))
    except Exception:
        gp_2023 = gp_2024 = gp_2025 = pp_2023 = pp_2024 = pp_2025 = "—"

    # 渲染模板
    template = Template(REPORT_TEMPLATE)
    report = None
    try:
        report = template.render(
            code=code,
            name=name,
            analysis_date=date_short,
            analysis_date_full=analysis_date,
            composite=composite,
            score_rows=score_rows,
            tech=tech,
            fund=fund,
            fund_data=fund_data,
            news=extract_news_details(news_data, stock_name=name, stock_code=code),
            negative=negative_data,
            stock_types=stock_types,
            type_basis=type_basis,
            type_judgment=type_judgment,
            veto=veto,
            core_conflict=core_conflict,
            quarter_commentary=quarter_commentary,
            reasoning_chain=reasoning_chain,
            observations=observations,
            position_str=position_str,
            stop_loss_info="结构止损 + 硬止损-8%" if composite["composite"] >= 60 else "不适用",
            next_focus=_next_focus(tech, fund, composite),
            data_sources=data_sources,
            trend_arrow=trend_arrow,
            fin_rows=fin_rows,
            mc_label=mc_label,
            industry_name=industry_name,
            industry_cls=industry_cls,
            # 历史同比数据（预计算，避免 Jinja2 深层嵌套）
            gp_2023=gp_2023, gp_2024=gp_2024, gp_2025=gp_2025,
            pp_2023=pp_2023, pp_2024=pp_2024, pp_2025=pp_2025,
        )
    except Exception as _render_err:
        # v5.3.4(C4/审计): 模板渲染崩溃（None/缺键的内联 format、次新股数据
        # 缺失等）不再让整个报告失败——降级为核心数据摘要渲染，保证 md 必产出
        # 且错误信息对用户可见，而非静默无报告。
        import traceback as _tb
        _fs = fund_data.get("fundamental_score") or {}
        report = (
            f"# {name}（{code}）三维分析报告 [⚠️降级渲染]\n\n"
            f"> **完整模板渲染失败**：{_render_err}\n>\n"
            f"> 以下为核心数据摘要，明细请查看 JSON 输出。\n\n"
            f"## 核心摘要\n\n"
            f"- **分析日期**: {analysis_date}\n"
            f"- **综合评分**: {composite.get('composite', '—')}\n"
            f"- **技术面**: score={composite.get('tech', '—')}, "
            f"日线买点={len(((chanlun_data.get('daily') or {}).get('buy_sell_points')) or [])}个\n"
            f"- **基本面**: total_score={_fs.get('total_score', '—')}, "
            f"数据源={fund_data.get('data_source', '—')}\n"
            f"- **消息面**: score={(news_data or {}).get('score', '—')}\n"
            f"- **负面检查**: source={(negative_data or {}).get('source', '—')}, "
            f"L3={(negative_data or {}).get('l3_count', 0)}\n"
            f"- **否决**: {'触发-' + str(veto.get('veto_reason', '')) if veto.get('veto_triggered') else '未触发'}\n\n"
            f"## 渲染错误堆栈\n\n```\n{_tb.format_exc()[-1500:]}\n```\n"
        )

    # 保存
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)

    return report


def generate_quarter_commentary(fund: dict, name: str) -> dict:
    """生成季报点评"""
    def _qfp(v):
        """季报点评百分比格式化（自动处理小数 vs 百分数）"""
        if v is not None:
            try:
                v = float(v)
                if abs(v) <= 1 and v != 0:
                    v = v * 100
            except (ValueError, TypeError):
                pass
        return v

    gp = _qfp(fund["profitability"].get("gpMargin"))
    np = _qfp(fund["profitability"].get("npMargin"))
    roe = _qfp(fund["profitability"].get("roeAvg"))
    rev_growth = fund["growth"].get("revenueGrowth")
    profit_growth = fund["growth"].get("profitGrowth")
    # v5.4(B-16): cfo 取值链 falsy→is not None——旧写法 cfo=0.0(真实持平值)
    # 被 or 链吞掉落到不存在的 cfops 键变 None，标签层再怎么修也救不回
    _cfo_raw = fund["health"].get("cfoToRevenue")
    cfo = _cfo_raw if _cfo_raw is not None else fund["health"].get("cfops")
    liability = _qfp(fund["health"].get("liabilityRatio") or fund["profitability"].get("liabilityRatio"))

    if not fund["data_date"]:
        return None

    # v5.4(B-18): 年化系数与报告期称谓按数据期动态取——旧实现把任何报告期的
    # ROE 都按 Q1 口径 ×4"年化"（中报应×2、三季报×4÷3、年报不放大），且点评
    # 文案硬编码"Q1"。data_date 兼容 YYYYMMDD 与 YYYY-MM-DD 两种形态。
    _dd = str(fund.get("data_date") or "").replace("-", "")
    _mm = _dd[4:6] if len(_dd) >= 6 and _dd.isdigit() else ""
    _PERIODS = {"03": (4, "一季报"), "06": (2, "中报"),
                "09": (4 / 3, "三季报"), "12": (1, "年报")}
    _factor, _plabel = _PERIODS.get(_mm, (None, "报告期未知"))

    rows = []

    # 营收（v5.4 B-16: falsy→is not None, 0 是真实值）
    rev = fund["profitability"].get("totalRevenue")
    rev_label = f"{rev/1e8:.2f}亿" if rev is not None else "—"
    rev_rating = "🟢" if (rev_growth or 0) > 20 else "🟡" if (rev_growth or 0) > 0 else "🔴"
    rev_comment = "大幅增长，订单交付加速" if (rev_growth or 0) > 20 else f"{'正' if (rev_growth or 0) > 0 else '负'}增长"
    rows.append({"name": "营收", "value": rev_label, "change": f"{rev_growth:+.1f}%" if rev_growth is not None else "—", "rating": rev_rating})

    # 净利
    np_abs = fund["profitability"].get("netProfit")
    np_label = f"{np_abs/1e8:.2f}亿" if np_abs is not None else "—"
    np_rating = "🟢" if (profit_growth or 0) > 20 else "🟡" if (profit_growth or 0) > 0 else "🔴"
    np_comment = f"{'增长' if (profit_growth or 0) > 0 else '下降'}"
    rows.append({"name": "归母净利", "value": np_label, "change": f"{profit_growth:+.1f}%" if profit_growth is not None else "—", "rating": np_rating})

    # 扣非
    deducted = fund["profitability"].get("deductedProfit")
    d_label = f"{deducted/1e8:.2f}亿" if deducted else "—"
    d_rating = "🟢" if fund["profit_quality"] and fund["profit_quality"] > 0.9 else "🟡"
    rows.append({"name": "扣非净利", "value": d_label, "change": "—", "rating": d_rating})

    # 毛利率
    gp_label = f"{gp:.2f}%" if gp is not None else "—"
    gp_rating = "🟢" if gp is not None and gp > 20 else "🟡" if gp is not None and gp > 10 else "🔴"
    rows.append({"name": "毛利率", "value": gp_label, "change": f"{gp:.1f}%" if gp is not None else "—", "rating": gp_rating})

    # 净利率
    np_pct = f"{np:.2f}%" if np is not None else "—"
    np_rating2 = "🟢" if np is not None and np > 10 else "🟡" if np is not None and np > 5 else "🔴"
    rows.append({"name": "净利率", "value": np_pct, "change": f"{np:.1f}%" if np is not None else "—", "rating": np_rating2})

    # 经营现金流
    cfo_label = f"{cfo:.1f}%" if cfo is not None else "—"
    cfo_rating = "🟢" if cfo is not None and cfo > 0 else "🔴"
    rows.append({"name": "经营现金流/营收", "value": cfo_label, "change": "正" if cfo is not None and cfo > 0 else "负", "rating": cfo_rating})

    # ROE
    # v5.4(B-16): 吞零值修复——roe=0 是真实值(亏损股), falsy 判断会显示"—"
    roe_label = f"{roe:.2f}%" if roe is not None else "—"
    roe_rating = "🟢" if roe is not None and roe > 10 else "🟡" if roe is not None and roe > 5 else "🔴"
    if roe is not None and _factor:
        roe_change = f"{_plabel}值{roe:.2f}%" if _factor == 1 else f"年化≈{roe * _factor:.1f}%"
    elif roe is not None:
        roe_change = "报告期未知，未年化"
    else:
        roe_change = "—"
    rows.append({"name": "ROE", "value": roe_label, "change": roe_change, "rating": roe_rating})

    # 摘要
    if rev_growth and profit_growth and profit_growth < rev_growth:
        summary = f"营收{rev_label}({rev_growth:+.1f}%)但利润{np_label}({profit_growth:+.1f}%)，增收不增利格局"
        if profit_growth and profit_growth < 0:
            summary += "加剧"
        judgment = f"营收增长但利润未能跟上，利润率持续压缩，{_plabel}未见拐点"
    elif rev_growth and rev_growth > 0 and profit_growth and profit_growth > 0:
        summary = f"营收利润双增长：营收{rev_growth:+.1f}%，利润{profit_growth:+.1f}%"
        judgment = f"{_plabel}量利齐升，基本面改善趋势确认"
    else:
        summary = f"{_plabel}数据待深入分析"
        judgment = "需结合更多数据判断"

    return {
        "summary": summary,
        "rows": rows,
        "judgment": judgment,
    }


def _build_reasoning_chain(composite: dict, tech: dict, fund_total: float,
                            fund: dict, news_score_val: float,
                            veto: dict, name: str, code: str) -> str:
    lines = []
    lines.append(f"1. 技术面({tech['score']}): {'多头' if tech['score'] >= 60 else '空头' if tech['score'] < 50 else '震荡'}")
    if tech["score"] < 50:
        lines.append(f"   → {'跌破中枢下沿' if tech.get('latest_zhongshu') and tech['current_price'] and tech['current_price'] < tech['latest_zhongshu']['zd'] else '趋势偏弱'}")
    elif tech["score"] >= 70:
        lines.append(f"   → 技术面强，{'突破中枢' if tech.get('latest_zhongshu') and tech['current_price'] and tech['current_price'] > tech['latest_zhongshu']['zg'] else '结构良好'}")

    lines.append("")
    lines.append(f"2. 基本面({fund_total}): {'优秀' if fund_total >= 70 else '一般' if fund_total >= 50 else '极差' if fund_total < 40 else '偏弱'}")
    fs = fund["fundamental_score"]
    trend_corr = fs.get("trend_correction", 0)
    if trend_corr:
        lines.append(f"   → 趋势修正{trend_corr:+.0f}分")

    lines.append("")
    lines.append(f"3. 消息面({news_score_val}): {'利好' if news_score_val >= 70 else '中性' if news_score_val >= 40 else '利空'}")

    if veto["veto_triggered"]:
        lines.append("")
        lines.append(f"4. 否决: {veto['veto_reason']} → 直接回避")
    elif veto["downgrade"] > 0:
        lines.append("")
        lines.append(f"4. 否决: ROE趋势下降+负债率高 → 降级{veto['downgrade']}档")

    lines.append("")
    w_t = composite["w_tech"]
    w_f = composite["w_fund"]
    w_n = composite["w_news"]
    c = composite["composite"]
    lines.append(f"5. 综合: {tech['score']}×{w_t*100:.0f}% + {fund_total}×{w_f*100:.0f}% + {news_score_val}×{w_n*100:.0f}% = {c} → {composite['decision']}")

    return "\n".join(lines)


def _build_observations(tech: dict, fund: dict, composite: dict) -> list:
    obs = []
    price = tech.get("current_price")
    zs = tech.get("latest_zhongshu")

    if zs and price:
        if price < zs.get("zd", 0):
            obs.append({"condition": f"股价站上{zs['zd']:.2f}(中枢ZD)", "action": "三卖风险解除", "status": "❌ 未触发"})
        if zs.get("zg"):
            obs.append({"condition": f"股价突破{zs['zg']:.2f}(中枢ZG)", "action": "技术面强势突破", "status": "❌ 待观察"})

    # ROE观察
    my = fund.get("multi_year_data", {})
    if composite["composite"] < 60:
        obs.append({"condition": "出现日线级别买点信号", "action": "技术面可上调至关注", "status": "❌ 未触发"})

    # 默认至少3条
    if len(obs) < 3:
        obs.append({"condition": "Q2营收增速维持", "action": "成长性确认", "status": "⏳ 待观察"})
        obs.append({"condition": "毛利率企稳回升", "action": "盈利能力改善", "status": "⏳ 待观察"})
    if len(obs) < 4:
        obs.append({"condition": "负债率下降", "action": "财务健康改善", "status": "⏳ 待观察"})

    return obs[:5]


def _next_focus(tech: dict, fund: dict, composite: dict) -> str:
    if composite["composite"] < 50:
        return "等待技术面买点+基本面改善信号"
    if composite["composite"] < 70:
        zs = tech.get("latest_zhongshu")
        if zs:
            return f"股价站稳中枢ZD({zs['zd']:.2f})+Q2数据验证"
    return "持股观察，按止损纪律执行"


# ============================================================
# 数据库写入
# ============================================================

def write_to_db(code: str, name: str, composite: dict, tech: dict,
                fund_total: float, news_score_val: float,
                veto: dict, stock_types: dict, output_path: str):
    """写入 SQLite（直接 import stock_db 调用，避免 subprocess）"""
    from datetime import datetime
    sys.path.insert(0, _CHANLUN_CORE)
    from stock_db import write_record, init_db

    # 确保 DB 已初始化
    import os
    db_path = os.path.expanduser("~/.hermes/data/stock_scores.db")
    if not os.path.exists(db_path):
        init_db()

    main_type = max(stock_types, key=stock_types.get)
    type_str = "/".join(f"{t}{p:.0f}%" for t, p in sorted(stock_types.items(), key=lambda x: -x[1]))

    payload = {
        "stock_code": code,
        "stock_name": name,
        "report_date": datetime.now().strftime("%Y-%m-%d"),
        "tech_score": tech["score"],
        "fund_score": fund_total,
        "news_score": news_score_val,
        "composite_score": composite["composite"],
        "decision": composite["decision"],
        "position_suggestion": position_from_score(composite["composite"], fund_total,
                                                    tech["score"], veto.get("veto_triggered", False)),
        "stock_type_probs": type_str,
        "veto_triggered": 1 if veto.get("veto_triggered") else 0,
        "core_conflict": f"技术{tech['score']}/基本面{fund_total}/消息面{news_score_val}",
        "observation_points": "待观察",
        "report_path": output_path,
    }
    try:
        write_record(payload)
    except Exception as e:
        print(f"  写入DB失败: {e}", file=sys.stderr)


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="A股三维分析报告生成器 v1.0")
    parser.add_argument("--code", help="股票代码（与--name配对，内部调single_stock_analysis）")
    parser.add_argument("--name", help="股票名称")
    parser.add_argument("--input", help="从JSON文件读取分析结果（替代实时分析）")
    parser.add_argument("--output", help="报告输出路径")
    parser.add_argument("--save-db", action="store_true", help="同时写入SQLite数据库")

    args = parser.parse_args()

    # ── 获取分析数据 ──
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            result = json.load(f)
        code = result.get("symbol", "000000")
        name = result.get("name", code)
    elif args.code:
        code = args.code
        name = args.name or code
        print(f"⏳ 正在分析 {name}({code})...", file=sys.stderr)
        # 调用 single_stock_analysis
        # v5.4.1(P3): /tmp 在 Windows 原生 Python 不存在(必败路径)——改 tempfile
        ssa_path = os.path.join(_CHANLUN_CORE, "single_stock_analysis.py")
        import tempfile as _tf
        _ssa_tmp = os.path.join(_tf.gettempdir(), f"_ssa_temp_{code}.json")
        r = subprocess.run(
            ["python", ssa_path, "--code", code, "--name", name, "--output", _ssa_tmp],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode != 0:
            print(f"❌ 分析失败: {r.stderr[:500]}", file=sys.stderr)
            sys.exit(1)
        with open(_ssa_tmp, "r", encoding="utf-8") as f:
            result = json.load(f)
    else:
        parser.print_help()
        sys.exit(1)

    # ── 确定输出路径 ──
    date_str = datetime.now().strftime("%Y-%m-%d")
    if args.output:
        output_path = args.output
    else:
        output_path = os.path.join(OUTPUT_DIR_LOCAL, f"{name}_{code}_{date_str}.md")

    # ── 生成报告 ──
    print(f"📝 正在生成报告...", file=sys.stderr)
    report = generate_report(result, output_path)

    # ── 同步到 Windows 目录 ──
    win_dir = os.path.join(OUTPUT_DIR_WIN, name)
    os.makedirs(win_dir, exist_ok=True)
    win_path = os.path.join(win_dir, f"{name}_{code}_{date_str}.md")
    with open(win_path, "w", encoding="utf-8") as f:
        f.write(report)

    # ── 写入 DB ──
    if args.save_db:
        modules = result.get("modules", {})
        chanlun_data = modules.get("chanlun", {})
        fund_data = modules.get("fundamental", {})
        news_data = modules.get("news", {})
        fund = extract_fund_details(fund_data)
        fund_total = fund["fundamental_score"].get("adjusted_total") or fund["fundamental_score"].get("total_score", 50)
        tech = extract_tech_details(chanlun_data)  # v5.3.4审计A4: 原引用未定义的 analysis_date 致 --save-db 必崩 NameError；参数本有默认值
        news_score_val = news_data.get("score", 50)
        veto = compute_veto_check(chanlun_data, fund_data,
                                  modules.get("negative", {}),
                                  news_data=modules.get("news", {}),
                                  name=name, code=code)
        stock_types = compute_stock_type_probs(fund_data)
        composite = compute_composite(tech["score"], fund_total, news_score_val, stock_types)

        write_to_db(code, name, composite, tech, fund_total, news_score_val,
                    veto, stock_types, output_path)

    # ── 输出确认 ──
    print(json.dumps({
        "status": "ok",
        "symbol": code,
        "name": name,
        "report": os.path.abspath(output_path),
        "win_report": win_path,
        "sections": ["技术面", "基本面", "消息面", "否决检查", "概率分类", "推理链", "观察清单"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
