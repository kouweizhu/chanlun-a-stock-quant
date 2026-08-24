#!/usr/bin/env python
"""pool_scanner.py — A500 股票池轻量技术面扫描器 v3.4

从 A500 池中筛选有买入机会的股票，识别三类标准买点 + 反转后买点。
仅跑日线 ChanLun 分析，不做 30 分钟递归。510 只约 3-5 分钟。

买点检测模式（按优先级）：
  1. 标准买卖点（一二三类买点）
  2. 趋势反转后买点（counter_trend 后的三买/类二买/回踩）

输出: scan_results.json — 所有有效分析结果（按 score 降序）
"""

import sys, os, json, time
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer
from file_utils import safe_write_json
from config_loader import A500_SCORE_THRESHOLD, A500_REV_SCORE_THRESHOLD
import baostock_utils

# ============================================================
# 配置
# ============================================================

POOL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "A500持仓.xls")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scanner_cache.json")
MIN_KLINES = 120          # 最少K线数
DATA_START = "2024-01-01"  # 数据起始（至少2年）
SCORE_THRESHOLD = A500_SCORE_THRESHOLD        # 进入 Phase 2 的最低分（≥3 标准筛选）
BUY_WINDOW_RECENT = 30     # 近期买点窗口（天）— score 5
REV_SCORE_THRESHOLD = A500_REV_SCORE_THRESHOLD    # 反转后买点的最低分（比标准买点更严格，要求 ≥4）
# 超过30天的买点 score 2，不进入Phase 2


def load_a500_pool() -> list:
    """加载 A500 股票池，返回 [(code, name), ...]"""
    df = pd.read_excel(POOL_PATH)
    pool = []
    for _, row in df.iterrows():
        raw_code = str(row["品种代码"]).strip()
        code = raw_code[:6]  # 截取 "300750.SZ" → "300750"
        name = str(row["品种简称"]).strip()
        pool.append((code, name))
    print(f"[pool_scanner] 加载 A500 池: {len(pool)} 只")
    return pool


def _detect_post_reversal_buy(analyzer, klines: list) -> tuple:
    """检测趋势反转后的买点机会

    当 counter_trend（反向中枢）确认下跌趋势已反转后，
    在新上升结构中寻找买点（三买/类二买/突破延续）。

    缠论依据：走势终完美——三买确认趋势反转后，新中枢内或
    向上离开新中枢时的买点属于新趋势的交易机会。

    Returns:
        (score: int, pattern: str, buy_info: dict | None)
        score=0 表示未检测到有效买点
    """
    zs_list = analyzer.zhongshus
    if len(zs_list) < 3:
        return 0, "", None

    buy_points = [p for p in analyzer.buy_sell_points if p.type == 'buy']
    if not buy_points:
        return 0, "", None

    # ── 1. 找到下跌趋势的最后一个中枢 ──
    # v5.3.1(M8): 统一"下跌趋势"判定口径——只认完全下移(前中枢ZD > 后中枢ZG),
    # 与 generate_analysis._identify_trends(第35课: 趋势=中枢依次同向且价格
    # 区间完全下移)一致。删除原"zg,zd 同时下降即算"的宽松分支: 同一股票
    # 两处口径曾得出相反结论, 且宽松分支会把重叠中枢误判为下跌趋势。
    downtrend_last_zs = None
    for i in range(len(zs_list) - 2, -1, -1):
        zs_a = zs_list[i]
        zs_b = zs_list[i + 1]
        if float(zs_a.zd) > float(zs_b.zg):
            downtrend_last_zs = zs_b  # 完全下移, 取末端中枢
            break

    if downtrend_last_zs is None:
        return 0, "", None

    # ── 2. 检查反向中枢（counter_trend） ──
    # 下跌趋势最后中枢之后，出现 ZG 更高的中枢说明趋势反转
    dt_end = str(downtrend_last_zs.end_date)
    counter_zs = None
    for zs in zs_list:
        if str(zs.start_date) >= dt_end and zs != downtrend_last_zs:
            if float(zs.zg) > float(downtrend_last_zs.zg):
                counter_zs = zs
                # 不 break — 持续更新，取最后一个（最新的）反向中枢作为参考
                # 修复前：break 只取第一个反向中枢（天山铝业→中枢[3] ZG=8.66）
                # 修复后：取最后一个（天山铝业→中枢[4]/[5]）更贴近当前价格

    if counter_zs is None:
        return 0, "", None

    ref_zg = float(counter_zs.zg)
    ref_zd = float(counter_zs.zd)

    # ── 3. 在新结构（counter_zs）中寻找买点 ──
    current_price = float(klines[-1].get('close', 0))
    if current_price <= 0:
        return 0, "", None

    try:
        current_date = datetime.strptime(str(klines[-1].get('date', ''))[:10], "%Y-%m-%d")
    except Exception:
        current_date = datetime.now()

    # 找 counter_zs 之后的三买
    for bp in sorted(buy_points, key=lambda p: str(p.date)):
        bp_dt = datetime.strptime(date_to_str(bp.date), "%Y-%m-%d")
        if bp_dt < parse_date_to_datetime(counter_zs.start_date):
            continue
        if bp.level != 3:
            continue
        days_ago = (current_date - bp_dt).days
        if days_ago < 0 or days_ago > 30:
            continue
        bp_price = float(bp.price) if bp.price else 0
        if bp_price <= 0:
            continue
        # 三买必须接近或高于新中枢 ZG
        if bp_price < ref_zg * 0.95:
            continue

        # 价格惩罚
        price_rise = (current_price - bp_price) / bp_price
        # v3.6: 反转后三买评分从5降为3
        # 理由：反转后三买不需要一买背驰确认，只有结构支撑，可靠性低于标准三买
        score = 3
        if price_rise > 0.20:
            score -= 2
        elif price_rise > 0.10:
            score -= 1
        if score >= 3:
            pattern = f"三买(趋势反转后,{days_ago}天前)"
            buy_info = {
                "buy_type": "反转后三买",
                "buy_date": date_to_str(bp.date),
                "buy_price": round(bp_price, 2),
                "zg": round(ref_zg, 2),
                "zd": round(ref_zd, 2),
            }
            return score, pattern, buy_info

    # ── 3b. 无近30天三买 → 检查当前价格位置 ──
    # 类二买：价格在 counter_zs 下半区且 MACD 改善
    # 仅下半区（zd ~ (zd+zg)/2）有意义——上半区更接近突破而非回踩
    # 2026-05-30: 类二买噪点太多，暂时禁用
    # mid_zone = (ref_zd + ref_zg) / 2
    # if ref_zd <= current_price <= mid_zone:
    #     macd_improving = False
    #     if len(analyzer.macd_data) >= 2:
    #         m0, m1 = analyzer.macd_data[-1], analyzer.macd_data[-2]
    #         if float(m0.dif) > float(m0.dea):
    #             macd_improving = True
    #         elif float(m1.macd) < float(m0.macd) < 0:
    #             macd_improving = True  # 绿柱缩短
    #
    #     if macd_improving:
    #         pattern = "类二买(趋势反转后,中枢下半区+MACD改善)"
    #         buy_info = {
    #             "buy_type": "反转后类二买",
    #             "buy_date": current_date.strftime("%Y-%m-%d"),
    #             "buy_price": round(current_price, 2),
    #             "zg": round(ref_zg, 2),
    #             "zd": round(ref_zd, 2),
    #         }
    #         return 3, pattern, buy_info

    # ── 3c. 突破后回踩确认（三买形成中，优先于突破延续）──
    # 硬约束：回踩价必须 > ZG（缠论三买要求回踩不进中枢）
    # 已由外层 current_price > ref_zg 保证，此处不再重复检查
    if current_price > ref_zg:
        latest_bi = analyzer.bis[-1]
        bi_end = float(latest_bi.end_price)
        if bi_end > ref_zg * 1.02:
            pullback_pct = (bi_end - current_price) / bi_end if bi_end > 0 else 0
            # v3.6: 反转后三买评分从5/4/3降为3/2/1
            # 理由：反转后三买不需要一买背驰确认，只有结构支撑，可靠性低于标准三买
            if pullback_pct >= 0.05:
                score = 3
                desc = f"深回踩{pullback_pct*100:.0f}%"
            elif pullback_pct >= 0.03:
                score = 2
                desc = f"浅回踩{pullback_pct*100:.0f}%"
            elif pullback_pct >= 0.02:
                score = 1
                desc = f"微回踩{pullback_pct*100:.0f}%"
            else:
                # 回踩 <2% → 突破延续
                pattern = "突破延续(趋势反转后,新高+回踩浅)"
                buy_info = {
                    "buy_type": "反转后突破",
                    "buy_date": current_date.strftime("%Y-%m-%d"),
                    "buy_price": round(current_price, 2),
                    "zg": round(ref_zg, 2),
                    "zd": round(ref_zd, 2),
                    "observational": True,  # v5.3.3(E-2)
                }
                return 2, pattern, buy_info

            pattern = f"三买形成中(趋势反转后,{desc})"
            buy_info = {
                "buy_type": "反转后三买",
                "buy_date": current_date.strftime("%Y-%m-%d"),
                "buy_price": round(current_price, 2),
                "zg": round(ref_zg, 2),
                "zd": round(ref_zd, 2),
                # v5.3.3(E-2): 纯几何观察信号(非真实缠论买点)——可入池观察,
                # Phase2 仓位强制封顶轻仓且排除出推荐列表
                "observational": True,
            }
            return score, pattern, buy_info

    return 0, "", None


def _detect_panbei_divergence_DISABLED(analyzer, current_price: float) -> tuple:
    """检测盘整底背驰/类一买

    缠论依据：同级别分解下，连续两个下跌笔的力度出现显著衰减
    （MACD柱面积缩小至前笔的40%以下），即使价格未创新低，
    也构成可靠的买入信号。

    典型案例 — 传音控股(688036) 2026-04：
      笔②下跌 61.68→50.99, MACD柱面积 9.38
      笔④下跌 57.42→51.50, MACD柱面积 1.62 (=17.3%)
      → 价格未新低(51.50>50.99)但力度极度衰竭 → 类一买

    核心条件：
      ① 价格条件：第二下跌笔的低点 >= 第一下跌笔低点的98%
         （不创新低，或轻微新低但不超过2%）
      ② 力度条件：MACD柱面积缩减至40%以下
      ③ 价量确认：当前价 > 第二下跌笔低点（已见底反弹）
      ④ 动量确认：MACD金叉或即将金叉（DIF向上接近DEA）

    Returns:
        (score: int, pattern: str, buy_info: dict | None)
        score=0 表示未检测到
    """
    bis = analyzer.bis
    macd_data = analyzer.macd_data

    if len(bis) < 4 or len(macd_data) < 20:
        return 0, "", None

    # 获取最近的两个向下笔
    # 从整个笔序列中筛选，避免只看最后5笔错漏
    down_bis = [b for b in bis if hasattr(b, 'direction') and b.direction == 'down']
    if len(down_bis) < 2:
        return 0, "", None

    d2 = down_bis[-1]  # 最近下跌笔
    d1 = down_bis[-2]  # 前一个下跌笔

    # 两个下跌笔之间必须有上涨笔隔开（构成底-顶-底结构）
    d1_idx = bis.index(d1)
    d2_idx = bis.index(d2)
    if d2_idx - d1_idx < 2:  # 中间至少隔1笔
        return 0, "", None

    # 条件①：价格条件 — 第二笔低点 >= 第一笔低点的98%
    d1_low = float(min(d1.start_price, d1.end_price))
    d2_low = float(min(d2.start_price, d2.end_price))
    if d2_low < d1_low * 0.98:  # 允许2%的向下穿透
        return 0, "", None

    # 条件②：MACD力度对比（v4.2 按笔长归一化）
    area1 = analyzer._calculate_macd_area_for_bi(d1, macd_data)
    area2 = analyzer._calculate_macd_area_for_bi(d2, macd_data)
    if area1 <= 0:
        return 0, "", None
    # ⚠️ v4.2 修复：面积按笔长归一化后再比较
    # 原实现直接比绝对面积：笔①长30根、笔②长5根时，即使笔②动能
    # 更强（单位时间力度更大），绝对面积也小 → 误判背驰。
    # 归一化 = 面积 / 笔内K线数（单位K线平均动能）
    def _bi_kline_count(bi):
        try:
            c = 0
            for md in macd_data:
                if md.date >= bi.start_date and md.date <= bi.end_date:
                    c += 1
            return max(c, 1)
        except Exception:
            return 1
    area1_norm = area1 / _bi_kline_count(d1)
    area2_norm = area2 / _bi_kline_count(d2)
    if area1_norm <= 0:
        return 0, "", None
    area_ratio = area2_norm / area1_norm
    if area_ratio > 0.40:  # MACD面积缩减到40%以下才算显著背驰
        return 0, "", None

    # 条件③：当前价在第二笔低点上方（已见底）
    if current_price <= d2_low * 0.99:
        return 0, "", None

    # 条件③.5（v4.2 新增）：时效约束 — 背驰点距今天数 ≤ 30 天
    # 原实现无时效检查：3个月前的底背驰会被当作新买点反复触发
    # （对比标准买点 30/60 天、反转买点 30 天窗口）
    from datetime import datetime as _dt
    try:
        d2_date = str(d2.end_date)[:10]
        d2_dt = _dt.strptime(d2_date, "%Y-%m-%d")
        age_days = (_dt.now() - d2_dt).days
        if age_days > 30:
            return 0, "", None  # 背驰点太老，不再作为新买点
    except Exception:
        return 0, "", None  # 日期解析失败保守拒绝

    # 条件④：MACD状态改善
    macd_improving = False
    if len(macd_data) >= 2:
        m0 = macd_data[-1]
        m1 = macd_data[-2]
        if float(m0.dif) > float(m0.dea):  # 已金叉
            macd_improving = True
        elif float(m0.dif) > float(m1.dif) and abs(float(m0.dif) - float(m0.dea)) < abs(float(m1.dif) - float(m1.dea)):
            # DIF上行且DIF-DEA差距缩小（接近金叉）
            macd_improving = True

    if not macd_improving:
        return 0, "", None

    # ────────────────────────────────────────────────────────────────
    # 条件⑤（v3.6修正）：趋势方向检查 — 类一买只在下跌/盘整中触发
    #
    # 缠论原理：一买的核心是"下跌趋势末端的背驰"，类一买（盘整底背驰）
    # 也应该在下跌趋势或盘整中出现，而不是上涨趋势中的回调。
    #
    # 如果整体是上涨趋势（中枢ZD持续上移），那么回调中的MACD力度衰竭
    # 只是上涨趋势的正常修正，不是一买/类一买信号。
    #
    # 判断标准：
    # - 最近5个中枢ZD上涨超过10% → 上涨趋势 → 不触发类一买
    # - 其他情况（盘整或下跌）→ 允许触发类一买
    # ────────────────────────────────────────────────────────────────
    zhongshus = analyzer.zhongshus
    if len(zhongshus) >= 3:
        lookback = min(5, len(zhongshus))
        trend_zs = zhongshus[-lookback:]
        trend_zd = [float(z.zd) for z in trend_zs]
        # 上涨趋势：ZD上涨超过10%
        if trend_zd[-1] > trend_zd[0] * 1.10:
            return 0, "", None  # 上涨趋势中的回调，不是类一买

    # 评分：根据背驰强度
    #
    # 盘整背驰（类一买）的可靠性本就低于趋势背驰（标准一买），
    # 因此基础评分从原来的5/4/3降为4/3/2。
    # 上涨趋势中的回调已被上面的条件⑤过滤，能到达这里的都是
    # 下跌趋势或盘整中的类一买。
    if area_ratio <= 0.20:
        score = 4  # 极强背驰（如传音17%）
    elif area_ratio <= 0.30:
        score = 3  # 强背驰
    else:
        score = 2  # 温和背驰
    pattern = f"类一买(盘整底背驰,MACD柱{area_ratio*100:.0f}%)"
    buy_info = {
        "buy_type": "类一买(盘整底背驰)",
        "buy_date": str(d2.end_date)[:10],
        "buy_price": round(d2_low, 2),
        "zg": round(d1_low if d1_low > d2_low else d2_low, 2),
        "zd": round(d2_low, 2),
        "area_ratio": round(area_ratio, 3),
        "area1": round(area1, 2),
        "area2": round(area2, 2),
    }

    return score, pattern, buy_info


def scan_stock(code: str, name: str, dm: DataManager) -> dict | None:
    """扫描单只股票，返回评分结果或 None（数据不足/分析失败）"""
    try:
        df = dm.get_klines(code, 'daily', DATA_START, datetime.now().strftime("%Y-%m-%d"))
        if df.empty or len(df) < MIN_KLINES:
            return None

        klines = dm.to_json_list(df)
        analyzer = ChanLunAnalyzer('daily', min_bi_klines=5)
        analyzer.analyze(klines)

        current_price = float(df.iloc[-1]['close'])
        current_date = str(df.iloc[-1]['date'])[:10]

        if len(analyzer.bis) < 3 or len(analyzer.zhongshus) == 0:
            return {
                "code": code, "name": name, "price": current_price,
                "date": current_date, "score": 0, "pattern": "结构不完整",
                "buy_type": "", "buy_date": "", "buy_price": 0,
                "zg": 0, "zd": 0, "total_bis": len(analyzer.bis),
                "total_zs": len(analyzer.zhongshus),
            }

        latest_zs = analyzer.zhongshus[-1]
        zg, zd = float(latest_zs.zg), float(latest_zs.zd)

        # ================================================================
        # 1. 检查标准买卖点
        # ================================================================
        buy_points = [p for p in analyzer.buy_sell_points if p.type == 'buy']
        sell_points = [p for p in analyzer.buy_sell_points if p.type == 'sell']

        try:
            last_dt = datetime.strptime(current_date, "%Y-%m-%d")
        except Exception:
            last_dt = datetime.now()

        best_buy = None
        best_score = 0
        best_buy_days = 999
        best_pattern = ""

        # ── v5.3.3(E-1): 买卖信号冲突仲裁 ──────────────────────────
        # 近N个自然日内存在一卖/二卖 → 该股当前不可买, 全部买入信号压制。
        # 缠论依据(29课): 一卖后的正确操作是卖出/观望; 同级别"站上ZG+回撤"
        # 的类三买形态大概率是二卖构造前的反弹中继(2026-08-22 雅克科技案例:
        # 08-18一卖@163.19, 3天-15%, 但08-21仍被几何信号推荐)。
        # 历史买点(一卖之前)一并失效——一卖是对该股顶部结构的全局确认。
        from config_loader import SELL_SIGNAL_SUPPRESS_DAYS
        _top_sells_recent = []
        for sp in sell_points:
            if getattr(sp, 'level', 0) not in (1, 2):
                continue
            try:
                sp_dt = datetime.strptime(date_to_str(sp.date), "%Y-%m-%d")
            except Exception:
                continue
            if 0 <= (last_dt - sp_dt).days <= SELL_SIGNAL_SUPPRESS_DAYS:
                _top_sells_recent.append(sp)
        if _top_sells_recent:
            _sp_last = max(_top_sells_recent, key=lambda p: str(p.date))
            _lv_name = '一卖' if getattr(_sp_last, 'level', 0) == 1 else '二卖'
            print(f"[E-1] {code} {name}: 近{SELL_SIGNAL_SUPPRESS_DAYS}日内出现{_lv_name}"
                  f"@{date_to_str(_sp_last.date)}({float(_sp_last.price):.2f}), 买入信号全部压制")
            return {
                "code": code, "name": name, "score": 0,
                "current_price": float(klines[-1].get('close', 0)) if klines else 0,
                "suppressed_by_sell": True,
                "sell_conflict_detail": f"{_lv_name}@{date_to_str(_sp_last.date)}",
            }

        for bp in buy_points:
            try:
                bp_dt = datetime.strptime(date_to_str(bp.date), "%Y-%m-%d")
            except Exception:
                continue
            days_ago = (last_dt - bp_dt).days
            if days_ago < 0:
                continue  # 未来买点忽略

            level_name = {1: "一买", 2: "二买", 3: "三买"}.get(bp.level, f"{bp.level}买")

            if days_ago <= BUY_WINDOW_RECENT:
                score = 5
                recency = "近期"
            elif bp.level == 2 and days_ago <= BUY_WINDOW_RECENT * 2:
                # 二买放宽时间窗口：二买是一买后的回调确认，天然滞后4-8周
                score = 4
                recency = "中期"
            else:
                score = 2  # >30天不进入Phase2
                recency = "远期"

            # v4.3: 下跌后首三买(V型反转)降分 — 与反转路径(v3.6, 反转后三买=3分)拉齐
            # 缠论依据：第20课三买定义不以一买/二买为前提，V型首三买合法但可靠性低
            # （无二买确认、前方套牢盘重）。标记由 generate_analysis._find_buy_sell_points 生成。
            _is_v_first_3b = bp.level == 3 and '下跌后首三买' in str(bp.reason)
            if _is_v_first_3b:
                score = min(score, 3)

            # 多个买点加分
            # v5.3.1(F6): V型首三买不享受多买点加成——用户拍板收紧。
            # 原实现 cap 后再加 +0.5, "首三买+任一远期买点"即可回到 3.5 入候选,
            # 抵消了 cap 压低低可靠性信号门槛的意图。
            if len(buy_points) >= 2 and not _is_v_first_3b:
                score = min(5, score + 0.5)

            # 价格距离惩罚：股价已涨离买点过远，机会已过
            if bp.price and bp.price > 0 and current_price > 0:
                price_rise = (current_price - float(bp.price)) / float(bp.price)
                if price_rise > 0.20:
                    score = max(0, score - 2)  # 已涨 20%+，机会远去
                elif price_rise > 0.10:
                    score = max(0, score - 1)  # 已涨 10%+，折扣

            # v4.0: 二买回调幅度惩罚
            # 缠论二买要求"回调不破前低"。回调越深越接近一买低点，
            # 可靠性越低。回调幅度 < 2% → 几乎跌穿一买，扣分。
            if bp.level == 2 and bp.price and bp.price > 0:
                fb_ = [p for p in buy_points if p.level == 1 and str(p.date) < str(bp.date)]
                if fb_ and fb_[-1].price and fb_[-1].price > 0:
                    fb_price = float(fb_[-1].price)
                    bp_price = float(bp.price)
                    if bp_price > fb_price:
                        margin = (bp_price - fb_price) / fb_price
                        if margin < 0.02:
                            score = max(0, score - 1.5)  # 极深回调
                        elif margin < 0.05:
                            score = max(0, score - 0.5)  # 偏深回调

            if score > best_score or (score == best_score and days_ago < (best_buy_days if best_buy else 999)):
                best_score = score
                best_buy = bp
                best_buy_days = days_ago
                level_name_display = level_name
                best_pattern = f"{level_name_display}({recency},{days_ago}天前)"

        # 计算 MACD 状态（所有路径都需要，包括反转后买点）
        macd_status = "unknown"
        if len(analyzer.macd_data) >= 2:
            m0 = analyzer.macd_data[-1]
            m1 = analyzer.macd_data[-2]
            if float(m0.dif) > float(m0.dea):
                macd_status = "golden"
            else:
                macd_status = "dead"
            if macd_status == "dead" and float(m0.macd) > float(m1.macd):
                macd_status = "dead_narrowing"

        # ================================================================
        # 2. 无标准买点 → 结构位置评分
        # ================================================================
        if best_buy is None:
            # 最新笔方向
            latest_bi = analyzer.bis[-1]
            bi_dir = latest_bi.direction
            latest_bi_end = float(latest_bi.end_price)

            # MACD 状态（已在上面统一计算）

            # 中枢位置
            price_vs_zs = current_price / zg if zg > 0 else 1.0

            # ── 优先：三买形成中（突破后回踩未成笔）──
            # 场景：向上笔突破 ZG → 价格回落但未形成向下笔 → 回踩中
            # 类似 海康威视 2026-04-22~24 的形态
            if bi_dir == "up" and latest_bi_end > zg * 1.01 and current_price > zg:
                pullback_pct = (latest_bi_end - current_price) / latest_bi_end
                if pullback_pct >= 0.05:
                    best_score = 4  # v4.0: 未成笔三买上限4分，已确认三买才给5分
                    best_pattern = f"三买形成中(深回踩{pullback_pct*100:.1f}%,未成笔)"
                elif pullback_pct >= 0.03:
                    best_score = 4
                    best_pattern = f"三买形成中(浅回踩{pullback_pct*100:.1f}%,未成笔)"
                elif pullback_pct >= 0.02:
                    best_score = 3
                    best_pattern = f"突破中枢上沿(微回踩{pullback_pct*100:.1f}%,观察)"
                else:
                    # v4.2 修复：刚突破中枢、回踩<2%的强势股不再得0分
                    # 原实现缺 else：pullback_pct < 0.02 时 best_score 保持 0，
                    # 导致"刚突破中枢"的最强信号反而被淘汰（评分倒挂：
                    # 突破即买回踩浅得0分 vs 回踩3-5%得3-4分）
                    best_score = 2
                    best_pattern = f"突破中枢上沿(刚突破{pullback_pct*100:.1f}%,等待回踩)"
            # ── 其次：中枢位置常规判断 ──
            elif 0.95 <= price_vs_zs <= 1.05 and macd_status in ("golden", "dead_narrowing"):
                if bi_dir == "down":
                    best_score = 2
                    best_pattern = "中枢下沿机会(下沿+MACD改善)"
                else:
                    best_score = 2
                    best_pattern = "中枢下沿机会(下沿+向上笔)"
            elif price_vs_zs > 1.0 and current_price < zg * 1.05:
                best_score = 2
                best_pattern = "中枢上沿附近(观察三买)"
            elif 0.90 <= price_vs_zs < 0.95 and macd_status in ("golden", "dead_narrowing"):
                best_score = 2
                best_pattern = "跌破中枢下沿(MACD改善中)"
            elif price_vs_zs > 1.05:
                best_score = 2
                best_pattern = "突破中枢上沿(等待回踩)"
            elif bi_dir == "up" and price_vs_zs >= 0.85:
                best_score = 1
                best_pattern = "向上笔(中枢下方)"
            else:
                best_score = 0
                best_pattern = "下跌趋势中"

        # ================================================================
        # 2.5. 趋势反转后买点检测（counter_trend 阻断的股票第二机会）
        #
        # 仅对有标准买点但被窗口/价格惩罚筛掉的股票触发。
        # 海康威视场景：buy3@01-30 被87天窗口筛掉，但趋势反转后
        # 新中枢 ZS#9 内有买点机会。
        # ================================================================
        _rev_buy_info = None
        if best_buy is not None and best_score < SCORE_THRESHOLD:
            rev_score, rev_pattern, rev_info = _detect_post_reversal_buy(analyzer, klines)
            if rev_score >= REV_SCORE_THRESHOLD and rev_score > best_score:
                best_score = rev_score
                best_pattern = rev_pattern
                best_buy = None  # 替换为标准买点
                _rev_buy_info = rev_info

        # ================================================================
        # 2.7 盘整底背驰/类一买检测 — 已禁用，不使用此类买点
        # ================================================================
        # 旧代码（已注释，不再使用类一买）：
        # if best_score < SCORE_THRESHOLD:  # <3时检测
        #     pb_score, pb_pattern, pb_info = _detect_panbei_divergence_DISABLED(analyzer, current_price)
        #     if pb_score >= SCORE_THRESHOLD and pb_score > best_score:
        #         best_score = pb_score
        #         best_pattern = pb_pattern
        #         best_buy = None
        #         _rev_buy_info = pb_info

        # ================================================================
        # 3. 组装结果
        # ================================================================
        buy_type_str = ""
        buy_date_str = ""
        buy_price_val = 0
        display_zg = zg
        display_zd = zd

        if best_buy:
            _level_names = {1: "一买", 2: "二买", 3: "三买"}
            buy_type_str = _level_names.get(best_buy.level, f"L{best_buy.level}买")
            buy_date_str = date_to_str(best_buy.date)
            buy_price_val = round(float(best_buy.price), 2)
        elif _rev_buy_info:
            buy_type_str = _rev_buy_info.get("buy_type", "")
            buy_date_str = _rev_buy_info.get("buy_date", "")
            buy_price_val = _rev_buy_info.get("buy_price", 0)
            display_zg = _rev_buy_info.get("zg", zg)
            display_zd = _rev_buy_info.get("zd", zd)

        # ── v5.3.3(E-2): 观察型几何信号统一标记 ──
        # 三类来源: ①反转路径3c/突破延续(显式 observational=True)
        #          ②标准路径几何评估的"形成中/等待回踩/观察"类 pattern
        # 判据: cache 显式标记优先, 否则按 pattern 关键词识别。
        _observational = bool(
            (_rev_buy_info or {}).get("observational", False)
            or any(kw in best_pattern for kw in ("形成中", "等待回踩", "观察"))
        )

        return {
            "code": code,
            "name": name,
            "price": round(current_price, 2),
            "date": current_date,
            "score": best_score,
            "pattern": best_pattern,
            "buy_type": buy_type_str,
            "buy_date": buy_date_str,
            "buy_price": buy_price_val,
            "zg": round(display_zg, 2),
            "zd": round(display_zd, 2),
            "total_bis": len(analyzer.bis),
            "total_zs": len(analyzer.zhongshus),
            "buy_count": len(buy_points),
            "sell_count": len(sell_points),
            "macd_status": macd_status,
            "observational": _observational,
        }

    except Exception as e:
        # v5.3.1(P1-4): 不再裸吞——打印失败详情。历史上 NameError 静默降级
        # 事故(confidence_score→假100分)正是这种"全部 skip 看起来正常"掩盖的。
        print(f"[pool_scanner] {code} {name} 分析失败: {type(e).__name__}: {str(e)[:80]}")
        return None


def main():
    t0 = time.time()
    pool = load_a500_pool()
    dm = DataManager()

    results = []
    skipped = 0
    consecutive_failures = 0
    for i, (code, name) in enumerate(pool):
        r = scan_stock(code, name, dm)
        if r is None:
            skipped += 1
            consecutive_failures += 1
            # v5.3.1(P1-4): 连续失败熔断——系统性 bug 作用于每只股票时
            # (重构后 NameError/字段变更), 及早中止而非静默跑完全池
            if consecutive_failures >= 20:
                print(f"[pool_scanner] ❌ 连续 {consecutive_failures} 只分析失败, "
                      f"疑似系统性故障(最后: {code}), 中止扫描以保护缓存")
                sys.exit(1)
        else:
            results.append(r)
            consecutive_failures = 0

        if (i + 1) % 50 == 0:
            print(f"[pool_scanner] 进度: {i+1}/{len(pool)} (有效:{len(results)} 跳过:{skipped})")

    # 按 score 降序排序
    results.sort(key=lambda r: (-r['score'], r['code']))

    # 保存 JSON
    output = {
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_scanned": len(pool),
        "valid_results": len(results),
        "skipped": skipped,
        "candidates": [r for r in results if r['score'] >= SCORE_THRESHOLD],
        "all_valid": results,
    }
    safe_write_json(CACHE_PATH, output)

    elapsed = time.time() - t0
    print(f"\n[pool_scanner] 完成! 耗时 {elapsed:.1f}s")
    print(f"  扫描: {len(pool)} 只 | 有效: {len(results)} | 跳过: {skipped}")
    print(f"  候选 (score≥{SCORE_THRESHOLD}): {len(output['candidates'])} 只")
    print(f"  缓存: {CACHE_PATH}")

    # 简短预览
    print(f"\n  {'代码':<8} {'名称':<8} {'评分':>4} {'模式':<30} {'买点':<14} {'现价':>8}")
    print(f"  {'-'*72}")
    for r in output['candidates'][:15]:
        print(f"  {r['code']:<8} {r['name']:<8} {r['score']:>4.1f} {r['pattern']:<30} "
              f"{r['buy_type']+'@'+r['buy_date'] if r['buy_date'] else '无':<14} ¥{r['price']:>7.2f}")

    return output


if __name__ == "__main__":
    main()
