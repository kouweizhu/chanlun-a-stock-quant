"""
segment_analyzer.py — 线段中枢缠论分析模块（独立完整版）
========================================================

基于缠论线段定义的量化分析引擎，包含：
1. 线段划分（特征序列 + 包含处理 + 线段破坏判断）
2. 线段中枢构建（3段重叠 + 中枢延伸）
3. 中枢扩张识别（同级别/同方向校验 + 条件1&2 + 保护机制）
4. 线段级别买卖点识别（一类/二类/三类）

设计原则：
- 独立于笔中枢系统（generate_analysis.py），不修改原代码
- 复用 ChanLunAnalyzer 的 K线包含处理、MACD 计算等基础功能
- 直接在日线级别运行，输出可嵌入 HTML 双视角报告

用法：
    from segment_analyzer import SegmentChanLunAnalyzer
    
    analyzer = SegmentChanLunAnalyzer()
    result = analyzer.analyze(klines_data)  # klines_data: List[dict]
    
    print(f"线段数: {len(result.segments)}")
    print(f"线段中枢: {len(result.seg_zhongshus)}")
    print(f"线段买卖点: {len(result.seg_buy_sell_points)}")

作者: Hermes Agent + 徒弟
日期: 2026-05-03
"""

from dataclasses import dataclass, field
from date_utils import date_to_str, parse_date_to_datetime
from typing import List, Optional, Tuple
from datetime import datetime, timedelta
import sys

from config_loader import (
    THRESHOLD_SEGMENT_BEICHI, THRESHOLD_SEGMENT_SECOND_BUY_TOLERANCE,
    THRESHOLD_SEGMENT_MAX_ZHONGSHU_BI, THRESHOLD_SEGMENT_MAX_ZHONGSHU_DAYS,
    THRESHOLD_SEGMENT_MIN_FLUCTUATION_PCT,
)

# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════


@dataclass
class FeatureElement:
    """特征序列元素

    对于一个线段，其特征序列由该线段中与线段方向相反的笔构成：
    - 向上线段 → 特征序列 = 向下笔的列表
    - 向下线段 → 特征序列 = 向上笔的列表

    每个元素的 high/low 取对应笔的极端价格。
    """

    high: float
    low: float
    start_date: str
    end_date: str
    bi_index: int  # 对应在 bis 列表中的索引


@dataclass
class Segment:
    """线段

    由奇数笔交替构成，如：
    - 向上线段：上-下-上-下-上（>=3笔，奇数）
    - 向下线段：下-上-下-上-下（>=3笔，奇数）
    """

    start_date: str
    start_price: float
    end_date: str
    end_price: float
    direction: str  # 'up' | 'down'
    bi_count: int  # 包含的笔数
    bi_start_idx: int  # 起始笔在 bis 列表中的索引
    bi_end_idx: int  # 结束笔在 bis 列表中的索引
    high_price: float = 0.0  # 线段波动最高点
    low_price: float = 0.0  # 线段波动最低点


@dataclass
class SegmentZhongShu:
    """线段中枢

    由至少3个连续线段的重叠区间构成。
    GG/DD 记录中枢区间的实际波动范围（用于扩张判断）。
    """

    start_date: str
    end_date: str
    zg: float  # 中枢上沿
    zd: float  # 中枢下沿
    gg: float  # 中枢区间内最高点（波动边界）
    dd: float  # 中枢区间内最低点（波动边界）
    segment_count: int  # 包含的线段数
    direction: str = "up"  # 中枢所在趋势方向（用于同方向扩张校验）


@dataclass
class SegmentBuySellPoint:
    """线段级别买卖点"""

    type: str  # 'buy' | 'sell'
    level: int  # 1=一类, 2=二类, 3=三类
    date: str
    price: float
    reason: str


# ═══════════════════════════════════════════════════════════
# 无限扩张保护参数（v4.2 从 config 读取，原硬编码 27/120/0.05）
# ═══════════════════════════════════════════════════════════

MAX_ZHONGSHU_BI = THRESHOLD_SEGMENT_MAX_ZHONGSHU_BI  # 单个中枢最大笔数（9段×3笔/段）
MAX_ZHONGSHU_DAYS = THRESHOLD_SEGMENT_MAX_ZHONGSHU_DAYS  # 最大存活交易日
MIN_FLUCTUATION_PCT = THRESHOLD_SEGMENT_MIN_FLUCTUATION_PCT  # 最小波动阈值（5%）


# ═══════════════════════════════════════════════════════════
# 第一部分：线段划分引擎
# ═══════════════════════════════════════════════════════════


def _make_feature_element(bi, bi_index: int) -> FeatureElement:
    """从笔创建特征序列元素"""
    return FeatureElement(
        high=max(bi.start_price, bi.end_price),
        low=min(bi.start_price, bi.end_price),
        start_date=bi.start_date,
        end_date=bi.end_date,
        bi_index=bi_index,
    )


def _elements_overlap(a: FeatureElement, b: FeatureElement) -> bool:
    """判断两个特征序列元素是否有重叠"""
    return a.low < b.high and b.low < a.high


def _merge_feature_elements(
    elements: List[FeatureElement], direction: str
) -> List[FeatureElement]:
    """特征序列包含处理

    严格遵守缠论K线包含处理规则，但适配特征序列的方向：
    - 向上线段（特征序列=向下笔）→ 处理方向为"向下" → 取低低
    - 向下线段（特征序列=向上笔）→ 处理方向为"向上" → 取高高

    取低低（DOWN direction）:
        合并后 high = min(high_a, high_b)
        合并后 low = min(low_a, low_b)

    取高高（UP direction）:
        合并后 high = max(high_a, high_b)
        合并后 low = max(low_a, low_b)
    """
    if len(elements) < 2:
        return elements

    # 特征序列的包含方向与线段方向相反
    merge_dir = "down" if direction == "up" else "up"
    # 单次遍历（不递归）：与K线包含处理一致，避免级联合并
    merged = []
    i = 0
    while i < len(elements):
        if i == len(elements) - 1:
            merged.append(elements[i])
            i += 1
            continue

        current = elements[i]
        next_elem = elements[i + 1]

        # 检查包含关系
        next_in_current = (
            next_elem.high <= current.high
            and next_elem.low >= current.low
        )
        current_in_next = (
            current.high <= next_elem.high
            and current.low >= next_elem.low
        )

        if next_in_current or current_in_next:
            # 包含关系成立，按方向合并
            if merge_dir == "up":
                merged_elem = FeatureElement(
                    high=max(current.high, next_elem.high),
                    low=max(current.low, next_elem.low),
                    start_date=current.start_date,
                    end_date=next_elem.end_date,
                    bi_index=current.bi_index if next_in_current else next_elem.bi_index,
                )
            else:
                merged_elem = FeatureElement(
                    high=min(current.high, next_elem.high),
                    low=min(current.low, next_elem.low),
                    start_date=current.start_date,
                    end_date=next_elem.end_date,
                    bi_index=current.bi_index if next_in_current else next_elem.bi_index,
                )
            merged.append(merged_elem)
            i += 2
        else:
            merged.append(current)
            i += 1

    return merged


def _find_top_pattern(
    elements: List[FeatureElement],
) -> Optional[Tuple[int, int, int]]:
    """在特征序列中寻找顶分型

    顶分型条件：中间元素的最高点严格高于左右两侧元素的最高点
    （mid.high > left.high 且 mid.high > right.high）。

    v5.4(注释对齐)：旧 docstring 写的"三者不重叠"与函数体不符——本函数
    只比较高点，不做重叠检查；特征序列间的包含合并已由上游
    _merge_feature_elements（线段破坏点主循环内、_find_top_pattern 调用前）
    完成，此处无需重复处理。算法实质正确，仅注释表述曾误导维护者。

    Returns:
        (left_idx, middle_idx, right_idx) or None
    """
    if len(elements) < 3:
        return None

    for i in range(1, len(elements) - 1):
        left, mid, right = elements[i - 1], elements[i], elements[i + 1]
        # 顶分型：中间 highest high 高于两侧
        if mid.high > left.high and mid.high > right.high:
            return (i - 1, i, i + 1)
    return None


def _find_bottom_pattern(
    elements: List[FeatureElement],
) -> Optional[Tuple[int, int, int]]:
    """在特征序列中寻找底分型

    底分型条件：中间元素的低点是三者的最低点

    Returns:
        (left_idx, middle_idx, right_idx) or None
    """
    if len(elements) < 3:
        return None

    for i in range(1, len(elements) - 1):
        left, mid, right = elements[i - 1], elements[i], elements[i + 1]
        if mid.low < left.low and mid.low < right.low:
            return (i - 1, i, i + 1)
    return None


def _check_feature_gap(
    elements: List[FeatureElement], pattern_indices: Tuple[int, int, int]
) -> bool:
    """检查特征序列中第一、二元素之间是否有缺口

    缺口 = 不重叠 ：
    - 顶分型：左边元素的高 < 中间元素的高 且 左边 high < 中间 low → 有缺口？
    - 实际判断：第一元素和第二元素的区间是否有重叠

    Returns:
        True: 有缺口（第二种破坏）; False: 无缺口（第一种破坏）
    """
    first_idx, second_idx, _ = pattern_indices
    first = elements[first_idx]
    second = elements[second_idx]
    return not _elements_overlap(first, second)


def _extract_feature_sequence(
    bis, seg_start: int, seg_end: int, direction: str
) -> List[FeatureElement]:
    """从笔序列中提取特征序列

    在线段划分过程中，从当前线段的笔中提取与线段方向相反的笔。

    Args:
        bis: 所有笔的列表
        seg_start: 线段起始笔索引
        seg_end: 线段结束笔索引（含）
        direction: 线段方向 'up' | 'down'
    Returns:
        特征序列元素列表
    """
    elements = []
    for i in range(seg_start, seg_end + 1):
        bi = bis[i]
        if direction == "up" and bi.direction == "down":
            elements.append(_make_feature_element(bi, i))
        elif direction == "down" and bi.direction == "up":
            elements.append(_make_feature_element(bi, i))
    return elements


def find_segments(bis) -> List[Segment]:
    """完整版线段划分主入口

    算法流程：
    1. 从第一个笔开始，逐笔扩展线段
    2. 新笔与线段方向一致 → 延伸线段
    3. 新笔与线段方向相反 → 检查是否构成破坏
       a. 提取特征序列
       b. 特征序列包含处理
       c. 寻找顶/底分型
       d. 分型确认 → 线段结束，新线段开始

    # 线段破坏判断：
    # - 第一种破坏（无缺口）：分型确认后原线段立即结束
    # - 第二种破坏（有缺口）：分型确认后，需等待后续反向线段（至少再
    #   积累3笔并形成反向特征序列分型）确认，原线段才结束。
    #   v4.2 修复：此前"不论有无缺口统一分割"会提前切断线段，产生碎片。
    #   现在区分缺口：无缺口立即结束；有缺口标记 pending，等待反向确认
    #   （受限保护：等待窗口内未确认则强制分割，防止线段无限延伸）。

    Args:
        bis: ChanLunAnalyzer 分析得到的 Bi 对象列表
    Returns:
        List[Segment]: 划分出的线段列表
    """
    if len(bis) < 3:
        return []

    segments = []
    seg_start = 0  # 当前线段的起始笔索引

    # 第二种破坏待确认状态
    pending_end = None  # 待确认的线段结束笔索引（有缺口时的候选分割点）

    i = 2  # 至少3笔才可能成段
    while i < len(bis):
        # 确定当前线段方向（由首笔决定）
        direction = bis[seg_start].direction

        # 验证当前笔范围的方向交替
        current_bis = bis[seg_start : i + 1]
        if not all(
            current_bis[j].direction != current_bis[j + 1].direction
            for j in range(len(current_bis) - 1)
        ):
            i += 1
            continue

        # 提取特征序列
        features = _extract_feature_sequence(bis, seg_start, i, direction)
        if len(features) < 3:
            i += 1
            continue

        # 特征序列包含处理
        merged_features = _merge_feature_elements(features, direction)
        if len(merged_features) < 3:
            i += 1
            continue

        # 寻找分型
        if direction == "up":
            pattern = _find_top_pattern(merged_features)
        else:
            pattern = _find_bottom_pattern(merged_features)

        if pattern is None:
            # 没有分型，线段继续延伸
            i += 1
            continue

        # 检查第一、二元素之间是否有缺口（第二破坏判定）
        has_gap = _check_feature_gap(merged_features, pattern)

        if has_gap:
            # ═══ 第二种破坏（有缺口）═══
            # 缠论要求：需等待反向线段确认后才结束原线段。
            # 候选分割点 = 分型右侧元素对应笔的前一根
            right_feature = merged_features[pattern[2]]
            candidate_end = right_feature.bi_index - 1

            if pending_end is None:
                # 首次出现候选分割点：记录待确认
                pending_end = candidate_end
                i += 1
                continue

            # 已有 pending：当前又出现新的候选分割点且更靠后，
            # 说明原线段已越过 pending 点继续延伸并再次形成分型。
            # 此时 pending 点被"反向走势"确认 → 在 pending 处分割原线段。
            if candidate_end > pending_end:
                seg_end = pending_end
                if seg_end - seg_start >= 2:
                    seg = _build_segment(bis, seg_start, seg_end, direction)
                    segments.append(seg)
                    seg_start = seg_end + 1
                    i = seg_start + 2
                    pending_end = None
                    continue

            # 未确认，继续延伸（笔列表有限，不会无限延伸；
            # 极端长线段由 _merge_same_direction_segments 兜底）
            i += 1
            continue

        # ═══ 第一种破坏（无缺口）═══
        # 分型确认后原线段立即结束
        right_feature = merged_features[pattern[2]]
        seg_end = right_feature.bi_index - 1

        # 如果有 pending 候选点且当前无缺口分型在 pending 之后
        # → pending 点被后续走势确认，先分割 pending
        if pending_end is not None and seg_end > pending_end:
            _pend_end = pending_end
            if _pend_end - seg_start >= 2:
                seg = _build_segment(bis, seg_start, _pend_end, direction)
                segments.append(seg)
                seg_start = _pend_end + 1
                i = seg_start + 2
                pending_end = None
                continue

        # 安全检查：seg_end 必须 >= seg_start + 2（至少3笔）
        if seg_end - seg_start < 2:
            i += 1
            continue

        # 构建线段
        seg = _build_segment(bis, seg_start, seg_end, direction)
        segments.append(seg)

        # 新线段从下一笔开始
        seg_start = seg_end + 1
        i = seg_start + 2  # 至少需要再积累3笔
        pending_end = None

    # 处理剩余的笔（最后不完整线段）
    if seg_start < len(bis) - 2:
        seg = _build_segment(bis, seg_start, len(bis) - 1, bis[seg_start].direction)
        segments.append(seg)

    # 后处理：合并同向线段（简化碎片）
    segments = _merge_same_direction_segments(segments)

    return segments


def _build_segment(bis, start_idx: int, end_idx: int, direction: str) -> Segment:
    """根据笔索引范围构建线段对象"""
    start_bi = bis[start_idx]
    end_bi = bis[end_idx]

    # 计算线段的高低点
    high_price = max(
        max(b.start_price, b.end_price) for b in bis[start_idx : end_idx + 1]
    )
    low_price = min(
        min(b.start_price, b.end_price) for b in bis[start_idx : end_idx + 1]
    )

    return Segment(
        start_date=start_bi.start_date,
        start_price=start_bi.start_price,
        end_date=end_bi.end_date,
        end_price=end_bi.end_price,
        direction=direction,
        bi_count=end_idx - start_idx + 1,
        bi_start_idx=start_idx,
        bi_end_idx=end_idx,
        high_price=high_price,
        low_price=low_price,
    )


def _merge_same_direction_segments(segments: List[Segment]) -> List[Segment]:
    """合并同向相邻线段，减少碎片"""
    if len(segments) < 2:
        return segments

    merged = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg.direction == prev.direction:
            # 同向合并：取更远的价格端点和更大的范围
            if seg.direction == "up":
                if seg.end_price > prev.end_price:
                    merged[-1] = Segment(
                        start_date=prev.start_date,
                        start_price=prev.start_price,
                        end_date=seg.end_date,
                        end_price=seg.end_price,
                        direction="up",
                        bi_count=prev.bi_count + seg.bi_count,
                        bi_start_idx=prev.bi_start_idx,
                        bi_end_idx=seg.bi_end_idx,
                        high_price=max(prev.high_price, seg.high_price),
                        low_price=min(prev.low_price, seg.low_price),
                    )
            else:
                if seg.end_price < prev.end_price:
                    merged[-1] = Segment(
                        start_date=prev.start_date,
                        start_price=prev.start_price,
                        end_date=seg.end_date,
                        end_price=seg.end_price,
                        direction="down",
                        bi_count=prev.bi_count + seg.bi_count,
                        bi_start_idx=prev.bi_start_idx,
                        bi_end_idx=seg.bi_end_idx,
                        high_price=max(prev.high_price, seg.high_price),
                        low_price=min(prev.low_price, seg.low_price),
                    )
        else:
            merged.append(seg)

    return merged


# ═══════════════════════════════════════════════════════════
# 第二部分：线段中枢构建 + 扩张
# ═══════════════════════════════════════════════════════════


def find_segment_zhongshus(
    segments: List[Segment], min_segments: int = 3
) -> List[SegmentZhongShu]:
    """线段中枢构建

    3个连续线段的重叠区间构成一个线段中枢。
    包含中枢延伸逻辑（后续线段仍在区间内则纳入）。

    Args:
        segments: 线段列表
        min_segments: 构成中枢的最小线段数（默认3）
    Returns:
        线段中枢列表
    """
    if len(segments) < min_segments:
        return []

    zhongshus = []
    i = 0

    while i <= len(segments) - 3:
        s1, s2, s3 = segments[i], segments[i + 1], segments[i + 2]

        # 计算3个线段的价格范围
        highs = [
            max(s.start_price, s.end_price) for s in [s1, s2, s3]
        ]
        lows = [
            min(s.start_price, s.end_price) for s in [s1, s2, s3]
        ]

        overlap_high = min(highs)
        overlap_low = max(lows)

        if overlap_high > overlap_low:
            zg = overlap_high
            zd = overlap_low
            end_idx = i + 3

            # 中枢延伸：后续线段仍在中枢区间内则纳入
            # ═══ 延伸保护（与扩张保护一致）═══
            while end_idx < len(segments):
                next_seg = segments[end_idx]
                next_high = max(next_seg.start_price, next_seg.end_price)
                next_low = min(next_seg.start_price, next_seg.end_price)
                
                # 条件：线段与中枢有重叠
                if not (next_high >= zd and next_low <= zg):
                    break
                
                # ── 保护1：段数超限（9段=27笔）──
                if (end_idx - i) >= 9:
                    break
                
                # ── 保护2：时间超限（120交易日）──
                try:
                    zs_start_dt = parse_date_to_datetime(s1.start_date)
                    seg_end_dt = parse_date_to_datetime(next_seg.end_date)
                    if (seg_end_dt - zs_start_dt).days >= MAX_ZHONGSHU_DAYS:
                        break
                except ValueError:
                    pass
                
                # ── 保护3：无效横盘（波动<5%持续）──
                if zg > 0 and zd > 0:
                    range_pct = (zg - zd) / zd
                    segs_so_far = segments[i:end_idx]
                    total_days = 0
                    try:
                        for s in segs_so_far:
                            sd = parse_date_to_datetime(s.start_date)
                            ed = parse_date_to_datetime(s.end_date)
                            total_days += (ed - sd).days
                    except ValueError:
                        total_days = 0
                    if range_pct < MIN_FLUCTUATION_PCT and total_days > 60:
                        break
                
                end_idx += 1

            # 计算 GG/DD：中枢区间内的实际最高/最低点
            zs_segments = segments[i:end_idx]
            gg = max(s.high_price for s in zs_segments)
            dd = min(s.low_price for s in zs_segments)

            # 确定中枢方向（v4.2 修复：不再用 s1/s2 方向 OR 判断）
            # 原实现 `s1.direction == "up" or s2.direction == "up"` 几乎恒为 up
            # （3个连续线段中至少一个向上的概率极高），导致 L606 扩张的
            # "同方向校验" 形同虚设。
            #
            # 正确做法（缠论原文）：中枢方向由"前后中枢位置关系"决定——
            #   后中枢 ZG > 前中枢 ZG → 上涨趋势 → up
            #   后中枢 ZD < 前中枢 ZD → 下跌趋势 → down
            #   既不高也不低 → 延续前中枢方向；第一个中枢 → 由首线段方向决定
            if zhongshus:
                prev_zs = zhongshus[-1]
                if zg > prev_zs.zg:
                    direction = "up"
                elif zd < prev_zs.zd:
                    direction = "down"
                else:
                    direction = prev_zs.direction  # 位置关系不明 → 延续
            else:
                # 第一个中枢：由构成中枢的第一个线段方向决定
                direction = s1.direction

            zs = SegmentZhongShu(
                start_date=s1.start_date,
                end_date=segments[end_idx - 1].end_date,
                zg=zg,
                zd=zd,
                gg=gg,
                dd=dd,
                segment_count=end_idx - i,
                direction=direction,
            )
            zhongshus.append(zs)
            i = end_idx
        else:
            i += 1

    return zhongshus


def _check_expansion(
    zs_a: SegmentZhongShu,
    zs_b: SegmentZhongShu,
    segments: List[Segment],
    bis,
    merged_klines,
) -> Tuple[bool, str, Optional[SegmentZhongShu]]:
    """检查两个线段中枢是否满足扩张条件

    三大校验：
    1. 同级别：段数相差不超过3倍
    2. 同方向：中枢方向一致
    3. 条件1（区间重叠）OR 条件2（波动触及）

    保护机制：
    - 笔数超限（>27笔）→ 拒绝
    - 时间超限（>120交易日）→ 拒绝
    - 无效横盘（波动<5%持续60日）→ 拒绝

    Returns:
        (是否扩张, 原因, 扩张后的中枢 or None)
    """
    # ── 同级别校验 ──
    ratio = max(zs_a.segment_count, zs_b.segment_count) / max(
        min(zs_a.segment_count, zs_b.segment_count), 1
    )
    if ratio > 3:
        return False, f"级别不同(段数比={ratio:.1f})", None

    # ── 同方向校验 ──
    if zs_a.direction != zs_b.direction:
        return False, f"方向不同({zs_a.direction} vs {zs_b.direction})", None

    # ── 条件1：区间重叠 ──
    overlap = (zs_b.zg > zs_a.zd) and (zs_b.zd < zs_a.zg)

    # ── 条件2：波动触及（b的任一段触及a的区间） ──
    touch = False
    if not overlap:
        # 找b相关的线段（通过日期范围）
        for seg in segments:
            seg_start = seg.start_date
            seg_end = seg.end_date
            seg_high = seg.high_price
            seg_low = seg.low_price
            # 如果线段在b中枢期间内
            if seg_start >= zs_b.start_date and seg_end <= zs_b.end_date:
                if seg_low <= zs_a.zg and seg_high >= zs_a.zd:
                    touch = True
                    break

    if not overlap and not touch:
        return False, "无扩张条件（不重叠、不触及）", None

    # ── 保护机制 ──
    # 1. 笔数检查（估算：按每段平均3笔）
    total_bi_est = (zs_a.segment_count + zs_b.segment_count) * 3
    if total_bi_est >= MAX_ZHONGSHU_BI:
        return False, f"笔数超限(≈{total_bi_est}>{MAX_ZHONGSHU_BI})，强制切分", None

    # 2. 时间检查
    try:
        start_dt = parse_date_to_datetime(zs_a.start_date)
        end_dt = parse_date_to_datetime(zs_b.end_date)
        days = (end_dt - start_dt).days
        if days >= MAX_ZHONGSHU_DAYS:
            return False, f"时间超限({days}天>{MAX_ZHONGSHU_DAYS})，重新初始化", None

        # 3. 无效横盘检查
        if zs_a.zd > 0:
            range_pct = (zs_a.gg - zs_a.dd) / zs_a.zd
            if range_pct < MIN_FLUCTUATION_PCT and days > 60:
                return False, f"无效横盘({range_pct:.1%}<{MIN_FLUCTUATION_PCT:.0%})", None
    except ValueError:
        pass

    # ── 扩张合并 ──
    new_zg = max(zs_a.zg, zs_b.zg)
    new_zd = min(zs_a.zd, zs_b.zd)
    new_gg = max(zs_a.gg, zs_b.gg)
    new_dd = min(zs_a.dd, zs_b.dd)

    expanded = SegmentZhongShu(
        start_date=zs_a.start_date,
        end_date=zs_b.end_date,
        zg=new_zg,
        zd=new_zd,
        gg=new_gg,
        dd=new_dd,
        segment_count=zs_a.segment_count + zs_b.segment_count,
        direction=zs_a.direction,
    )

    return True, f"扩张成功 ZG={new_zg:.2f} ZD={new_zd:.2f}", expanded


def apply_expansion(
    zhongshus: List[SegmentZhongShu],
    segments: List[Segment],
    bis,
    merged_klines,
    depth: int = 0,
) -> List[SegmentZhongShu]:
    """应用中枢扩张

    遍历相邻中枢对，检查扩张条件。
    扩张后原中枢消失，只保留合并后的大中枢。
    使用 while 循环因为扩张后可能需要再和下一个比较。

    v4.2: 增加递归深度限制（原实现无 depth 参数，极端数据下可能
    递归多轮，虽然 len(result)<len(zhongshus) 保证有限步）
    """
    MAX_EXPANSION_DEPTH = 10
    if depth >= MAX_EXPANSION_DEPTH:
        return zhongshus  # 深度保护：不再递归

    if len(zhongshus) < 2:
        return zhongshus

    result = []
    i = 0
    while i < len(zhongshus):
        if i == len(zhongshus) - 1:
            result.append(zhongshus[i])
            break

        current = zhongshus[i]
        next_zs = zhongshus[i + 1]

        can_expand, reason, expanded = _check_expansion(
            current, next_zs, segments, bis, merged_klines
        )

        if can_expand and expanded is not None:
            # 扩张：两个中枢合并为一个
            result.append(expanded)
            i += 2  # 跳过两个原中枢
        else:
            result.append(current)
            i += 1

    # 递归：新列表可能还能再扩张
    if len(result) < len(zhongshus):
        return apply_expansion(result, segments, bis, merged_klines, depth + 1)

    return result


# ═══════════════════════════════════════════════════════════
# 第三部分：线段级别买卖点识别
# ═══════════════════════════════════════════════════════════


def find_segment_buy_sell_points(
    segments: List[Segment],
    seg_zhongshus: List[SegmentZhongShu],
    macd_data,
    bis,
) -> List[SegmentBuySellPoint]:
    """线段级别买卖点识别

    逻辑与笔中枢一致，但操作对象是线段和线段中枢。

    一买（卖）：线段趋势背驰
        - 识别由线段构成的趋势（上/下）
        - 趋势末端的线段与进入趋势的线段做MACD面积背驰比较

    二买（卖）：线段回调/反弹确认
        - 一买后的第一次反向线段不创新低/高

    三买（卖）：线段突破中枢后回踩/反弹不进入
        - 线段向上突破ZG后，向下线段回踩不进入ZG → SB3

    Args:
        segments: 线段列表
        seg_zhongshus: 线段中枢列表
        macd_data: MACD 数据列表
        bis: 笔列表
    Returns:
        线段买卖点列表
    """
    points = []

    # ── 三类买点（SB3）：线段突破线段中枢ZG后回踩不进入 ──
    for zs in seg_zhongshus:
        # 找到中枢结束后的线段
        after_segs = [
            seg for seg in segments if seg.start_date >= zs.end_date
        ]

        # SB3：向上突破ZG后回调不进中枢
        found_up_break = False
        pullback_seg = None
        for seg in after_segs:
            if not found_up_break and seg.direction == "up":
                seg_high = max(seg.start_price, seg.end_price)
                if seg_high > zs.zg:
                    found_up_break = True
                    continue
            if found_up_break and seg.direction == "down":
                pullback_seg = seg
                pullback_low = min(seg.start_price, seg.end_price)
                if pullback_low <= zs.zg:
                    # 回调进入中枢，本次三买失败。
                    # v5.3.4(D2/审计P1-11): 原提前 break 会漏掉此后"重新突破
                    # +再次回踩不进中枢"的有效三买序列——重置状态继续扫描
                    # （仅影响 HTML 双视角 SB/SS 标签，不进选股主链）
                    found_up_break = False
                    pullback_seg = None
                continue
            if found_up_break and pullback_seg and seg.direction == "up":
                # 确认三买：突破后回调不进中枢，然后启动新一轮上涨
                points.append(
                    SegmentBuySellPoint(
                        type="buy",
                        level=3,
                        date=seg.start_date,
                        price=round(seg.start_price, 2),
                        reason=f"线段三买(SB3)：突破中枢[{date_to_str(zs.start_date)[-5:]}~{date_to_str(zs.end_date)[-5:]}]ZG={zs.zg:.1f}后回踩确认",
                    )
                )
                break

        # SB3（卖）：向下突破ZD后反弹不进中枢
        found_down_break = False
        rebound_seg = None
        for seg in after_segs:
            if not found_down_break and seg.direction == "down":
                seg_low = min(seg.start_price, seg.end_price)
                if seg_low < zs.zd:
                    found_down_break = True
                    continue
            if found_down_break and seg.direction == "up":
                rebound_seg = seg
                rebound_high = max(seg.start_price, seg.end_price)
                if rebound_high >= zs.zd:
                    # v5.3.4(D2/审计P1-11): 同 SB3——失败重置而非终止扫描
                    found_down_break = False
                    rebound_seg = None
                continue
            if found_down_break and rebound_seg and seg.direction == "down":
                points.append(
                    SegmentBuySellPoint(
                        type="sell",
                        level=3,
                        date=seg.start_date,
                        price=round(seg.start_price, 2),
                        reason=f"线段三卖(SS3)：跌破中枢[{date_to_str(zs.start_date)[-5:]}~{date_to_str(zs.end_date)[-5:]}]ZD={zs.zd:.1f}后反弹确认",
                    )
                )
                break

    # ── 一类买卖点（SB1/SS1）：线段趋势背驰 ──
    first_class = _find_segment_first_class_points(segments, seg_zhongshus, macd_data)
    points.extend(first_class)

    # ── 二类买卖点（SB2/SS2）：线段回调确认 ──
    second_class = _find_segment_second_class_points(segments, first_class)
    points.extend(second_class)

    # 按日期排序
    points.sort(key=lambda p: p.date)
    return points


def _find_segment_first_class_points(
    segments: List[Segment],
    seg_zhongshus: List[SegmentZhongShu],
    macd_data,
) -> List[SegmentBuySellPoint]:
    """线段趋势背驰识别（一类买卖点）

    v4.2 修复（缠论62课）：
    趋势背驰比较的是"趋势第一个中枢的进入段" vs "趋势最后一个中枢的
    离开段"，而非逐对相邻中枢比较。

    原实现 `for j in range(1, len): zs_prev/zs_curr` 只比较相邻中枢对：
    - 2个中枢时恰好等价于首尾比较（常见情况，行为不变）
    - ≥3个同向中枢时，会误把中途的次级别背驰当作趋势终结信号

    修复：先识别完整趋势（连续同向中枢序列），再对每个趋势做首尾比较。
    背驰判断用线段对应的MACD柱状图面积比较。
    """
    points = []
    if len(seg_zhongshus) < 2:
        return points

    # ═══ v4.2 识别完整趋势（连续同向中枢序列）═══
    # 上涨趋势：后中枢 ZG > 前中枢 ZG（中枢整体上移）
    # 下跌趋势：后中枢 ZD < 前中枢 ZD（中枢整体下移）
    trends = []  # [(direction, [zs_idx...]), ...]

    i = 0
    while i < len(seg_zhongshus) - 1:
        zs1 = seg_zhongshus[i]
        zs2 = seg_zhongshus[i + 1]
        if zs2.zg > zs1.zg:
            # 上涨趋势开始
            trend = ['up', [i, i + 1]]
            j = i + 1
            while j + 1 < len(seg_zhongshus):
                if seg_zhongshus[j + 1].zg > seg_zhongshus[j].zg:
                    trend[1].append(j + 1)
                    j += 1
                else:
                    break
            trends.append(trend)
            i = j
        elif zs2.zd < zs1.zd:
            # 下跌趋势开始
            trend = ['down', [i, i + 1]]
            j = i + 1
            while j + 1 < len(seg_zhongshus):
                if seg_zhongshus[j + 1].zd < seg_zhongshus[j].zd:
                    trend[1].append(j + 1)
                    j += 1
                else:
                    break
            trends.append(trend)
            i = j
        else:
            i += 1

    # 对每个趋势做首尾中枢比较（缠论62课）
    for direction, zs_indices in trends:
        if len(zs_indices) < 2:
            continue

        first_zs = seg_zhongshus[zs_indices[0]]    # 趋势第一个中枢
        last_zs = seg_zhongshus[zs_indices[-1]]   # 趋势最后一个中枢

        if direction == 'up':
            # 上涨趋势顶背驰 → 一卖(SS1)
            # 进入段 = 趋势第一个中枢之前的向上线段
            # 离开段 = 趋势最后一个中枢之后的向上线段
            enter_seg = _find_seg_before_zs(segments, first_zs, direction='up')
            exit_seg = _find_seg_after_zs(segments, last_zs, direction='up')
            if enter_seg and exit_seg:
                enter_area = _calc_segment_macd_area_approx(enter_seg, macd_data)
                exit_area = _calc_segment_macd_area_approx(exit_seg, macd_data)
                if exit_area < enter_area * THRESHOLD_SEGMENT_BEICHI and enter_area > 0:
                    points.append(
                        SegmentBuySellPoint(
                            type="sell",
                            level=1,
                            date=exit_seg.end_date,
                            price=round(exit_seg.end_price, 2),
                            reason=f"线段一卖(SS1)：顶背驰，离开段面积({exit_area:.0f})<进入段({enter_area:.0f})",
                        )
                    )
        else:
            # 下跌趋势底背驰 → 一买(SB1)
            enter_seg = _find_seg_before_zs(segments, first_zs, direction='down')
            exit_seg = _find_seg_after_zs(segments, last_zs, direction='down')
            if enter_seg and exit_seg:
                enter_area = _calc_segment_macd_area_approx(enter_seg, macd_data)
                exit_area = _calc_segment_macd_area_approx(exit_seg, macd_data)
                if exit_area < enter_area * THRESHOLD_SEGMENT_BEICHI and enter_area > 0:
                    points.append(
                        SegmentBuySellPoint(
                            type="buy",
                            level=1,
                            date=exit_seg.end_date,
                            price=round(exit_seg.end_price, 2),
                            reason=f"线段一买(SB1)：底背驰，离开段面积({exit_area:.0f})<进入段({enter_area:.0f})",
                        )
                    )

    return points


def _find_segment_second_class_points(
    segments: List[Segment],
    first_class_points: List[SegmentBuySellPoint],
) -> List[SegmentBuySellPoint]:
    """线段二类买卖点：一类买卖点后的第一次反向线段

    二买条件：
    - 一买后第一次向下线段的终点不低于一买价格（或在一买价格*0.99以上）
    - 取该向下线段结束后的第一个向上线段起点

    二卖条件类似。
    """
    points = []
    TOLERANCE = THRESHOLD_SEGMENT_SECOND_BUY_TOLERANCE  # 二买容差（可配置）

    for fp in first_class_points:
        # 找到一类买卖点之后的线段
        after_segs = [
            seg
            for seg in segments
            if seg.start_date > fp.date
        ]
        if len(after_segs) < 2:
            continue

        if fp.type == "buy":
            # 二买：一买后的第一次回调
            for j, seg in enumerate(after_segs):
                if seg.direction == "down":
                    if seg.end_price >= fp.price * (1 - TOLERANCE):
                        # 不创新低，下一个向上线段起点为二买
                        if j + 1 < len(after_segs):
                            next_up = after_segs[j + 1]
                            if next_up.direction == "up":
                                points.append(
                                    SegmentBuySellPoint(
                                        type="buy",
                                        level=2,
                                        date=next_up.start_date,
                                        price=round(next_up.start_price, 2),
                                        reason=f"线段二买(SB2)：回调不创新低(¥{seg.end_price:.2f}≥¥{fp.price:.2f})",
                                    )
                                )
                    break  # 只看第一次回调

        elif fp.type == "sell":
            for j, seg in enumerate(after_segs):
                if seg.direction == "up":
                    if seg.end_price <= fp.price * (1 + TOLERANCE):
                        if j + 1 < len(after_segs):
                            next_down = after_segs[j + 1]
                            if next_down.direction == "down":
                                points.append(
                                    SegmentBuySellPoint(
                                        type="sell",
                                        level=2,
                                        date=next_down.start_date,
                                        price=round(next_down.start_price, 2),
                                        reason=f"线段二卖(SS2)：反弹不创新高(¥{seg.end_price:.2f}≤¥{fp.price:.2f})",
                                    )
                                )
                    break

    return points


def _find_seg_before_zs(
    segments: List[Segment], zs: SegmentZhongShu, direction: str = None
) -> Optional[Segment]:
    """找到中枢之前的最后一个同向线段（按方向过滤）"""
    candidates = [
        seg
        for seg in segments
        if seg.end_date < zs.start_date
    ]
    if not candidates:
        return None
    # 如果指定方向，从后往前找第一个匹配方向的线段
    if direction:
        for seg in reversed(candidates):
            if seg.direction == direction:
                return seg
        return None
    return candidates[-1]


def _find_seg_after_zs(
    segments: List[Segment], zs: SegmentZhongShu, direction: str = None
) -> Optional[Segment]:
    """找到中枢之后的第一个同向线段（按方向过滤）"""
    candidates = [
        seg
        for seg in segments
        if seg.start_date >= zs.end_date
    ]
    if not candidates:
        return None
    # 如果指定方向，从前向后找第一个匹配方向的线段
    if direction:
        for seg in candidates:
            if seg.direction == direction:
                return seg
        return None
    return candidates[0]


def _calc_segment_macd_area_approx(
    seg: Segment, macd_data
) -> float:
    """计算线段对应的MACD柱状图近似面积

    v5.4(A-7): 与笔级 v4.2 口径统一——按线段方向分色累加(上涨段累红柱
    绝对值、下跌段累绿柱绝对值), 不再把反向柱计入动能。旧 abs() 全色
    累加会高估动能、模糊背驰, 导致线段级 SB1/SS1 系统性偏难触发
    (generate_analysis L446 笔级注释同一结论)。仅用于 HTML 双视角标签,
    不进选股主链。
    """
    area = 0.0
    _up = (seg.direction == 'up')
    for md in macd_data:
        if seg.start_date <= md.date <= seg.end_date:
            if _up and md.macd > 0:
                area += md.macd
            elif not _up and md.macd < 0:
                area += -md.macd
    return area


# ═══════════════════════════════════════════════════════════
# 第四部分：主分析器
# ═══════════════════════════════════════════════════════════


class SegmentChanLunAnalyzer:
    """线段中枢缠论分析器

    复用 ChanLunAnalyzer 的 K线包含处理、笔划分、MACD 计算，
    在线段层面进行中枢构建、扩张和买卖点识别。

    用法：
        from segment_analyzer import SegmentChanLunAnalyzer
        from generate_analysis import ChanLunAnalyzer

        # 1. 先跑笔级别分析
        bi_analyzer = ChanLunAnalyzer().analyze(klines_data)

        # 2. 跑线段级别分析
        seg_analyzer = SegmentChanLunAnalyzer()
        result = seg_analyzer.analyze(bi_analyzer)
    """

    def __init__(self):
        self.segments: List[Segment] = []
        self.seg_zhongshus: List[SegmentZhongShu] = []
        self.seg_buy_sell_points: List[SegmentBuySellPoint] = []
        self.bi_analyzer = None  # 引用笔分析器

    def analyze(
        self, bi_analyzer, merged_klines=None
    ) -> "SegmentChanLunAnalyzer":
        """执行完整的线段级别缠论分析

        Args:
            bi_analyzer: 已完成笔分析的 ChanLunAnalyzer 实例
            merged_klines: 合并K线列表（可选，用于扩张保护的时间计算）
        Returns:
            self
        """
        self.bi_analyzer = bi_analyzer

        if not bi_analyzer.bis or len(bi_analyzer.bis) < 3:
            return self

        # Step 1: 完整线段划分
        self.segments = find_segments(bi_analyzer.bis)

        if not self.segments:
            return self

        # Step 2: 线段中枢构建
        self.seg_zhongshus = find_segment_zhongshus(self.segments)

        # Step 3: 中枢扩张
        if self.seg_zhongshus:
            self.seg_zhongshus = apply_expansion(
                self.seg_zhongshus,
                self.segments,
                bi_analyzer.bis,
                merged_klines or bi_analyzer.merged_klines,
            )

        # Step 4: 线段级别买卖点
        self.seg_buy_sell_points = find_segment_buy_sell_points(
            self.segments,
            self.seg_zhongshus,
            bi_analyzer.macd_data,
            bi_analyzer.bis,
        )

        return self

    def to_dict(self) -> dict:
        """导出为字典，方便 JSON 序列化"""
        return {
            "segments": [
                {
                    "start_date": s.start_date,
                    "start_price": round(s.start_price, 2),
                    "end_date": s.end_date,
                    "end_price": round(s.end_price, 2),
                    "direction": s.direction,
                    "bi_count": s.bi_count,
                    "high_price": round(s.high_price, 2),
                    "low_price": round(s.low_price, 2),
                }
                for s in self.segments
            ],
            "zhongshus": [
                {
                    "start_date": z.start_date,
                    "end_date": z.end_date,
                    "zg": round(z.zg, 2),
                    "zd": round(z.zd, 2),
                    "gg": round(z.gg, 2),
                    "dd": round(z.dd, 2),
                    "segment_count": z.segment_count,
                    "direction": z.direction,
                }
                for z in self.seg_zhongshus
            ],
            "buy_sell_points": [
                {
                    "type": p.type,
                    "level": p.level,
                    "date": p.date,
                    "price": p.price,
                    "reason": p.reason,
                }
                for p in self.seg_buy_sell_points
            ],
        }

    def print_summary(self):
        """终端打印分析摘要"""
        print(f"\n{'='*60}")
        print(f"  线段中枢缠论分析摘要")
        print(f"{'='*60}")
        print(f"  线段数: {len(self.segments)}")
        print(f"  线段中枢: {len(self.seg_zhongshus)}")
        print(f"  线段买卖点: {len(self.seg_buy_sell_points)}")

        if self.seg_zhongshus:
            print(f"\n  ── 线段中枢明细 ──")
            for i, zs in enumerate(self.seg_zhongshus):
                width_pct = (
                    (zs.zg - zs.zd) / zs.zd * 100 if zs.zd > 0 else 0
                )
                print(
                    f"  [{i+1}] {zs.start_date} ~ {zs.end_date}  "
                    f"ZG={zs.zg:.2f} ZD={zs.zd:.2f} "
                    f"GG={zs.gg:.2f} DD={zs.dd:.2f} "
                    f"宽度={width_pct:.1f}%  段数={zs.segment_count}  "
                    f"方向={zs.direction}"
                )

        if self.seg_buy_sell_points:
            print(f"\n  ── 线段买卖点 ──")
            for p in self.seg_buy_sell_points:
                tag = (
                    f"SB{p.level}"
                    if p.type == "buy"
                    else f"SS{p.level}"
                )
                print(
                    f"  [{tag}] {p.date}  ¥{p.price:.2f}  {p.reason}"
                )

        print()


# ═══════════════════════════════════════════════════════════
# 命令行入口（测试用）
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 测试：海康威视 002415
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from data_manager import DataManager
    from generate_analysis import ChanLunAnalyzer

    symbol = sys.argv[1] if len(sys.argv) > 1 else "002415"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 500

    print(f"获取 {symbol} 最近 {days} 天数据...")
    dm = DataManager()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )
    df = dm.get_klines(
        symbol, level="daily", start_date=start_date, end_date=end_date
    )

    if df is None or df.empty:
        print(f"❌ 无法获取 {symbol} 数据")
        sys.exit(1)

    klines = dm.to_json_list(df)
    print(f"✅ {len(klines)} 根K线")

    # 笔级别分析
    print(f"\n[1] 笔级别缠论分析...")
    bi_analyzer = ChanLunAnalyzer().analyze(klines)
    print(
        f"  分型={len(bi_analyzer.fenxings)}, 笔={len(bi_analyzer.bis)}, "
        f"笔中枢={len(bi_analyzer.zhongshus)}, "
        f"买卖点={len(bi_analyzer.buy_sell_points)}"
    )

    # 线段级别分析
    print(f"\n[2] 线段级别缠论分析...")
    seg_analyzer = SegmentChanLunAnalyzer()
    seg_analyzer.analyze(bi_analyzer)

    # 对比
    print(f"\n{'='*60}")
    print(f"  笔中枢 vs 线段中枢 对比")
    print(f"{'='*60}")
    print(f"  笔中枢数量:    {len(bi_analyzer.zhongshus)}")
    print(f"  线段中枢数量:  {len(seg_analyzer.seg_zhongshus)}")
    print(f"  笔买卖点:      {len(bi_analyzer.buy_sell_points)}")
    print(f"  线段买卖点:    {len(seg_analyzer.seg_buy_sell_points)}")

    # 线段详情
    print(f"\n{'─'*60}")
    print(f"  线段明细")
    print(f"{'─'*60}")
    for i, seg in enumerate(seg_analyzer.segments):
        arrow = "↑" if seg.direction == "up" else "↓"
        print(
            f"  [{i+1:2d}] {arrow} {seg.start_date}({seg.start_price:.2f}) → "
            f"{seg.end_date}({seg.end_price:.2f})  "
            f"笔数={seg.bi_count} 波幅={seg.high_price:.2f}~{seg.low_price:.2f}"
        )

    seg_analyzer.print_summary()
