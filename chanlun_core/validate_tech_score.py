"""
validate_tech_score.py — Phase 1: 技术面评分 vs 后续收益率验证

验证假设：缠论技术评分高的买入信号，后续收益率是否显著高于评分低的信号？

方法：
1. 对多只股票运行回测
2. 在每个买入信号处，记录：
   - 技术评分（从缠论状态推导的 0-100 分）
   - 置信度（1-5 分）
   - 信号类型（一买/二买/三买）
   - 后续 5/10/20/60 日收益率
3. 输出交叉分析：评分区间 vs 收益分布

用法：
  python validate_tech_score.py
  python validate_tech_score.py --stocks 600309,300059,601318
  python validate_tech_score.py --full     # 跑全部 18 只自选股
"""

import sys, json, os
from date_utils import date_to_str, parse_date_to_datetime
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict

from data_manager import DataManager
from generate_analysis import RecursiveTimingSystem, ChanLunAnalyzer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_engine import BacktestEngine, TradeRecord
from quick_fundamental import classify_by_industry

# ====== 默认测试股票池（与负面消息扫描共享同一清单） ======
# 行业→类型映射由 classify_by_industry() 统一提供
try:
    from check_negative_news import MONITOR_LIST
    DEFAULT_STOCKS = [(c, n, ind) for c, n, ind in MONITOR_LIST]
except ImportError:
    DEFAULT_STOCKS = [
        ("600309", "万华化学", "化工"), ("600346", "恒力石化", "化工"),
        ("600486", "扬农化工", "化工"), ("000830", "鲁西化工", "化工"),
        ("600298", "安琪酵母", "食品"), ("300783", "三只松鼠", "消费"),
        ("601888", "中国中免", "消费"), ("002714", "牧原股份", "农牧"),
        ("601615", "明阳智能", "新能源"), ("300772", "运达股份", "新能源"),
        ("601155", "新城控股", "地产"), ("000002", "万科A", "地产"),
        ("002271", "东方雨虹", "建材"), ("601601", "中国太保", "保险"),
        ("601318", "中国平安", "保险"), ("300059", "东方财富", "金融"),
        ("000001", "平安银行", "银行"), ("002415", "海康威视", "安防"),
    ]


def compute_technical_score(daily_analyzer, m30_analyzer, buy_point) -> dict:
    """
    从缠论分析器推导技术评分（0-100 分）+ 评分明细。
    
    评分框架（四维度叠加，各维度独立计分后求和）：
    1. 趋势结构 (40分): 当前处于什么结构位置
       - 中枢底附近 = 安全
       - 背离结构 = 反转概率高
    2. 信号质量 (30分): 买卖点本身的可靠性
       - 一买(趋势背驰) > 二买(回调确认) > 三买(中枢突破)
       - ⚠️ 注意：信号质量分与结构分是叠加关系。一买在中枢底部可得 30+35=65 分的基础分
    3. 多级别共振 (20分): 30分钟级别的配合
       - 30分钟也有买点确认
    4. 量价辅助 (10分): 当前成交量形态
       - 缩量止跌、放量突破
    
    四个维度评分直接求和（无上限截断，最大值约105），最终 tech_score = sum(四维度)
    """
    scores = {}
    details = []

    # ── buy_point=None 保护：Phase2 重建分析器后可能找不到匹配买点 ──
    # 为买点反转后三买/类一买等场景提供容错（pool_scanner.py 检测到的买点
    # 在重建分析器中不一定有对应的 BuySellPoint 对象）
    if buy_point is None:
        class _MockBP:
            price = 0.0
            level = 0
            date = None
            reason = ''
            confirmed = True
            multilevel_confirmation = {'confidence_score': 0, 'm30_confirmation': False}
        buy_point = _MockBP()
        # 尝试用最新K线收盘价作为 entry_price
        if daily_analyzer.klines and len(daily_analyzer.klines) > 0:
            buy_point.price = float(daily_analyzer.klines[-1].close)
        details.append("(无标准买点: 用当前价代理)")

    # 全局初始化（v5.0.1 修复：重构共振评分时删了 confidence_score 定义，
    # 导致函数末尾 return 引用未定义变量 → NameError → 所有股票技术分降级
    # 到 score*20，跌破惩罚等真实评分逻辑全部失效）
    _conf = getattr(buy_point, 'multilevel_confirmation', None)
    if isinstance(_conf, dict):
        confidence_score = _conf.get('confidence_score', 0)
    else:
        confidence_score = getattr(_conf, 'confidence_score', 0)

    # --- 1. 趋势结构评分 (40分) ---
    # 新逻辑（v5.0）：基于缠论结构完成度评分，不再以价格位置为核心
    # 一买：背驰+确认+中枢数
    # 二买：一买存在+不破前低+底分型
    # 三买：中枢突破+回踩不破ZG+回踩幅度
    structure_score = 20  # 基础分（中性位置）
    
    if not daily_analyzer.zhongshus:
        structure_score = 15  # 无中枢，结构不清晰
        details.append("无中枢: 结构不清晰(-5)")
    else:
        latest_zs = daily_analyzer.zhongshus[-1]
        entry_price = buy_point.price
        point_level = getattr(buy_point, 'level', 0)
        
        # 公共数据准备
        has_beichi = hasattr(buy_point, 'reason') and buy_point.reason and '背驰' in str(buy_point.reason)
        
        # MACD 状态检查
        macd_golden = False
        macd_improving = False
        if len(daily_analyzer.macd_data) >= 2:
            m0 = daily_analyzer.macd_data[-1]
            m1 = daily_analyzer.macd_data[-2]
            if float(m0.dif) > float(m0.dea):
                macd_golden = True
            elif float(m0.dif) > float(m1.dif) and abs(float(m0.dif) - float(m0.dea)) < abs(float(m1.dif) - float(m1.dea)):
                macd_improving = True
        
        # 检查是否有向上一笔（一买确认）
        has_up_after = False
        if hasattr(buy_point, 'date'):
            has_up_after = any(
                b.direction == 'up' and b.start_date > buy_point.date
                for b in daily_analyzer.bis
            )
        
        # 中枢数
        zhongshu_count = len(daily_analyzer.zhongshus)
        
        if point_level == 1:
            # 一买（趋势反转）：背驰+确认+中枢数
            # v5.3.2(D-2/T1): 子项缩权重标定——原加分合计40, 加上基础20=60
            # 被 min(40) 截断, "背驰+MACD"两项即封顶, 中枢数/确认笔白给,
            # 结构完美与缺两项同分(区分度失效)。缩至合计18(满分38),
            # 梯度20~38全部可分辨, cap 只兜极端。
            if has_beichi:
                structure_score += 8
                details.append("底背驰结构: 反转必要条件(+8)")
            if macd_golden or macd_improving:
                structure_score += 4
                details.append("MACD金叉或改善: 动量确认(+4)")
            if has_up_after:
                structure_score += 4
                details.append("已有向上一笔: 确认信号(+4)")
            if zhongshu_count >= 2:
                structure_score += 2
                details.append(f"中枢数≥2({zhongshu_count}个): 趋势完成(+2)")
            elif zhongshu_count == 1:
                details.append(f"仅1个中枢: 结构不够完整(+0)")
                
        elif point_level == 2:
            # 二买（回调确认）：一买存在+不破前低+底分型+回调幅度
            # 找前方一买
            # v5.3.1(M4): 取"最近的一买"而非列表序第一个——多轮下跌的股票
            # 存在多轮一买, 锚到最早的会使回调幅度/不破前低判断全部失真。
            # buy_sell_points 按日期升序, 遍历不 break 即得最近一个。
            first_buy = None
            for p in daily_analyzer.buy_sell_points:
                if p.type == 'buy' and p.level == 1 and hasattr(buy_point, 'date') and str(p.date) < str(buy_point.date):
                    first_buy = p
            
            if first_buy:
                # v5.3.2(D-2): 二买子项缩权 10+15+10/5+5=40 → 3+7+4/2+3=17
                structure_score += 3
                details.append(f"存在前方一买({str(first_buy.date)[:10]}): 前提条件(+3)")

                # 检查是否不破前低
                if entry_price >= first_buy.price:
                    structure_score += 7
                    details.append(f"回调不破一买低点({first_buy.price:.2f}): 二买核心(+7)")
                else:
                    details.append(f"跌破一买低点({first_buy.price:.2f}): 结构破坏(+0)")

                # 回调幅度（基于一买低点到ZG的距离）
                pullback_depth = (first_buy.price - entry_price) / first_buy.price if first_buy.price > 0 else 0
                if pullback_depth < 0.03:
                    structure_score += 4
                    details.append("回调幅度浅(<3%): 强势回调(+4)")
                elif pullback_depth < 0.08:
                    structure_score += 2
                    details.append("回调幅度适中(3-8%): 正常回调(+2)")
                else:
                    details.append("回调幅度深(>8%): 偏弱回调(+0)")
            else:
                details.append("无前方一买: 二买前提缺失(+0)")

            # 底分型确认（检查买点日期是否有底分型）
            if hasattr(buy_point, 'date') and hasattr(daily_analyzer, 'fenxings'):
                bp_date = str(buy_point.date)[:10]
                has_bottom = any(
                    str(fx.date)[:10] == bp_date and fx.type == 'bottom'
                    for fx in daily_analyzer.fenxings
                )
                if has_bottom:
                    structure_score += 3
                    details.append("底分型确认: 结构完成(+3)")
                    
        elif point_level == 3:
            # 三买（中枢突破）：突破+回踩不破ZG+回踩幅度
            # v5.3.1(M3): 锚定"被突破的参照中枢"——buy_point.ref_zg/ref_zd 由
            # 检测侧记录。三买后若已形成更高新中枢, zhongshus[-1] 不再是当初
            # 突破基准, 对照它会把强势三买误判为"未突破/回踩破ZG"
            # (2026-08-22 审计: 候选池19只三买15只错位)。无 ref 字段时回退。
            _zs_ref = latest_zs
            _rzg = getattr(buy_point, 'ref_zg', None)
            _rzd = getattr(buy_point, 'ref_zd', None)
            _zs_zg = float(_rzg) if _rzg is not None else _zs_ref.zg
            _zs_zd = float(_rzd) if _rzd is not None else _zs_ref.zd

            # 检查中枢突破
            # v5.3.2(D-2): 三买子项缩权 10+15+10/5=35/30 → 5+7+5/3=17/15
            if entry_price > _zs_zg:
                structure_score += 5
                details.append(f"中枢突破(ZG={_zs_zg:.2f}): 三买前提(+5)")
            else:
                details.append(f"未突破中枢(ZG={_zs_zg:.2f}): 非三买(+0)")

            # 回踩不破ZG
            if entry_price >= _zs_zg:
                structure_score += 7
                details.append(f"回踩不破ZG({_zs_zg:.2f}): 三买核心(+7)")
            elif entry_price >= _zs_zg * 0.97:
                structure_score += 4
                details.append(f"回踩接近ZG(破3%内): 勉强有效(+4)")
            else:
                details.append(f"跌破ZG>3%: 三买失败(+0)")

            # 回踩幅度（相对于中枢宽度）
            zs_width = _zs_zg - _zs_zd
            if zs_width > 0:
                pullback_to_zg = (entry_price - _zs_zd) / zs_width
                if pullback_to_zg > 1.0:
                    structure_score += 5
                    details.append(f"中枢上方站住: 强势确认(+5)")
                elif pullback_to_zg > 0.7:
                    structure_score += 3
                    details.append(f"中枢上部: 偏强(+3)")
                else:
                    details.append(f"中枢下部: 回踩过深(+0)")
        else:
            # 其他（反转后买点等）：基础分 + 简单判断
            if entry_price <= latest_zs.zd * 1.03:
                structure_score += 15
                details.append("中枢下沿附近: 安全边际(+15)")
            elif entry_price <= latest_zs.zg:
                structure_score += 5
                details.append("中枢内部: 中性(+5)")
            else:
                details.append("中枢上方: 追高(+0)")

    scores['structure'] = min(40, max(0, structure_score))

    # --- 2. 信号质量评分 (30分) ---
    # 新逻辑（v5.0）：基于信号完整性和可靠性评分，不再以价格位置为核心
    # 一买：背驰存在+确认信号+量能配合
    # 二买：前低不破+确认完整+量能配合
    # 三买：突破完成+回踩确认+量能配合
    signal_score = 0
    point_level = getattr(buy_point, 'level', 0)
    
    # 公共数据准备
    has_beichi = hasattr(buy_point, 'reason') and buy_point.reason and '背驰' in str(buy_point.reason)
    
    # MACD 状态
    macd_golden = False
    macd_improving = False
    if len(daily_analyzer.macd_data) >= 2:
        m0 = daily_analyzer.macd_data[-1]
        m1 = daily_analyzer.macd_data[-2]
        if float(m0.dif) > float(m0.dea):
            macd_golden = True
        elif float(m0.dif) > float(m1.dif) and abs(float(m0.dif) - float(m0.dea)) < abs(float(m1.dif) - float(m1.dea)):
            macd_improving = True
    
    # 向上一笔确认
    has_up_after = False
    if hasattr(buy_point, 'date'):
        has_up_after = any(
            b.direction == 'up' and b.start_date > buy_point.date
            for b in daily_analyzer.bis
        )
    
    # 量能检查（缩量止跌/放量突破）
    volume_score = 0
    vol_ratio = 1.0  # v5.3.2(D-5/T3): 提升作用域, 三买"突破放量"项需引用
    if hasattr(buy_point, 'date') and daily_analyzer.klines and len(daily_analyzer.klines) > 20:
        bp_date = date_to_str(buy_point.date)
        for i, k in enumerate(daily_analyzer.klines):
            if date_to_str(k.date) == bp_date:
                recent_volumes = [float(k_.volume) for k_ in daily_analyzer.klines[max(0,i-20):i]]
                if recent_volumes:
                    avg_vol = np.mean(recent_volumes)
                    cur_vol = float(k.volume)
                    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1
                    if vol_ratio < 0.7:
                        volume_score = 3  # 缩量止跌
                    elif 0.7 <= vol_ratio <= 1.5:
                        volume_score = 2  # 温和放量
                    elif vol_ratio > 2.0 and float(k.close) < float(k.open):
                        volume_score = 0  # 放量下跌
                    else:
                        volume_score = 1  # 一般
                break
    
    if point_level == 1:
        # 一买（趋势反转）：背驰+确认+量能
        if has_beichi:
            signal_score += 10
            details.append("底背驰存在: 反转必要条件(+10)")
        else:
            details.append("无背驰: 一买基础缺失(+0)")
        
        # 背驰级别（简化：有中枢即可）
        if daily_analyzer.zhongshus:
            signal_score += 5
            details.append("有中枢结构: 背驰级别有效(+5)")
        
        # 向上一笔确认
        if has_up_after:
            signal_score += 8
            details.append("向上一笔已出现: 确认信号(+8)")
        else:
            details.append("无向上一买确认: 信号不完整(+0)")
        
        # MACD配合
        if macd_golden:
            signal_score += 4
            details.append("MACD金叉: 动量配合(+4)")
        elif macd_improving:
            signal_score += 2
            details.append("MACD改善: 动量偏强(+2)")
        
        # 量能
        if volume_score >= 2:
            signal_score += 3
            details.append("量能健康: 抛压衰竭/温和放量(+3)")
        
        point_type_str = '一买'
        
    elif point_level == 2:
        # 二买（回调确认）：前低不破+确认+量能
        # 找前方一买
        # v5.3.2(D-1/T2): 与结构段(M4)对齐——取"最近的一买"(遍历不break),
        # 多轮下跌的股票不再锚到最早一买。
        first_buy = None
        for p in daily_analyzer.buy_sell_points:
            if p.type == 'buy' and p.level == 1 and hasattr(buy_point, 'date') and str(p.date) < str(buy_point.date):
                first_buy = p
        
        if first_buy:
            signal_score += 8
            details.append(f"前方一买存在: 二买前提(+8)")
            
            # 不破前低
            entry_price = buy_point.price
            if entry_price >= first_buy.price:
                signal_score += 12
                details.append(f"回调不破前低({first_buy.price:.2f}): 二买核心(+12)")
            else:
                details.append(f"跌破前低({first_buy.price:.2f}): 信号不可靠(+0)")
            
            # 回调幅度
            pullback_depth = (first_buy.price - entry_price) / first_buy.price if first_buy.price > 0 else 0
            if pullback_depth < 0.03:
                signal_score += 5
                details.append("回调幅度浅(<3%): 强势(+5)")
            elif pullback_depth < 0.08:
                signal_score += 3
                details.append("回调幅度适中(3-8%): 正常(+3)")
            else:
                details.append("回调幅度深(>8%): 偏弱(+0)")
            
            # 底分型确认
            if hasattr(buy_point, 'date') and hasattr(daily_analyzer, 'fenxings'):
                bp_date = str(buy_point.date)[:10]
                has_bottom = any(
                    str(fx.date)[:10] == bp_date and fx.type == 'bottom'
                    for fx in daily_analyzer.fenxings
                )
                if has_bottom:
                    signal_score += 3
                    details.append("底分型确认: 结构完成(+3)")
        else:
            details.append("无前方一买: 二买前提缺失(+0)")
        
        # 量能
        if volume_score >= 2:
            signal_score += 2
            details.append("量能健康: 抛压衰竭/温和放量(+2)")
        
        point_type_str = '二买'
        
    elif point_level == 3:
        # 三买（中枢突破）：突破+回踩确认+量能
        # v5.3.2(D-1/T2): 与结构段(M3)对齐——锚定"被突破的参照中枢"
        # ref_zg/ref_zd, 不再对照 zhongshus[-1]。此前结构段锚旧中枢、
        # 信号段锚新中枢, 同一次评分两项互相矛盾。
        _sig_rzg = getattr(buy_point, 'ref_zg', None)
        _sig_zs = daily_analyzer.zhongshus[-1] if daily_analyzer.zhongshus else None

        if _sig_zs:
            _sig_zg = float(_sig_rzg) if _sig_rzg is not None else float(_sig_zs.zg)

            entry_price = buy_point.price

            # 突破完成
            if entry_price > _sig_zg:
                signal_score += 8
                details.append(f"中枢突破(ZG={_sig_zg:.2f}): 前提完成(+8)")
            else:
                details.append(f"未突破中枢: 三买前提缺失(+0)")

            # 回踩不破ZG
            if entry_price >= _sig_zg:
                signal_score += 10
                details.append(f"回踩不破ZG({_sig_zg:.2f}): 三买核心(+10)")
            elif entry_price >= _sig_zg * 0.97:
                signal_score += 5
                details.append(f"回踩接近ZG(破<3%): 勉强有效(+5)")
            else:
                details.append(f"跌破ZG>3%: 三买失败(+0)")

            # 量能配合
            if volume_score >= 2:
                signal_score += 5
                details.append("量能健康: 放量突破/缩量回踩(+5)")

            # 中枢上方站住
            if entry_price > _sig_zg:
                signal_score += 4
                details.append("中枢上方站住: 强势确认(+4)")

            # v5.3.2(D-5/T3): 突破伴随放量(+3)——三买信号原满分27, 比一买/
            # 二买的30天然少3分(池内全三买时系统性吃亏)。补"突破当日放量"
            # 项对齐满分30: 第20课三买确认本就隐含突破有效性的量能要求。
            if vol_ratio > 1.5:
                signal_score += 3
                details.append(f"突破放量(量比{vol_ratio:.2f}): 突破有效性确认(+3)")
        else:
            details.append("无中枢: 三买无基础(+0)")
        
        point_type_str = '三买'
        
    else:
        signal_score = 15
        point_type_str = f'未知(level={point_level})'
        details.append(f"未知类型level={point_level}: 信号强度不确定(+15)")

    scores['signal_quality'] = min(30, max(0, signal_score))

    # --- 3. 多级别共振评分 (20分) ---
    # 新逻辑（v5.0）：基于方向一致性和结构配合评分
    # 核心：30分钟买点类型与日线是否一致 + 时间同步 + 结构配合
    resonance_score = 5  # 基础分（仅日线）
    
    if m30_analyzer and hasattr(buy_point, 'date'):
        # 获取30分钟买点
        m30_buy_points = [p for p in m30_analyzer.buy_sell_points if 'buy' in str(p.type).lower()]
        
        if m30_buy_points:
            bp_date = date_to_str(buy_point.date)
            bp_date_dt = datetime.strptime(bp_date, '%Y-%m-%d')
            
            # 找最近的30分钟买点（±5天内）
            nearby_points = []
            for p in m30_buy_points:
                try:
                    p_date = date_to_str(p.date)
                    p_date_dt = datetime.strptime(p_date, '%Y-%m-%d')
                    days_diff = abs((p_date_dt - bp_date_dt).days)
                    if days_diff <= 5:
                        nearby_points.append((p, days_diff))
                except:
                    continue
            
            if nearby_points:
                # 取最近的30分钟买点
                nearby_points.sort(key=lambda x: x[1])
                closest_m30 = nearby_points[0][0]
                days_diff = nearby_points[0][1]
                
                # 基础共振存在
                resonance_score += 8
                details.append(f"30分钟有买点(±{days_diff}天): 基础共振(+8)")
                
                # 类型一致性检查
                daily_level = getattr(buy_point, 'level', 0)
                m30_level = getattr(closest_m30, 'level', 0)
                
                if daily_level == m30_level and daily_level > 0:
                    resonance_score += 5
                    details.append(f"30分钟买点类型一致(都是{daily_level}买): 方向一致(+5)")
                elif daily_level > 0 and m30_level > 0:
                    details.append(f"30分钟买点类型不同(日{daily_level}买/30m{m30_level}买): 方向不一致(+0)")
                
                # 时间同步（±3天内额外加分）
                if days_diff <= 3:
                    resonance_score += 3
                    details.append(f"时间同步(±{days_diff}天): 同步良好(+3)")
                
                # 30分钟背驰检查
                if hasattr(closest_m30, 'reason') and closest_m30.reason and '背驰' in str(closest_m30.reason):
                    resonance_score += 2
                    details.append("30分钟有背驰: 结构确认(+2)")
                
                # 30分钟中枢与日线中枢同向
                # v5.4(C-04): 各级别与自身前一中枢比较运动方向——旧实现拿两个
                # 级别的绝对 ZD 互相比(m30.zd>daily.zd 与 daily.zd>m30.zd 互为
                # 否定)，条件恒互斥、等价于要求两级中枢 ZD 完全相等，+2 分实际
                # 永远拿不到(共振有效上限18)。现按缠论语义: 两级中枢均较各自
                # 前一中枢上移才算"趋势配合"(买点共振场景只认向上)。
                if len(m30_analyzer.zhongshus) >= 2 and len(daily_analyzer.zhongshus) >= 2:
                    m30_zd_up = m30_analyzer.zhongshus[-1].zd > m30_analyzer.zhongshus[-2].zd
                    daily_zd_up = daily_analyzer.zhongshus[-1].zd > daily_analyzer.zhongshus[-2].zd
                    if m30_zd_up and daily_zd_up:
                        resonance_score += 2
                        details.append("30分钟与日线中枢均较前一中枢上移: 趋势配合(+2)")
            else:
                details.append("30分钟有买点但不在±5天窗口内: 共振弱(+0)")
        else:
            details.append("30分钟无买点: 仅日线信号(+0)")
    else:
        if not m30_analyzer:
            details.append("无30分钟分析器: 仅日线信号(+0)")
    
    scores['resonance'] = min(20, max(0, resonance_score))

    # --- 4. 量价辅助评分 (10分) ---
    # 新逻辑（v5.0）：基于量结构评分（跨日对比），不同买点类型有不同标准
    # 一买：底部缩量 → 抛压衰竭
    # 二买：回调缩量 → 卖压减轻
    # 三买：放量突破 + 缩量回踩
    volume_score = 5  # 基础分
    point_level = getattr(buy_point, 'level', 0)
    
    try:
        if daily_analyzer.klines and len(daily_analyzer.klines) > 20:
            if hasattr(buy_point, 'date'):
                bp_date = date_to_str(buy_point.date)
                bp_idx = None
                for i, k in enumerate(daily_analyzer.klines):
                    if date_to_str(k.date) == bp_date:
                        bp_idx = i
                        break
                
                if bp_idx is not None:
                    # 计算20日均量
                    start_idx = max(0, bp_idx - 20)
                    recent_volumes = [float(k_.volume) for k_ in daily_analyzer.klines[start_idx:bp_idx]]
                    avg_vol = np.mean(recent_volumes) if recent_volumes else 0
                    cur_vol = float(daily_analyzer.klines[bp_idx].volume)
                    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1
                    
                    # 检查买点日是否下跌
                    k = daily_analyzer.klines[bp_idx]
                    is_down = float(k.close) < float(k.open)
                    
                    # 通用：放量下跌扣分
                    if vol_ratio > 2.0 and is_down:
                        volume_score -= 3
                        details.append(f"放量下跌(量比{vol_ratio:.2f}): 抛压重(-3)")
                    
                    if point_level == 1:
                        # 一买：底部缩量 → 抛压衰竭
                        # v5.3.1(F25): 原为两个完全相同的 vol_ratio<0.7 条件
                        # 叠加(+3/+2, 笔误式重复), 合并为一条 +5, 总分不变
                        if vol_ratio < 0.7:
                            volume_score += 5
                            details.append(f"底部缩量(量比{vol_ratio:.2f}): 抛压衰竭+量价配合(+5)")
                            
                    elif point_level == 2:
                        # 二买：回调缩量 → 卖压减轻
                        # 找一买日量
                        first_buy_vol = None
                        for p in daily_analyzer.buy_sell_points:
                            if p.type == 'buy' and p.level == 1 and hasattr(p, 'date'):
                                for k2 in daily_analyzer.klines:
                                    if date_to_str(k2.date) == date_to_str(p.date):
                                        first_buy_vol = float(k2.volume)
                                        break
                                break
                        
                        if first_buy_vol and first_buy_vol > 0:
                            vol_vs_first = cur_vol / first_buy_vol
                            if vol_vs_first < 0.7:
                                volume_score += 3
                                details.append(f"回调缩量(量比{vol_vs_first:.2f}<一买): 卖压减轻(+3)")
                            elif vol_vs_first < 1.0:
                                volume_score += 1
                                details.append(f"回调量减(量比{vol_vs_first:.2f}): 卖压略减(+1)")
                        
                        if vol_ratio < 0.7:
                            volume_score += 2
                            details.append(f"底部缩量(量比{vol_ratio:.2f}): 抛压衰竭(+2)")
                            
                    elif point_level == 3:
                        # 三买：突破放量 + 缩量回踩
                        if vol_ratio > 1.5:
                            volume_score += 3
                            details.append(f"放量突破(量比{vol_ratio:.2f}): 量能确认(+3)")
                        elif vol_ratio > 1.2:
                            volume_score += 1
                            details.append(f"温和放量(量比{vol_ratio:.2f}): 量能略增(+1)")
                        
                        # 回踩日缩量（检查买点日是否比前日缩量）
                        if bp_idx > 0:
                            prev_vol = float(daily_analyzer.klines[bp_idx - 1].volume)
                            if prev_vol > 0 and cur_vol < prev_vol * 0.7:
                                volume_score += 2
                                details.append(f"回踩缩量(量比{cur_vol/prev_vol:.2f}): 健康回踩(+2)")
    except Exception as e:
        print(f'[validate_tech_score] 警告: 成交量分析失败: {e}')
    
    scores['volume'] = min(10, max(0, volume_score))

    # ── 7. 跌破惩罚（v5.0 统一规则）──
    # 三买：跌破中枢ZD
    # 一买/二买：跌破买点价格
    penalty = 0
    try:
        point_level = getattr(buy_point, 'level', 0)
        if daily_analyzer.klines and len(daily_analyzer.klines) > 0:
            current_price = float(daily_analyzer.klines[-1].close)
            
            if point_level == 3 and daily_analyzer.zhongshus:
                # 三买：检查跌破中枢ZD
                # v5.3.1(M3): 锚定被突破中枢的 ZD(ref_zd), 无 ref 时回退最新中枢——
                # 对照三买后形成的新中枢 ZD 会漏罚(新ZD通常更高, 跌回旧中枢内却
                # 仍在新ZD上方 → 旧逻辑不罚, 结构实际已坏)
                latest_zs = daily_analyzer.zhongshus[-1]
                _rzd_pen = getattr(buy_point, 'ref_zd', None)
                zs_zd = float(_rzd_pen) if _rzd_pen is not None else float(latest_zs.zd)
                if current_price > 0 and current_price < zs_zd:
                    pct_drop = (zs_zd - current_price) / zs_zd * 100
                    if pct_drop > 5:
                        penalty = -15
                        details.append(f"跌破中枢ZD>5%({zs_zd:.2f}, -{pct_drop:.1f}%): 结构大概率已坏({penalty})")
                    elif pct_drop > 2:
                        penalty = -7
                        details.append(f"跌破中枢ZD 2-5%({zs_zd:.2f}, -{pct_drop:.1f}%): 结构可能已坏({penalty})")
                    else:
                        penalty = -3
                        details.append(f"轻微跌破中枢ZD<2%({zs_zd:.2f}, -{pct_drop:.1f}%): 噪声范围内({penalty})")
                        
            elif point_level in (1, 2) and hasattr(buy_point, 'price') and buy_point.price > 0:
                # 一买/二买：检查跌破买点价格
                buy_price = float(buy_point.price)
                if current_price > 0 and current_price < buy_price:
                    pct_drop = (buy_price - current_price) / buy_price * 100
                    if pct_drop > 10:
                        penalty = -20
                        details.append(f"跌破买价>10%(¥{buy_price:.2f}, -{pct_drop:.1f}%): 结构已坏({penalty})")
                    elif pct_drop > 2:
                        penalty = -10
                        details.append(f"跌破买价 2-10%(¥{buy_price:.2f}, -{pct_drop:.1f}%): 结构可能已坏({penalty})")
                    else:
                        penalty = -3
                        details.append(f"轻微跌破买价<2%(¥{buy_price:.2f}, -{pct_drop:.1f}%): 噪声范围内({penalty})")
    except Exception as e:
        print(f'[validate_tech_score] 警告: 跌破检查失败: {e}')
    scores['penalty'] = penalty

    # === 综合评分 ===
    # 满分：structure(40) + signal_quality(30) + resonance(20) + volume(10) = 100
    total_score = sum(scores.values())
    
    # 评分映射（满分100，不含惩罚项）
    if total_score >= 80:
        grade = 'A+'
    elif total_score >= 70:
        grade = 'A'
    elif total_score >= 60:
        grade = 'B+'
    elif total_score >= 50:
        grade = 'B'
    elif total_score >= 40:
        grade = 'C+'
    else:
        grade = 'C'

    return {
        'tech_score': total_score,
        'grade': grade,
        'breakdown': scores,
        'details': '; '.join(details),
        'confidence': confidence_score,
        'point_type': point_type_str,
        'point_level': point_level,
    }


def get_forward_returns(daily_data: pd.DataFrame, entry_date: str, periods: list = [5, 10, 20, 60]):
    """计算买入后 N 个交易日的收益率"""
    results = {}
    daily_data = daily_data.copy()
    if 'date' in daily_data.columns:
        daily_data['date'] = daily_data['date'].astype(str).str[:10]
    
    try:
        entry_idx = daily_data[daily_data['date'] == entry_date].index[0]
    except (IndexError, KeyError):
        return {f'fwd_{p}d': None for p in periods}
    
    entry_close = float(daily_data.iloc[entry_idx]['close'])
    
    for period in periods:
        target_idx = entry_idx + period
        if target_idx < len(daily_data):
            exit_close = float(daily_data.iloc[target_idx]['close'])
            returns = (exit_close - entry_close) / entry_close * 100
        else:
            returns = None
        results[f'fwd_{period}d'] = returns
    
    return results


def run_single_validation(symbol: str, name: str = "", start_date: str = "2024-01-01"):
    """对单只股票运行评分验证"""
    engine = BacktestEngine(symbol, name=name or symbol)
    
    # 1. 加载数据
    daily_data, m30_data = engine.load_all_data(start_date)
    if daily_data.empty:
        return None
    
    # 2. 运行分析
    daily_analyzer, m30_analyzer, signal_points = engine.run_single_analysis(
        daily_data, m30_data
    )
    
    # 3. 对每个买点计算技术评分和后续收益
    results = []
    buy_points = [p for p in daily_analyzer.buy_sell_points if 'buy' in str(p.type).lower()]
    
    for bp in buy_points:
        entry_date = date_to_str(bp.date)
        
        # 技术评分
        score_info = compute_technical_score(daily_analyzer, m30_analyzer, bp)
        
        # 后续收益
        fwd_returns = get_forward_returns(daily_data, entry_date)
        
        # 置信度
        conf = getattr(bp, 'multilevel_confirmation', {})
        
        result = {
            'symbol': symbol,
            'name': name or symbol,
            'date': entry_date,
            'price': bp.price,
            'point_type': score_info.get('point_type', str(bp.type)),
            'tech_score': score_info['tech_score'],
            'grade': score_info['grade'],
            'confidence': conf.get('confidence_score', 0),
            'm30_confirmed': conf.get('m30_confirmation', False),
        }
        result.update(fwd_returns)
        result['score_breakdown'] = score_info['breakdown']
        result['score_detail'] = score_info['details']
        results.append(result)
    
    return results


def print_validation_report(all_results: list):
    """打印验证报告"""
    if not all_results:
        print("\n⚠  无回测结果")
        return
    
    df = pd.DataFrame(all_results)
    
    print("\n" + "=" * 80)
    print("  缠论技术评分验证报告 — Phase 1")
    print("=" * 80)
    
    # 1. 按评分等级分组
    print("\n  📊 按评分等级分组（技术评分 vs 后续收益率）")
    print("  " + "-" * 70)
    
    grades = ['A+', 'A', 'B+', 'B', 'C+', 'C']
    for grade in grades:
        subset = df[df['grade'] == grade]
        if len(subset) == 0:
            continue
        
        avg_tech = subset['tech_score'].mean()
        avg_conf = subset['confidence'].mean()
        count = len(subset)
        
        fwd_avgs = {}
        for p in [5, 10, 20, 60]:
            col = f'fwd_{p}d'
            valid = subset[col].dropna()
            fwd_avgs[f'{p}d'] = f"{valid.mean():+.2f}%" if len(valid) > 0 else "N/A"
        
        print(f"  {grade} (均分{avg_tech:.0f}, 置信{avg_conf:.1f}, {count}次): "
              f"5日={fwd_avgs['5d']}, 10日={fwd_avgs['10d']}, "
              f"20日={fwd_avgs['20d']}, 60日={fwd_avgs['60d']}")
    
    # 2. 按置信度分组
    print("\n  📊 按置信度分组")
    print("  " + "-" * 70)
    for conf_level in range(0, 6):
        subset = df[df['confidence'] == conf_level]
        if len(subset) == 0:
            continue
        count = len(subset)
        avg_tech = subset['tech_score'].mean()
        
        fwd_avgs = {}
        for p in [5, 10, 20, 60]:
            col = f'fwd_{p}d'
            valid = subset[col].dropna()
            fwd_avgs[f'{p}d'] = f"{valid.mean():+.2f}%" if len(valid) > 0 else "N/A"
        
        print(f"  置信度={conf_level} (均分{avg_tech:.0f}, {count}次): "
              f"5日={fwd_avgs['5d']}, 10日={fwd_avgs['10d']}, "
              f"20日={fwd_avgs['20d']}, 60日={fwd_avgs['60d']}")
    
    # 3. 按信号类型分组
    print("\n  📊 按信号类型分组")
    print("  " + "-" * 70)
    for ptype in df['point_type'].unique():
        subset = df[df['point_type'] == ptype]
        count = len(subset)
        avg_tech = subset['tech_score'].mean()
        
        fwd_avgs = {}
        for p in [5, 10, 20, 60]:
            col = f'fwd_{p}d'
            valid = subset[col].dropna()
            fwd_avgs[f'{p}d'] = f"{valid.mean():+.2f}%" if len(valid) > 0 else "N/A"
        
        print(f"  {ptype} ({count}次, 均分{avg_tech:.0f}): "
              f"5日={fwd_avgs['5d']}, 10日={fwd_avgs['10d']}, "
              f"20日={fwd_avgs['20d']}, 60日={fwd_avgs['60d']}")
    
    # 4. 相关性分析
    print("\n  📈 评分与收益相关性")
    print("  " + "-" * 70)
    for p in [5, 10, 20, 60]:
        col = f'fwd_{p}d'
        valid = df[['tech_score', col]].dropna()
        if len(valid) >= 5:
            corr = valid['tech_score'].corr(valid[col])
            print(f"  技术评分 vs {p}日后收益率: 相关系数 r = {corr:.3f}")
        else:
            print(f"  技术评分 vs {p}日后收益率: 样本不足({len(valid)}个)")
    
    # 5. 买入信号评分 vs 实际交易盈亏
    print("\n  📋 所有信号明细")
    print("  " + "-" * 70)
    for _, row in df.iterrows():
        fwd_str = ' | '.join([f"{p}d:{row[f'fwd_{p}d']:+.1f}%" if pd.notna(row[f'fwd_{p}d']) else f"{p}d:N/A" 
                              for p in [5, 10, 20]])
        print(f"  {row['symbol']} {row['date']} | {row['point_type']:8s} | "
              f"评分{row['tech_score']:.0f}({row['grade']}) | 置信{row['confidence']} | "
              f"后续: {fwd_str}")
    
    # 6. 按股票分类汇总（基于 classify_by_industry 统一分类）
    print("\n  🏷 按股票类型汇总")
    print("  " + "-" * 70)
    
    # 从结果中动态获取类型分组
    type_groups = {}
    for _, row in df.iterrows():
        stype = row.get('stock_type', '其他')
        if stype not in type_groups:
            type_groups[stype] = []
        type_groups[stype].append(row)
    
    # 按样本数降序显示
    for label in sorted(type_groups.keys(), key=lambda k: len(type_groups[k]), reverse=True):
        subset = pd.DataFrame(type_groups[label])
        count = len(subset)
        if count == 0:
            continue
        avg_tech = subset['tech_score'].mean()
        win_5d = (subset['fwd_5d'].dropna() > 0).sum() / max(len(subset['fwd_5d'].dropna()), 1) * 100
        
        fwd_avgs = {}
        for p in [5, 10, 20, 60]:
            col = f'fwd_{p}d'
            valid = subset[col].dropna()
            fwd_avgs[f'{p}d'] = f"{valid.mean():+.2f}%" if len(valid) > 0 else "N/A"
        
        print(f"  {label} ({count}次, 均分{avg_tech:.0f}): 胜率(5d)={win_5d:.0f}% | "
              f"5日={fwd_avgs['5d']}, 10日={fwd_avgs['10d']}, 20日={fwd_avgs['20d']}, 60日={fwd_avgs['60d']}")
    
    # 7. 保存到文件
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tech_score_validation.json")
    output_data = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_signals': len(all_results),
        'summary': {
            grade: {
                'count': len(df[df['grade'] == grade]),
                'avg_tech_score': float(df[df['grade'] == grade]['tech_score'].mean()) if len(df[df['grade'] == grade]) > 0 else 0,
                'fwd_20d_return': f"{df[df['grade'] == grade]['fwd_20d'].mean():+.2f}%" if len(df[df['grade'] == grade]) > 0 else "N/A"
            }
            for grade in grades
            if len(df[df['grade'] == grade]) > 0
        },
        'signals': all_results
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 详细数据已保存: {output_path}")
    
    return df


def main():
    # DEFAULT_STOCKS 已包含全部18只（含行业信息），--full 不再特殊处理
    stocks = DEFAULT_STOCKS
    if '--stocks' in sys.argv:
        idx = sys.argv.index('--stocks')
        if idx + 1 < len(sys.argv):
            stocks = [s.strip() for s in sys.argv[idx + 1].split(',')]
    elif '--quick' in sys.argv:
        stocks = DEFAULT_STOCKS[:5]  # 快速测试：前5只
    
    # 构建 股票代码 → 股票类型 的映射
    stock_type_map = {}
    for item in stocks:
        if isinstance(item, (tuple, list)) and len(item) >= 3:
            code, name, industry = item[0], item[1], item[2]
            stock_type_map[code] = classify_by_industry(industry)
        elif isinstance(item, (tuple, list)) and len(item) >= 1:
            code = item[0]
            stock_type_map[code] = "其他"  # 无行业信息
    
    print(f"验证股票: {len(stocks)}只")
    type_counts = Counter(stock_type_map.values())
    print(f"  类型分布: {dict(type_counts)}")
    print(f"开始运行...\n")
    
    all_results = []
    error_stocks = []
    
    for i, item in enumerate(stocks):
        # 兼容两种格式：字符串 "600309" 或元组 ("600309", "万华化学")
        if isinstance(item, (tuple, list)):
            symbol, name = item[0], item[1] if len(item) > 1 else item[0]
        else:
            symbol, name = item, item
        print(f"[{i+1}/{len(stocks)}] {symbol} {name}...", end=" ", flush=True)
        try:
            results = run_single_validation(symbol, name)
            if results:
                # 为每个信号附加股票类型（统一由 classify_by_industry 判定）
                stock_type = stock_type_map.get(symbol, "其他")
                for r in results:
                    r['stock_type'] = stock_type
                all_results.extend(results)
                print(f"✅ {len(results)}个信号 ({stock_type})")
            else:
                print("⚠ 无数据")
        except Exception as e:
            print(f"❌ {e}")
            error_stocks.append(symbol)
    
    print(f"\n共发现 {len(all_results)} 个买点信号")
    if error_stocks:
        print(f"失败: {', '.join(error_stocks)}")
    
    # 打印验证报告
    if all_results:
        print_validation_report(all_results)
    
    # 核心结论
    print("\n" + "=" * 80)
    print("  核心结论")
    print("=" * 80)
    print("  如果高评分信号的后续收益率 > 低评分信号 → 评分模型有效")
    print("  如果相关系数 r > 0.3 → 评分与收益正相关，可纳入三维系统")
    print("  如果无明显区分 → 需要调整评分因子权重")
    print("=" * 80)


if __name__ == "__main__":
    main()
