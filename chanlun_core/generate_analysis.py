"""
缠论多级别分析库 - 完整版
支持多级别递归分析 + HTML 可视化报告生成
"""

import json
from date_utils import date_to_str, parse_date_to_datetime
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict
from datetime import datetime, timedelta

@dataclass
class KLine:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int

@dataclass
class FenXing:
    type: str
    index: int
    date: str
    price: float
    kline_index: int

@dataclass
class Bi:
    start_date: str
    start_price: float
    end_date: str
    end_price: float
    direction: str
    # v5.4(A-1): 端点确认标志——False 表示末笔被 _extend_last_bi 延伸过,
    # end_date/end_price 落在无分型确认的合并K线上(存在重绘可能)。
    # 下游需要"结构确认点"时应回溯分型, 消费示例见二买卖扫描。
    confirmed_end: bool = True

@dataclass
class ZhongShu:
    start_date: str
    end_date: str
    zg: float
    zd: float
    bi_count: int

@dataclass
class BuySellPoint:
    type: str
    level: int
    date: str
    price: float
    reason: str
    confirmed: bool = True  # v3.5.5: 一买是否有向上一笔确认（False=潜在一买）
    # v5.3.1(M3): 三类买卖点的参照中枢边界——评分/惩罚应对照"被突破(跌破)的
    # 中枢", 而非 zhongshus[-1](三买后形成新中枢时会错位, 2026-08-22 审计:
    # 候选池 19 只三买 15 只错位)。None = 未记录(旧数据兼容, 回退旧逻辑)
    ref_zg: float = None
    ref_zd: float = None

@dataclass
class MACDData:
    date: str
    dif: float
    dea: float
    macd: float

class ChanLunAnalyzer:
    def __init__(self, level='daily', min_bi_klines=5, divergence_threshold=None, second_class_tolerance=None,
                 enable_forward_validation=True):
        self.level = level
        self.min_bi_klines = min_bi_klines
        # v4.2: 默认值从 config 读取（原硬编码 0.7/0.01）
        if divergence_threshold is None:
            from config_loader import THRESHOLD_DIVERGENCE
            divergence_threshold = THRESHOLD_DIVERGENCE
        if second_class_tolerance is None:
            from config_loader import THRESHOLD_SECOND_CLASS_TOLERANCE
            second_class_tolerance = THRESHOLD_SECOND_CLASS_TOLERANCE
        self.divergence_threshold = divergence_threshold  # 背驰阈值：离开段面积 < 进入段面积 * 阈值（至少衰减30%才算背驰）
        self.second_class_tolerance = second_class_tolerance  # 二类买卖点容差（如0.01=1%）
        # Forward Validation（v4.1）：一卖后365日内创新高则一卖无效（事后标注用）
        # ⚠️ 回测模式必须传 enable_forward_validation=False：
        #    回测遍历时"未来365日"是未知的，启用会导致信号集被未来数据清洗（前视偏差）
        self.enable_forward_validation = enable_forward_validation
        self.klines = []
        self.merged_klines = []
        self.fenxings = []
        self.bis = []
        self.zhongshus = []
        self.buy_sell_points = []
        self.macd_data = []
    
    def analyze(self, klines_data: List[dict]) -> 'ChanLunAnalyzer':
        self.klines = [KLine(**k) for k in klines_data]
        self.merged_klines = self._merge_klines(self.klines)
        self.fenxings = self._find_fenxings(self.merged_klines)
        self.bis = self._find_bis(self.fenxings, self.merged_klines)
        self._extend_last_bi(self.merged_klines)
        self.zhongshus = self._find_zhongshus(self.bis)
        self.macd_data = self._calculate_macd(self.klines)
        self.buy_sell_points = self._find_buy_sell_points()
        return self
    
    @staticmethod
    def calibrate_prices(analyzer, actual_latest_price, reference_price, caller_tag=""):
        """统一价格校准：当实际复权价偏离参考价超过10%时，等比缩放所有价格
        
        Args:
            analyzer: ChanLunAnalyzer 实例（已分析完成）
            actual_latest_price: 数据源最新收盘价
            reference_price: 用户提供的参考价（如市价）
            caller_tag: 调用方标识（用于日志）
        Returns:
            scale_factor (1.0 表示未校准)
        """
        if not reference_price or actual_latest_price <= 0:
            return 1.0
        if abs(actual_latest_price - reference_price) > reference_price * 0.1:
            scale_factor = reference_price / actual_latest_price
            label = f"[{caller_tag}] " if caller_tag else ""
            print(f"{label}价格校准: {actual_latest_price:.2f} → {reference_price:.2f} (x{scale_factor:.4f})")
            # 缩放原始K线和合并K线（图表数据源）
            for k in analyzer.klines:
                k.open *= scale_factor
                k.high *= scale_factor
                k.low *= scale_factor
                k.close *= scale_factor
            for mk in analyzer.merged_klines:
                mk['high'] *= scale_factor
                mk['low'] *= scale_factor
            # 缩放分型/笔/中枢/买卖点
            for fx in analyzer.fenxings:
                fx.price *= scale_factor
            for bi in analyzer.bis:
                bi.start_price *= scale_factor
                bi.end_price *= scale_factor
            for zs in analyzer.zhongshus:
                zs.zg *= scale_factor
                zs.zd *= scale_factor
            for bp in analyzer.buy_sell_points:
                bp.price *= scale_factor
                # v5.4(A-2): 参考中枢字段与价格同缩放——M3新增的 ref_zg/ref_zd
                # 若不同步, 校准后破位惩罚锚定错位。当前主链调用方 factor 恒为1
                # (休眠), 此为防御性修复; 注意 None 判断(非三买卖点无此字段)。
                if getattr(bp, 'ref_zg', None) is not None:
                    bp.ref_zg *= scale_factor
                if getattr(bp, 'ref_zd', None) is not None:
                    bp.ref_zd *= scale_factor
            return scale_factor
        return 1.0
    
    def _merge_klines(self, klines):
        if not klines: return []
        merged = [{'high': klines[0].high, 'low': klines[0].low, 'index': 0, 'date': klines[0].date, 'original_count': 1}]
        direction = 0  # 0=未确定，1=向上，-1=向下
        ref_index = 0  # 最近的无包含关系的K线索引
        
        for i in range(1, len(klines)):
            curr = klines[i]
            prev = merged[-1]
            
            # 检查包含关系
            is_contain = (curr.high <= prev['high'] and curr.low >= prev['low']) or \
                         (curr.high >= prev['high'] and curr.low <= prev['low'])
            
            if is_contain:
                # 需要确定方向
                if direction == 0:
                    # 向前查找最近的无包含关系的K线作为参考
                    # v3.5.4 修复：使用合并K线而非原始K线做方向参考
                    ref_kline = merged[ref_index]
                    # 比较当前K线与参考K线的高点
                    if curr.high > ref_kline['high']:
                        direction = 1  # 向上
                    else:
                        direction = -1  # 向下
                
                # 根据方向合并
                if direction > 0:  # 向上处理
                    prev['high'] = max(prev['high'], curr.high)
                    prev['low'] = max(prev['low'], curr.low)
                    prev['original_count'] += 1
                else:  # 向下处理
                    prev['high'] = min(prev['high'], curr.high)
                    prev['low'] = min(prev['low'], curr.low)
                    prev['original_count'] += 1
            else:
                # 无包含关系，更新方向和参考索引
                if curr.high > prev['high']:
                    direction = 1  # 向上
                else:
                    direction = -1  # 向下
                ref_index = i
                merged.append({'high': curr.high, 'low': curr.low, 'index': i, 'date': curr.date, 'original_count': 1})
        
        return merged
    
    def _find_fenxings(self, merged):
        fenxings = []
        for i in range(1, len(merged) - 1):
            prev, curr, next_k = merged[i-1], merged[i], merged[i+1]
            # 顶分型：中间K线的高点最高（标准缠论定义）
            if curr['high'] > prev['high'] and curr['high'] > next_k['high']:
                fenxings.append(FenXing(type='top', index=curr['index'], date=curr['date'], price=curr['high'], kline_index=i))
            # 底分型：中间K线的低点最低（标准缠论定义）
            elif curr['low'] < prev['low'] and curr['low'] < next_k['low']:
                fenxings.append(FenXing(type='bottom', index=curr['index'], date=curr['date'], price=curr['low'], kline_index=i))
        return fenxings
    
    def _filter_extreme_fenxings(self, fenxings):
        """缠论原文划分步骤2：同性质分型保留极值

        连续顶分型只保留最高点的那个，连续底分型只保留最低点的那个
        （《教你炒股票62课》划分笔的步骤2）。

        Returns:
            过滤后严格交替的分型序列（顶底顶底...）
        """
        if not fenxings:
            return []
        filtered = []
        for fx in fenxings:
            if not filtered:
                filtered.append(fx)
                continue
            last = filtered[-1]
            if fx.type == last.type:
                # 同性质：保留更极端的
                if (fx.type == 'top' and fx.price > last.price) or (fx.type == 'bottom' and fx.price < last.price):
                    filtered[-1] = fx
            else:
                filtered.append(fx)
        return filtered

    def _is_valid_bi_pair(self, bottom_fx, top_fx, merged):
        """缠论严格笔（老笔）有效性校验（原文62课 + 补充定义）

        三项校验：
        1. 硬性条件A：整笔无包含K线数 ≥ min_bi_klines（默认5根）
        2. 硬性条件B：顶底分型之间至少1根独立K线
           （不属于顶分型也不属于底分型的K线）
        3. 有效性校验：顶分型最高K线的区间至少有一部分高于
           底分型最低K线的区间；若顶完全落在底分型范围内则不构成有效笔

        Args:
            bottom_fx: 底分型
            top_fx: 顶分型
            merged: 合并后K线列表（含high/low）
        Returns:
            bool
        """
        start_idx = min(bottom_fx.kline_index, top_fx.kline_index)
        end_idx = max(bottom_fx.kline_index, top_fx.kline_index)
        kline_count = end_idx - start_idx + 1  # 整笔合并后K线数（含两端分型中K线）

        # 硬性条件A：整笔无包含K线数 ≥ 5（老笔标准）
        if kline_count < self.min_bi_klines:
            return False

        # 硬性条件B：顶底之间至少1根独立K线
        # 整笔 = 底分型中K线 + 底右K线 + 独立K线 + 顶左K线 + 顶分型中K线
        # 独立K线数 = kline_count - 4（扣除两个分型的中K线和边界K线）
        independent = kline_count - 4
        if independent < 1:
            return False

        # 有效性校验（原文补充定义）：
        # 顶分型最高K线 = merged[top_fx.kline_index]（分型中K线即最高K线）
        # 底分型最低K线 = merged[bottom_fx.kline_index]
        top_k = merged[top_fx.kline_index]
        bottom_k = merged[bottom_fx.kline_index]
        top_high = top_k['high']
        top_low = top_k['low']
        bottom_high = bottom_k['high']
        bottom_low = bottom_k['low']

        # 顶完全落在底分型范围内（顶K线区间被底K线区间完全包含）→ 无效
        if top_low >= bottom_low and top_high <= bottom_high:
            return False
        # 顶的最高点 ≤ 底的最低点 → 没有一部分高于底 → 无效
        if top_high <= bottom_low:
            return False

        return True

    def _find_bis(self, fenxings, merged):
        """缠论严格笔识别（v4.2 按原文62课重构）

        步骤（缠论原文划分笔的步骤）：
        1. 同性质分型保留极值（连续顶保留最高、连续底保留最低）
        2. 剩余分型，相邻顶底连接成笔，逐笔校验：
           a. 整笔无包含K线数 ≥ min_bi_klines（默认5）
           b. 顶底之间至少1根独立K线
           c. 有效性：顶分型最高K线区间部分高于底分型最低K线区间
        3. 中继分型忽略原则：不满足条件的相邻分型跳过（中继），
           继续向后找第一个满足条件的异性质分型成笔
        """
        filtered = self._filter_extreme_fenxings(fenxings)
        bis = []
        i = 0
        n = len(filtered)
        while i < n:
            start_fx = filtered[i]
            found = None
            found_j = None
            for j in range(i + 1, n):
                fx = filtered[j]
                if fx.type == start_fx.type:
                    # 同性质更极端 → 笔延伸（缠论：新笔未产生，原笔延伸）
                    # 例：01-29顶 → 02-19更高顶，中间无有效底分型 → 原 up 笔终点延伸到 02-19
                    if (fx.type == 'top' and fx.price > start_fx.price) or \
                       (fx.type == 'bottom' and fx.price < start_fx.price):
                        # 若上一笔终点就是当前起点分型 → 同步延伸上一笔
                        if bis and str(bis[-1].end_date) == str(start_fx.date):
                            bis[-1].end_date = fx.date
                            bis[-1].end_price = fx.price
                        start_fx = fx
                        i = j
                    continue
                # 异性质：校验是否能成笔
                if start_fx.type == 'bottom':
                    valid = self._is_valid_bi_pair(start_fx, fx, merged)
                else:
                    valid = self._is_valid_bi_pair(fx, start_fx, merged)
                if valid:
                    found = fx
                    found_j = j
                    break
                # 不满足：跳过该分型（中继），继续向后找
            if found is None:
                break
            direction = 'up' if start_fx.type == 'bottom' else 'down'
            bis.append(Bi(start_date=start_fx.date, start_price=start_fx.price,
                          end_date=found.date, end_price=found.price, direction=direction))
            i = found_j
        return bis
    
    def _extend_last_bi(self, merged):
        """末笔延伸：后续合并K线创新极值时，同步延伸末笔的日期与价格。

        v5.4(A-1) docstring 现行化——v3.5.4 时代的"只延伸价格不延伸日期"已
        被取代：只延价格会造成端点价格与日期错位的时空矛盾(价格来自新K线,
        日期停在旧分型), 现行行为为日期+价格双延伸。代价是延伸后的端点落在
        无分型确认的合并K线上(缠论62课笔端点须分型确认), 存在重绘可能——
        通过 Bi.confirmed_end=False 显式标记, 下游(二买卖扫描的分型回溯)
        据此回溯到真正的结构确认点, 不再依赖散落的启发式判断。

        下跌笔：后续出现更低低点 → 同步更新结束日期与价格
        上涨笔：后续出现更高高点 → 同步更新结束日期与价格
        """
        if not self.bis:
            return

        last_bi = self.bis[-1]
        end_pos = None
        for i, k in enumerate(merged):
            if k['date'] == last_bi.end_date:
                end_pos = i
                break

        if end_pos is None or end_pos >= len(merged) - 1:
            return  # 端点已是最新K线或找不到

        if last_bi.direction == 'down':
            new_low, new_date = last_bi.end_price, last_bi.end_date
            for k in merged[end_pos + 1:]:
                if k['low'] < new_low:
                    new_low, new_date = k['low'], k['date']
            if new_low < last_bi.end_price:
                # v5.4(A-4): ASCII安全日志——去除 ¥/↓ 等GBK控制台无法编码的字符,
                # 且不再带前导空格(旧monkey-patch白名单按'['开头识别会漏掉)
                print(f"[BiExtend-DN] last_bi extended: {last_bi.end_date}({last_bi.end_price:.2f}) -> {new_date}({new_low:.2f})")
                # 同步延伸日期和价格（消除原本"只延伸价格"导致的时空错配）
                last_bi.end_date = new_date
                last_bi.end_price = new_low
                last_bi.confirmed_end = False  # v5.4(A-1): 标记未确认端点

        elif last_bi.direction == 'up':
            new_high, new_date = last_bi.end_price, last_bi.end_date
            for k in merged[end_pos + 1:]:
                if k['high'] > new_high:
                    new_high, new_date = k['high'], k['date']
            if new_high > last_bi.end_price:
                print(f"[BiExtend-UP] last_bi extended: {last_bi.end_date}({last_bi.end_price:.2f}) -> {new_date}({new_high:.2f})")
                # 同步延伸日期和价格
                last_bi.end_date = new_date
                last_bi.end_price = new_high
                last_bi.confirmed_end = False  # v5.4(A-1): 标记未确认端点
    
    def _find_zhongshus(self, bis):
        if len(bis) < 3: return []
        zhongshus = []
        i = 0
        while i < len(bis) - 2:
            bi1, bi2, bi3 = bis[i], bis[i+1], bis[i+2]
            high1, low1 = max(bi1.start_price, bi1.end_price), min(bi1.start_price, bi1.end_price)
            high2, low2 = max(bi2.start_price, bi2.end_price), min(bi2.start_price, bi2.end_price)
            high3, low3 = max(bi3.start_price, bi3.end_price), min(bi3.start_price, bi3.end_price)
            overlap_high, overlap_low = min(high1, high2, high3), max(low1, low2, low3)
            if overlap_high > overlap_low:
                end_idx = i + 3
                zg, zd = overlap_high, overlap_low
                while end_idx < len(bis):
                    next_bi = bis[end_idx]
                    next_high, next_low = max(next_bi.start_price, next_bi.end_price), min(next_bi.start_price, next_bi.end_price)
                    # v5.3.1(M7): 穿透笔(高≥ZG且低≤ZD)不再直接判中枢结束——
                    # 第18课: 次级别走势"离开后返回"仍属中枢延伸, 宽幅震荡笔
                    # 穿透后返回的, 中枢应继续; 仅当穿透后下一笔不再回到
                    # [ZD,ZG] 区间(真离开), 中枢才在此结束。
                    if next_high >= zg and next_low <= zd:
                        if end_idx + 1 < len(bis):
                            _after = bis[end_idx + 1]
                            _a_high = max(_after.start_price, _after.end_price)
                            _a_low = min(_after.start_price, _after.end_price)
                            if _a_high >= zd and _a_low <= zg:
                                end_idx += 2  # 穿透+返回均属延伸
                                continue
                        break  # 穿透后无返回(或已到最后一笔) → 真离开段
                    if next_high >= zd and next_low <= zg:
                        end_idx += 1
                    else: break
                
                # === 稳定性过滤 ===
                start_d = bi1.start_date
                end_d = bis[end_idx-1].end_date
                
                # 1. 原始K线数检查：中枢区间内至少5根合并K线
                merged_count = sum(1 for k in self.merged_klines
                                   if start_d <= k['date'] <= end_d)
                if merged_count < 5:
                    i += 1
                    continue
                
                # 2. 中枢区间幅度检查 — 已删除
                #    缠论中枢无宽度限制，此过滤器会误删正常中枢
                #    （如传音控股2025年4~8月宽度11.3%被错误过滤）
                
                zhongshus.append(ZhongShu(start_date=start_d, end_date=end_d,
                                          zg=zg, zd=zd, bi_count=end_idx-i))
                i = end_idx
            else: i += 1
        return zhongshus

    def _calculate_macd(self, klines, fast=12, slow=26, signal=9):
        closes = [k.close for k in klines]
        def calc_ema(data, period):
            if not data: return []
            ema = [data[0]]
            m = 2/(period+1)
            for i in range(1, len(data)): ema.append((data[i]-ema[-1])*m + ema[-1])
            return ema
        ema_f, ema_s = calc_ema(closes, fast), calc_ema(closes, slow)
        dif = [ema_f[i]-ema_s[i] for i in range(len(closes))]
        dea = calc_ema(dif, signal)
        macd = [(dif[i]-dea[i])*2 for i in range(len(dif))]
        return [MACDData(date=klines[i].date, dif=round(dif[i],3), dea=round(dea[i],3), macd=round(macd[i],3)) for i in range(len(klines))]

    def _calculate_macd_area_for_bi(self, bi, macd_data):
        """计算笔对应的MACD柱状图面积（v4.2 按笔方向分色累加）

        缠论原文：背驰比较的是"同方向动能"的衰减——
          上涨笔（离开段）看红柱面积（MACD>0）
          下跌笔（离开段）看绿柱面积（MACD<0）
        原实现 abs() 混合红绿柱，会把笔内反向波动（如上涨笔中的回调
        绿柱）也计入面积，高估动能、模糊背驰信号。

        因此改为：按笔方向只累加同色柱面积。
        """
        # 找到笔开始和结束日期对应的MACD数据索引
        start_idx = -1
        end_idx = -1
        for i, md in enumerate(macd_data):
            if md.date == bi.start_date:
                start_idx = i
            if md.date == bi.end_date:
                end_idx = i
        
        if start_idx == -1 or end_idx == -1:
            # 如果找不到精确日期，尝试近似匹配
            for i, md in enumerate(macd_data):
                if start_idx == -1 and md.date >= bi.start_date:
                    start_idx = i
                if end_idx == -1 and md.date >= bi.end_date:
                    end_idx = i
                    break
        
        if start_idx == -1 or end_idx == -1 or start_idx >= end_idx:
            return 0.0
        
        # 计算MACD柱状图面积（按笔方向分色累加）
        area = 0.0
        if bi.direction == 'up':
            # 上涨笔：只累加红柱（MACD > 0）
            for i in range(start_idx, end_idx + 1):
                if macd_data[i].macd > 0:
                    area += macd_data[i].macd
        else:
            # 下跌笔：只累加绿柱绝对值（MACD < 0）
            for i in range(start_idx, end_idx + 1):
                if macd_data[i].macd < 0:
                    area += abs(macd_data[i].macd)
        
        return area

    def _calculate_macd_dif_extreme_for_bi(self, bi, macd_data):
        """计算笔对应的DIF极值（对于下跌笔取最小值，对于上涨笔取最大值）"""
        # 找到笔开始和结束日期对应的MACD数据索引
        start_idx = -1
        end_idx = -1
        for i, md in enumerate(macd_data):
            if md.date == bi.start_date:
                start_idx = i
            if md.date == bi.end_date:
                end_idx = i
        
        if start_idx == -1 or end_idx == -1:
            # 如果找不到精确日期，尝试近似匹配
            for i, md in enumerate(macd_data):
                if start_idx == -1 and md.date >= bi.start_date:
                    start_idx = i
                if end_idx == -1 and md.date >= bi.end_date:
                    end_idx = i
                    break
        
        if start_idx == -1 or end_idx == -1 or start_idx >= end_idx:
            return 0.0
        
        # 提取该笔范围内的DIF值
        dif_values = [macd_data[i].dif for i in range(start_idx, end_idx + 1)]
        
        if bi.direction == 'down':
            # 下跌笔：取DIF最小值（最负的值）
            return min(dif_values)
        else:
            # 上涨笔：取DIF最大值（最正的值）
            return max(dif_values)

    def _identify_trends(self):
        """识别趋势：返回下跌趋势和上涨趋势的中枢列表"""
        if len(self.zhongshus) < 2:
            return [], []
        
        downtrends = []
        uptrends = []
        
        # 检查中枢序列
        for i in range(len(self.zhongshus) - 1):
            zs1 = self.zhongshus[i]
            zs2 = self.zhongshus[i + 1]
            
            # 下跌趋势：后中枢ZG < 前中枢ZD（中枢完全下移）
            if zs2.zg < zs1.zd:
                if not downtrends or downtrends[-1][-1] != zs1:
                    downtrends.append([zs1, zs2])
                else:
                    downtrends[-1].append(zs2)
            # 上涨趋势：后中枢ZD > 前中枢ZG（中枢完全上移）
            elif zs2.zd > zs1.zg:
                if not uptrends or uptrends[-1][-1] != zs1:
                    uptrends.append([zs1, zs2])
                else:
                    uptrends[-1].append(zs2)
        
        # 只保留至少包含2个中枢的趋势
        downtrends = [trend for trend in downtrends if len(trend) >= 2]
        uptrends = [trend for trend in uptrends if len(trend) >= 2]
        
        return downtrends, uptrends

    def _check_first_buy_structure(self, zs, leave_bi) -> bool:
        """检查一买的结构完整性（v3.5 新增）
        
        缠论原理：下跌趋势末端的三卖确认最后一个中枢被破坏后，
        如果后续出现反弹回到ZG之上，说明下跌结构已被反向走势破坏。
        此时再跌破ZD属于新的盘整结构，不是原下跌趋势的延续。
        
        沃森生物案例：
            中枢#6 [12.02, 12.37]
            笔#26(12.37→10.92)首次跌破ZD=12.02 → 真三卖
            笔#27(10.92→14.65)反弹回ZG=12.37之上 → ⚠️ 结构已破坏
            笔#32(12.97→11.65)再次跌破ZD → 新盘整，不是一买
        
        Args:
            zs: 最后一个下跌中枢
            leave_bi: 当前找到的离开段（最后一跌的向下笔）
        
        Returns:
            True = 结构已被反弹破坏，不是真一买
            False = 结构完整
        """
        # 找到第一个跌破ZD的向下笔（即：触发三卖的第一笔）
        # v5.3.1(M5): 从"中枢确立(end_date>=zs.end_date)之后"起搜——原实现从
        # zs.start_date 起搜会误捕中枢形成期构成笔(构成笔低点天然可<ZD,
        # 因 ZD=max(构成低点)), 把中枢内部正常震荡当成"结构已破坏", 过度
        # 抑制一买。与三买检测 after_bis 的口径(b.end_date>=zs.end_date)统一。
        first_break = None
        for bi in self.bis:
            if bi.direction != 'down':
                continue
            if bi.end_date < zs.end_date:
                continue  # 中枢确立前的笔(含构成笔)不参与突破判定
            bi_low = min(bi.start_price, bi.end_price)
            if bi_low < zs.zd:
                first_break = bi
                break

        if first_break is None:
            return False  # 从未跌破ZD，不存在三卖

        # 如果第一次跌破和当前离开段是同一笔，不用再检查
        if first_break is leave_bi:
            return False

        # 从首次跌破之后到当前离开段结束之前，检查有无反弹突破ZG
        # v3.5.2 修复：使用价格相对比例 + 中枢宽度比例双重条件
        # - 价格比例：超越ZG必须超过ZG的3%（对各价位股票统一）
        # - 宽度比例：超越ZG必须超过中枢区宽的50%（排除极窄中枢的小幅突破）
        # 同时满足两个条件才算结构破坏
        # v5.3.1(M5): 破坏检查同样只看中枢结束后的笔, 排除中枢内构成笔
        zs_width = zs.zg - zs.zd
        for bi in self.bis:
            if bi.direction != 'up':
                continue
            if bi.end_date < zs.end_date:
                continue  # 中枢内部构成笔的震荡不算破坏
            if bi.end_date <= first_break.end_date:
                continue  # 在首次跌破之前
            if bi.start_date >= leave_bi.end_date:
                continue  # 在当前离开段结束之后
            bi_high = max(bi.start_price, bi.end_price)
            exceed_zg = bi_high - zs.zg
            if exceed_zg <= 0:
                continue
            # 条件1：超ZG超ZG价的3%（价格相对比例）
            exceed_pct = exceed_zg / zs.zg
            # 条件2：超ZG超中枢区宽的50%（排除极小突破）
            width_ratio = exceed_zg / zs_width if zs_width > 0 else float('inf')
            if exceed_pct > 0.03 and width_ratio > 0.5:
                # 存在反弹突破ZG → 结构已被破坏
                return True
        
        return False  # 结构完整

    def _check_first_sell_structure(self, zs, leave_bi) -> bool:
        """检查一卖的结构完整性（v3.5 新增，对称检查）
        
        缠论原理：上涨趋势末端的二卖/三卖确认最后一个中枢被破坏后，
        如果后续出现回落到ZD之下的向下笔，说明上涨结构已被反向走势破坏。
        
        Args:
            zs: 最后一个上涨中枢
            leave_bi: 当前找到的离开段（最后一涨的向上笔）
        
        Returns:
            True = 结构已被回调破坏，不是真一卖
            False = 结构完整
        """
        # 找到第一个升破ZG的向上笔（触发趋势确认的第一笔）
        # v5.3.1(M5): 对称修复——从中枢确立后起搜, 排除形成期构成笔
        # (构成笔高点天然可>ZG), 避免把中枢内震荡误判为结构破坏。
        first_break = None
        for bi in self.bis:
            if bi.direction != 'up':
                continue
            if bi.end_date < zs.end_date:
                continue  # 中枢确立前的笔(含构成笔)不参与突破判定
            bi_high = max(bi.start_price, bi.end_price)
            if bi_high > zs.zg:
                first_break = bi
                break

        if first_break is None:
            return False  # 从未升破ZG

        if first_break is leave_bi:
            return False

        # 从首次升破之后到当前离开段结束之前，检查有无回落到ZD之下
        # v3.5.2 修复：对称逻辑——价格相对比例 + 中枢宽度比例双重条件
        # v5.3.1(M5): 破坏检查同样排除中枢内部构成笔
        zs_width = zs.zg - zs.zd
        for bi in self.bis:
            if bi.direction != 'down':
                continue
            if bi.end_date < zs.end_date:
                continue  # 中枢内部构成笔的震荡不算破坏
            if bi.end_date <= first_break.end_date:
                continue
            if bi.start_date >= leave_bi.end_date:
                continue
            bi_low = min(bi.start_price, bi.end_price)
            exceed_zd = zs.zd - bi_low
            if exceed_zd <= 0:
                continue
            exceed_pct = exceed_zd / zs.zd
            width_ratio = exceed_zd / zs_width if zs_width > 0 else float('inf')
            if exceed_pct > 0.03 and width_ratio > 0.5:
                # 存在回调跌破ZD → 结构已被破坏
                return True
        
        return False

    def _find_first_class_points(self):
        """识别一类买卖点"""
        points = []
        
        if not self.macd_data:
            return points
        
        downtrends, uptrends = self._identify_trends()
        
        # 寻找一类买点（下跌趋势末端背驰）
        for trend in downtrends:
            if len(trend) < 2:
                continue
                
            # 最后一个中枢
            last_zs = trend[-1]
            
            # v3.1 修复：检查最后一个下跌中枢之后，是否出现了更高的中枢
            # 如果出现了反向中枢（ZG > 下跌趋势最后中枢的ZG），说明下跌趋势
            # 已经被反转，不应再基于旧中枢产生一买信号
            counter_trend = [z for z in self.zhongshus
                           if z.start_date >= last_zs.end_date
                           and z != last_zs
                           and z.zg > last_zs.zg]
            if counter_trend:
                # 有新中枢在下跌趋势最后一个中枢上方形成
                # 例如 海康威视: ZS9(下跌趋势末中枢)之后出现ZS10(ZG=32.99 > 30.66)
                # 此时一买参考中枢应为ZS10而非ZS9，本次不产生一买
                continue
            
            # 找到离开段（最后一个中枢之后的向下笔）
            # 使用 end_date >= zs.end_date（而非 start_date >= zs.end_date）
            # 与三类买点同理：离开段可能在中枢最后一根构成笔中即启动
            after_bis = [b for b in self.bis if b.end_date >= last_zs.end_date and b.direction == 'down']
            if not after_bis:
                continue
                
            # 找到离开段：遍历候选找到第一个跌破中枢下沿（ZD）的向下笔
            # 而非简单取第一个（第一笔可能只是中枢延伸，真正离开段在后面）
            leave_bi = None
            for bi in after_bis:
                bi_low = min(bi.start_price, bi.end_price)
                if bi_low < last_zs.zd:
                    leave_bi = bi
                    break
            
            if not leave_bi:
                continue  # 无笔跌破ZD，非趋势末端
            
            # ════════════════════════════════════════════════════════
            # ★ v3.5 修复：结构完整性检查 — 防止假一买
            #
            # 缠论原理：首次跌破ZD（三卖）之后如果出现反弹回到ZG之上，
            # 说明下跌结构已被反向走势破坏。后续再跌破ZD属于新盘整。
            # ════════════════════════════════════════════════════════
            if self._check_first_buy_structure(last_zs, leave_bi):
                continue  # 结构已破坏，不是真一买
            
            # 找到进入段：取趋势第一个中枢之前的、方向为down的最近一笔
            # 缠论原文（第62课）：趋势背驰比较的是"趋势第一个中枢的进入段"
            # vs "趋势最后一个中枢的离开段"，而非局部中枢的进入/离开段
            first_zs = trend[0]
            enter_bi = None
            for bi in reversed(self.bis):
                if bi.end_date <= first_zs.start_date and bi.direction == 'down':
                    enter_bi = bi
                    break
            
            if not enter_bi:
                continue
            
            # 计算MACD面积
            enter_area = self._calculate_macd_area_for_bi(enter_bi, self.macd_data)
            leave_area = self._calculate_macd_area_for_bi(leave_bi, self.macd_data)

            # ════════════════════════════════════════════════════════
            # ★ v4.2 修复：DIF极值底背驰补充判断（与卖点侧对称）
            #
            # 卖点侧（上涨趋势）已有 DIF 顶背驰判断（v4.1），但买点侧
            # （下跌趋势）缺失对称逻辑——这是不对称实现。
            # 缠论原文：背驰要看"红绿柱面积+黄白线高度"两组信号。
            # 下跌趋势末端若加速赶底，MACD柱面积可能因绝对数值大而
            # 不背驰，但 DIF 线已显著高于前期低点 → DIF 底背驰。
            #
            # 判断逻辑：趋势全程中 DIF 的最低点（不含离开段本身）
            # vs 离开段的 DIF 最低点。
            # 如果离开段 DIF > 全局DIF最低点×阈值（即下跌动能衰减），
            # 则 DIF 底背驰成立。
            # ════════════════════════════════════════════════════════
            # v5.3.1(M2): 窗口终点排除离开段——原实现 md.date <= leave_bi.end_date
            # 把离开段自身也计入"全局最低"。末端赶底时 DIF 最低点通常落在离开段
            # 内, 此时 global==leave, "leave > global×阈值"对负数恒不成立,
            # DIF 底背驰被系统性抑制(只剩面积背驰单腿), 与本注释意图相悖。
            global_dif_min = 999.0
            for md in self.macd_data:
                if md.date >= enter_bi.start_date and md.date < leave_bi.start_date:
                    if md.dif < global_dif_min:
                        global_dif_min = md.dif

            leave_dif_min = self._calculate_macd_dif_extreme_for_bi(leave_bi, self.macd_data)

            area_beichi = leave_area < enter_area * self.divergence_threshold
            dif_beichi = leave_dif_min > global_dif_min * self.divergence_threshold
            # v5.4(A-3): DIF 腿加"面积至少不反向"约束——OR 组合里 DIF 腿单独
            # 开门时, 若离开段面积反而≥进入段(动能增强), 仅凭 DIF 高度判背驰
            # 属混合符号场景的平凡成立, 违背24课"柱面积+黄白线高度两组信号相互
            # 印证"的本意。约束后 DIF 腿只能作面积未恶化时的辅助确认, 不能独立
            # 开启一买。量级下限暂不引入(避免无标定参数), TODO 回测校准。
            if dif_beichi and not (leave_area < enter_area):
                dif_beichi = False

            # 检查背驰：面积背驰 OR DIF极值背驰，满足其一即产生一买
            if area_beichi or dif_beichi:
                # 构建背驰原因说明
                if area_beichi and dif_beichi:
                    beichi_reason = f'双重背驰：面积({enter_area:.2f}>{leave_area:.2f}) + DIF({global_dif_min:.2f}<{leave_dif_min:.2f})'
                elif area_beichi:
                    beichi_reason = f'面积背驰（{enter_area:.2f}>{leave_area:.2f}）'
                else:
                    beichi_reason = f'DIF底背驰（全局DIF最低{global_dif_min:.2f}<离开段{leave_dif_min:.2f}）'

                # 分类买点在离开段的终点（最低点）
                point_date = leave_bi.end_date
                point_price = leave_bi.end_price
                
                # v3.5.5: 检查一买是否被向上一笔确认
                _has_up_after = any(
                    b.direction == 'up' and b.start_date > point_date
                    for b in self.bis
                )
                
                # 跳过未确认的一买（潜一买不再作为独立买点输出）
                if not _has_up_after:
                    continue
                
                # 检查是否已存在相同日期、级别和类型的一类买点
                if not any(p.date == point_date and p.level == 1 and p.type == 'buy' for p in points):
                    points.append(BuySellPoint(
                        type='buy', level=1, date=point_date, price=round(point_price, 2),
                        reason=f'一类买点：{beichi_reason}', confirmed=True
                    ))
        
        # 寻找一类卖点（上涨趋势末端背驰）
        for trend in uptrends:
            if len(trend) < 2:
                continue
                
            # 最后一个中枢
            last_zs = trend[-1]
            
            # v3.1 修复：检查最后一个上涨中枢之后，是否出现了更低的中枢
            counter_trend = [z for z in self.zhongshus
                           if z.start_date >= last_zs.end_date
                           and z != last_zs
                           and z.zd < last_zs.zd]
            if counter_trend:
                # 有新中枢在上涨趋势最后一个中枢下方形成，上涨已被反转
                continue
            
            # 找到离开段（最后一个中枢之后的向上笔）
            # 使用 end_date >= zs.end_date（而非 start_date >= zs.end_date）
            # 与三类卖点同理：离开段可能在中枢最后一根构成笔中即启动
            after_bis = [b for b in self.bis if b.end_date >= last_zs.end_date and b.direction == 'up']
            if not after_bis:
                continue
                
            # 找到离开段：遍历候选找到第一个升破中枢上沿（ZG）的向上笔
            leave_bi = None
            for bi in after_bis:
                bi_high = max(bi.start_price, bi.end_price)
                if bi_high > last_zs.zg:
                    leave_bi = bi
                    break
            
            if not leave_bi:
                continue  # 无笔升破ZG，非趋势末端
            
            # ════════════════════════════════════════════════════════
            # ★ v3.5 修复：结构完整性检查 — 防止假一卖（对称逻辑）
            #
            # 缠论原理：首次升破ZG之后如果出现回调回到ZD之下，
            # 说明上涨结构已被破坏，后续升破ZG属于新盘整。
            # ════════════════════════════════════════════════════════
            if self._check_first_sell_structure(last_zs, leave_bi):
                continue  # 结构已破坏，不是真一卖
            
            # 找到进入段：取趋势第一个中枢之前的、方向为up的最近一笔
            # 缠论原文（第62课）：趋势背驰比较的是"趋势第一个中枢的进入段"
            # vs "趋势最后一个中枢的离开段"，而非局部中枢的进入/离开段
            first_zs = trend[0]
            enter_bi = None
            for bi in reversed(self.bis):
                if bi.end_date <= first_zs.start_date and bi.direction == 'up':
                    enter_bi = bi
                    break
            
            if not enter_bi:
                continue
            
            # 计算MACD面积
            enter_area = self._calculate_macd_area_for_bi(enter_bi, self.macd_data)
            leave_area = self._calculate_macd_area_for_bi(leave_bi, self.macd_data)

            # ════════════════════════════════════════════════════════
            # ★ v4.1 修复：DIF极值背驰补充判断
            #
            # 缠论原文说背驰判断要看"红绿柱面积+黄白线高度"两组信号。
            # 有些趋势末端是加速赶顶阶段（如中国中免2026-02），
            # MACD柱面积因绝对数值大而不背驰，
            # 但DIF线已显著低于前期高点——这是DIF顶背驰。
            #
            # 判断逻辑：趋势全程中DIF的最高点（不含离开段本身） 
            # vs 离开段的DIF最高点。
            # 如果离开段DIF < 全局DIF×阈值，则DIF背驰成立。
            # ════════════════════════════════════════════════════════

            # 计算趋势全局DIF最高点（从进入段开始, v5.3.1(M2): 不含离开段本身,
            # 对称修复——含离开段时 global==leave_max, 顶背驰恒 False）
            global_dif_max = -999.0
            for md in self.macd_data:
                if md.date >= enter_bi.start_date and md.date < leave_bi.start_date:
                    if md.dif > global_dif_max:
                        global_dif_max = md.dif

            leave_dif_max = self._calculate_macd_dif_extreme_for_bi(leave_bi, self.macd_data)

            area_beichi = leave_area < enter_area * self.divergence_threshold
            dif_beichi = leave_dif_max < global_dif_max * self.divergence_threshold
            # v5.4(A-3对称): 卖点侧 DIF 腿同样加"面积至少不反向"约束（见买侧注释）
            if dif_beichi and not (leave_area < enter_area):
                dif_beichi = False

            # 面积背驰 OR DIF极值背驰，满足其一即产生一卖
            if area_beichi or dif_beichi:
                # 构建背驰原因说明
                if area_beichi and dif_beichi:
                    beichi_reason = f'双重背驰：面积({enter_area:.2f}>{leave_area:.2f}) + DIF({global_dif_max:.2f}>{leave_dif_max:.2f})'
                elif area_beichi:
                    beichi_reason = f'面积背驰（{enter_area:.2f}>{leave_area:.2f}）'
                else:
                    beichi_reason = f'DIF背驰（全局DIF最高{global_dif_max:.2f}>离开段{leave_dif_max:.2f}）'

                # 一类卖点在离开段的终点（最高点）
                point_date = leave_bi.end_date
                point_price = leave_bi.end_price
                # 检查是否已存在相同日期、级别和类型的一类卖点
                if not any(p.date == point_date and p.level == 1 and p.type == 'sell' for p in points):
                    points.append(BuySellPoint(
                        type='sell', level=1, date=point_date, price=round(point_price, 2),
                        reason=f'一类卖点：{beichi_reason}'
                    ))

        # ════════════════════════════════════════════════════════
        # ★ v4.1 修复：Forward validation — 一卖后价格创出新高则一卖无效
        #
        # 缠论铁律：一卖必须是上涨趋势的最终终点。
        # 一卖之后如果出现了价格更高的向上一笔（即价格突破一卖价位），
        # 说明上涨趋势仍在延续，该"一卖"不是真正的趋势背驰终点。
        # 此时应移除该一卖，让后续趋势延伸后重新产生真正的一卖。
        #
        # 限365日窗口：超过1年后出现的更高价格视为新趋势周期，
        # 不破坏之前的一卖。
        #
        # 对称逻辑也适用一买：一买之后如果出现了价格更低的向下一笔，则一买无效。
        #
        # ⚠️ v4.2 回测开关：enable_forward_validation=False 时跳过本段。
        #    回测逐日遍历时，"未来365日"尚未发生，启用等于用未来数据
        #    作废历史信号（前视偏差）。实时分析（数据截至当日）不受影响。
        # ════════════════════════════════════════════════════════
        if not self.enable_forward_validation:
            return points
        validated = []
        for point in points:
            invalidated = False
            point_dt = parse_date_to_datetime(point.date)
            if point.type == 'sell' and point.level == 1:
                for bi in self.bis:
                    if bi.direction != 'up':
                        continue
                    bi_dt = parse_date_to_datetime(bi.end_date)
                    if bi_dt <= point_dt:
                        continue
                    # 限365日窗口，超过视为新周期
                    if (bi_dt - point_dt).days > 365:
                        break
                    bi_high = max(bi.start_price, bi.end_price)
                    if bi_high > point.price:
                        invalidated = True
                        break
            elif point.type == 'buy' and point.level == 1:
                for bi in self.bis:
                    if bi.direction != 'down':
                        continue
                    bi_dt = parse_date_to_datetime(bi.end_date)
                    if bi_dt <= point_dt:
                        continue
                    if (bi_dt - point_dt).days > 365:
                        break
                    bi_low = min(bi.start_price, bi.end_price)
                    if bi_low < point.price:
                        invalidated = True
                        break
            if not invalidated:
                validated.append(point)

        return validated

    def _find_second_class_points(self, first_class_points):
        """识别二类买卖点
        
        二类买点：一类买点后的第一次回调不创新低
        二类卖点：一类卖点后的第一次反弹不创新高
        """
        points = []
        
        if not first_class_points:
            return points
        
        # 按日期排序所有笔，便于查找
        sorted_bis = sorted(self.bis, key=lambda b: b.start_date)
        
        for fc_point in first_class_points:
            if fc_point.type == 'buy':
                # 寻找一类买点后的第一次回调（向下笔）
                # v5.3.1(M6): 回归缠论原文(17课)——二买=一买后第一次次级别回调
                # 不创新低。v3.5.4 的"遍历所有回调取最浅"会选中时点偏晚的回调、
                # 远离底部, 偏离原文语义; 经用户拍板(2026-08-22)回归原文口径。
                after_bis = [b for b in sorted_bis if b.start_date > fc_point.date]

                callback_bi = None
                for bi in after_bis:
                    if bi.direction == 'down':
                        bi_low = min(bi.start_price, bi.end_price)
                        if bi_low >= fc_point.price * (1 - self.second_class_tolerance):
                            callback_bi = bi  # 第一次不创新低的回调即二买
                            break
                        else:
                            # v5.4(A-6): 首次回调已创新低(跌破一买价×(1-容差))——一买
                            # 已被破坏、二买前提崩塌(17课), 放弃此锚点。
                            # 实盘模式 forward validation(L969-981) 本会清洗此类锚点;
                            # 回测模式(enable_forward_validation=False)此前会跳过创新低
                            # 回调继续找更晚的"不创新低"回调 → 产出非法二买。统一行为。
                            break

                if not callback_bi:
                    continue

                # ★ v3.5.3 修复：回调笔终点分型确认
                # 如果末笔被延伸（终点无底分型），回溯到最后一个底分型
                # 赛力斯案例：回调笔04-23→05-13，但05-13无底分型
                # 真正确认点在底@05-06(¥87.0)，非延伸终点05-13
                _end_date = callback_bi.end_date
                _end_price = callback_bi.end_price
                if callback_bi.direction == 'down':
                    # v5.4(A-1): 用 confirmed_end 判定端点是否需分型回溯——
                    # 旧实现对 end_date 做全分型表字符串匹配(慢且依赖格式);
                    # 延伸笔终点必无底分型, 回溯区间内最后一个底分型作为
                    # 结构确认点(赛力斯案例 05-13→05-06)。
                    if not getattr(callback_bi, 'confirmed_end', True):
                        _last_bottom = None
                        for fx in reversed(self.fenxings):
                            if fx.type == 'bottom' and str(fx.date)[:10] <= str(callback_bi.end_date)[:10] and str(fx.date)[:10] >= str(callback_bi.start_date)[:10]:
                                _last_bottom = fx
                                break
                        if _last_bottom:
                            _end_date = _last_bottom.date
                            _end_price = _last_bottom.price
                        else:
                            continue  # 找不到底分型，不产生二买
                
                # 检查回调是否创新低：回调笔的低点应该不低于一类买点价格
                callback_low = min(callback_bi.start_price, _end_price)
                if callback_low >= fc_point.price * (1 - self.second_class_tolerance):
                    point_date = _end_date
                    point_price = _end_price
                    # 去重检查：同一日期、级别的二类买点不重复添加
                    if not any(p.date == point_date and p.level == 2 and p.type == 'buy' for p in points):
                        points.append(BuySellPoint(
                        type='buy', level=2, date=point_date, price=round(point_price, 2),
                        reason=f'二类买点：一类买点后回调不创新低（一类买点:{fc_point.price:.2f}，回调低点:{callback_low:.2f}）'
                    ))
            
            elif fc_point.type == 'sell':
                # 寻找一类卖点后的第一次反弹（向上笔）
                # v5.3.1(M6): 对称回归原文——二卖=一卖后第一次反弹不创新高
                after_bis = [b for b in sorted_bis if b.start_date > fc_point.date]

                rebound_bi = None
                for bi in after_bis:
                    if bi.direction == 'up':
                        bi_high = max(bi.start_price, bi.end_price)
                        if bi_high <= fc_point.price * (1 + self.second_class_tolerance):
                            rebound_bi = bi  # 第一次不创新高的反弹即二卖
                            break
                        else:
                            # v5.4(A-6对称): 首次反弹已创新高(突破一卖价×(1+容差))——
                            # 一卖已被破坏、二卖前提崩塌, 放弃此锚点(与买侧A-6对称)。
                            break

                if not rebound_bi:
                    continue
                
                # ★ v3.5.3 修复：反弹笔终点分型确认（对称）
                _end_date = rebound_bi.end_date
                _end_price = rebound_bi.end_price
                if rebound_bi.direction == 'up':
                    # v5.4(A-1对称): confirmed_end 判定 + 分型回溯（见买侧注释）
                    if not getattr(rebound_bi, 'confirmed_end', True):
                        _last_top = None
                        for fx in reversed(self.fenxings):
                            if fx.type == 'top' and str(fx.date)[:10] <= str(rebound_bi.end_date)[:10] and str(fx.date)[:10] >= str(rebound_bi.start_date)[:10]:
                                _last_top = fx
                                break
                        if _last_top:
                            _end_date = _last_top.date
                            _end_price = _last_top.price
                        else:
                            continue
                
                # 检查反弹是否创新高：反弹笔的高点应该不高于一类卖点价格
                rebound_high = max(rebound_bi.start_price, _end_price)
                if rebound_high <= fc_point.price * (1 + self.second_class_tolerance):
                    point_date = _end_date
                    point_price = _end_price
                    # 去重检查：同一日期、级别的二类卖点不重复添加
                    if not any(p.date == point_date and p.level == 2 and p.type == 'sell' for p in points):
                        points.append(BuySellPoint(
                        type='sell', level=2, date=point_date, price=round(point_price, 2),
                        reason=f'二类卖点：一类卖点后反弹不创新高（一类卖点:{fc_point.price:.2f}，反弹高点:{rebound_high:.2f}）'
                    ))
        
        return points

    def _find_buy_sell_points(self):
        points = []
        
        # 首先识别一类买卖点（趋势背驰点）
        first_class_points = self._find_first_class_points()
        points.extend(first_class_points)
        
        # 然后识别二类买卖点（一类买卖点后的回调/反弹不创新低/高）
        # 只使用已确认的一类买卖点（confirmed=True）作为二类买卖点的锚点
        confirmed_fc_points = [p for p in first_class_points if getattr(p, 'confirmed', True)]
        second_class_points = self._find_second_class_points(confirmed_fc_points)
        points.extend(second_class_points)
        
        # 最后识别三类买卖点（中枢突破确认点）
        # v3.0修复: 先收集所有候选，再按"最近中枢"归属去重
        buy_candidates = []  # {date, price, zs_end, zs_label, v_type}
        sell_candidates = []

        # ════════════════════════════════════════════════════════
        # ★ v4.3: 下跌后首三买(V型反转)上下文标注
        #
        # 缠论原文（第20课）三买定义只要求"中枢＋次级别离开＋次级别回抽不回"，
        # 并不以一买/二买存在为前提——下跌趋势末中枢上方的首个三买(V型反转)
        # 完全合法。但其可靠性低于上涨趋势中的标准三买（无二买确认、前方套牢盘重），
        # pool_scanner 反转路径(v3.6)对同类信号已降为3分。
        # 此处为标准路径的三买补上同样的上下文标注，消除两条路径的评分不一致。
        # 标记条件：① 该中枢是某个下跌趋势的最后一个中枢；
        #          ② 三买日期前不存在已确认的一买/二买
        #           （若有一买确认，则属正常反转确认链，不标）。
        # ════════════════════════════════════════════════════════
        _downtrends, _uptrends = self._identify_trends()
        _dt_last_zs_ends = {str(t[-1].end_date) for t in _downtrends}

        for zs in self.zhongshus:
            # 使用 end_date >= zs.end_date（而非 start_date >= zs.end_date）
            # 这样才能捕获到"在最后一笔中枢构成笔中即突破ZG"的笔
            # 例如中枢B(2025-07~2025-09)的最后一根构成笔同时也是突破笔
            after_bis = [b for b in self.bis if b.end_date >= zs.end_date]
            
            # 三类买点
            found_up_break = False
            pullback_bi = None
            for bi in after_bis:
                if not found_up_break and bi.direction == 'up':
                    bi_high = max(bi.start_price, bi.end_price)
                    if bi_high > zs.zg:
                        found_up_break = True
                        continue
                if found_up_break and bi.direction == 'down':
                    pullback_bi = bi
                    pullback_low = min(bi.start_price, bi.end_price)
                    if pullback_low <= zs.zg:
                        # v3.5.4 修复：回踩进中枢 → 重置突破状态，继续搜索
                        # 后续可能有新的突破-回踩序列形成有效三买
                        found_up_break = False
                        pullback_bi = None
                        continue
                    continue
                if found_up_break and pullback_bi and bi.direction == 'up':
                    # v4.3: V型反转上下文判定——中枢为下跌趋势末中枢且此前无一买/二买确认
                    _is_v_first = (
                        str(zs.end_date) in _dt_last_zs_ends
                        and not any(
                            p.type == 'buy' and p.level in (1, 2) and str(p.date) < str(bi.start_date)
                            for p in points
                        )
                    )
                    buy_candidates.append({
                        'date': bi.start_date,
                        'price': round(bi.start_price, 2),
                        'zs_end': zs.end_date,
                        # v5.3.1(M3): 记录被突破中枢边界, 供评分侧锚定
                        'zg': zs.zg, 'zd': zs.zd,
                        'zs_label': f"[{str(zs.start_date)[-5:]}~{str(zs.end_date)[-5:]}](ZG={zs.zg:.0f})",
                        'v_type': _is_v_first,
                    })
                    break
            
            # 三类卖点
            found_down_break = False
            rebound_bi = None
            for bi in after_bis:
                if not found_down_break and bi.direction == 'down':
                    bi_low = min(bi.start_price, bi.end_price)
                    if bi_low < zs.zd:
                        found_down_break = True
                        continue
                if found_down_break and bi.direction == 'up':
                    rebound_bi = bi
                    rebound_high = max(bi.start_price, bi.end_price)
                    if rebound_high >= zs.zd:
                        # v3.5.4 修复：反弹进中枢 → 重置突破状态，继续搜索
                        found_down_break = False
                        rebound_bi = None
                        continue
                    continue
                if found_down_break and rebound_bi and bi.direction == 'down':
                    sell_candidates.append({
                        'date': bi.start_date,
                        'price': round(bi.start_price, 2),
                        'zs_end': zs.end_date,
                        # v5.3.1(M3): 记录被跌破中枢边界, 供评分侧锚定
                        'zg': zs.zg, 'zd': zs.zd,
                        'zs_label': f"[{str(zs.start_date)[-5:]}~{str(zs.end_date)[-5:]}](ZD={zs.zd:.0f})"
                    })
                    break
        
        # 去重：每个日期只保留最近中枢的候选
        def dedup_by_nearest_zs(candidates):
            by_date = {}
            for c in candidates:
                d = c['date']
                if d not in by_date or c['zs_end'] > by_date[d]['zs_end']:
                    by_date[d] = c
            return by_date.values()
        
        for c in dedup_by_nearest_zs(buy_candidates):
            if not any(p.date == c['date'] and p.level == 3 and p.type == 'buy' for p in points):
                _v_suffix = '(下跌后首三买/V型)' if c.get('v_type') else ''
                points.append(BuySellPoint(
                    type='buy', level=3, date=c['date'],
                    price=c['price'],
                    reason=f'三类买点：突破中枢{c["zs_label"]}后回踩不进中枢{_v_suffix}',
                    ref_zg=c.get('zg'), ref_zd=c.get('zd'),
                ))

        for c in dedup_by_nearest_zs(sell_candidates):
            if not any(p.date == c['date'] and p.level == 3 and p.type == 'sell' for p in points):
                points.append(BuySellPoint(
                    type='sell', level=3, date=c['date'],
                    price=c['price'],
                    reason=f'三类卖点：跌破中枢{c["zs_label"]}后反弹不进中枢',
                    ref_zg=c.get('zg'), ref_zd=c.get('zd'),
                ))
        
        # 后处理去重：同一(type, level, date)只保留一个买卖点
        # 多个不同中枢可能在同一天产生相同的买卖点（如相邻中枢对同一突破都触发三类点）
        seen = {}
        for p in points:
            key = (p.type, p.level, p.date)
            if key not in seen:
                seen[key] = p
        points = list(seen.values())
        
        # 按日期排序所有买卖点
        points.sort(key=lambda p: p.date)
        return points

class HTMLVisualizer:
    def __init__(self, symbol: str, name: str, analyzer: ChanLunAnalyzer, reference_price: float = None,
                 trade_signal=None, m30_analyzer=None, m30_data=None, segment_result=None,
                 display_start_date="2020-01-01"):
        self.symbol = symbol
        self.name = name
        self.analyzer = analyzer
        self.reference_price = reference_price
        self.trade_signal = trade_signal
        self.m30_analyzer = m30_analyzer
        self.m30_data = m30_data
        self.segment_result = segment_result  # SegmentChanLunAnalyzer 分析结果
        self.display_start_date = display_start_date  # 图表显示起始日期，默认2020-01-01
        
        # 不再使用价格校准——直接信任Baostock前复权数据
        self.scale_factor = 1.0
    
    def _check_m30_downtrend(self, buy_date: str) -> bool:
        """检查30分钟级别在买入日期前是否存在连续下跌段（同回测引擎逻辑）
        连续≥3支完结下跌笔视为30分钟下跌趋势未反转，返回True（应过滤）
        单支下跌笔是正常波动，不计入过滤条件

        v5.3.4(D3-②): 日期解析+降序排序结果缓存在 m30_analyzer 上——
        原实现每支笔重复 str() 切片且每个三买候选点都全列表 sort
        (O(N logN))；缓存后排序整个会话仅一次，查询退化为线性扫描。
        判定语义与原实现完全一致。
        """
        if not self.m30_analyzer or not self.m30_analyzer.bis:
            return False

        buy_date_str = date_to_str(buy_date)

        cached = getattr(self.m30_analyzer, '_downtrend_sorted_bis', None)
        if cached is None:
            pairs = []
            for bi in self.m30_analyzer.bis:
                bi_end = str(bi.end_date)[:10] if bi.end_date else ''
                if bi_end:
                    pairs.append((bi_end, bi.direction))
            pairs.sort(key=lambda p: p[0], reverse=True)  # 结束日期降序=最近在前
            cached = pairs
            self.m30_analyzer._downtrend_sorted_bis = cached

        # 从最近的笔往回数：跳过买入日之后完结的笔，遇非down笔停止
        down_count = 0
        for end_str, direction in cached:
            if end_str > buy_date_str:
                continue
            if direction == 'down':
                down_count += 1
            else:
                break

        return down_count >= 3  # 原为>=2，误杀标准三买回调
    
    def _apply_calibration(self, price):
        return price * self.scale_factor
    
    def generate_html(self, output_path: str):
        # 应用校准到所有价格数据
        calibrated_klines = []
        for k in self.analyzer.klines:
            calibrated_klines.append({
                'date': date_to_str(k.date),
                'open': self._apply_calibration(k.open),
                'high': self._apply_calibration(k.high),
                'low': self._apply_calibration(k.low),
                'close': self._apply_calibration(k.close),
                'volume': k.volume
            })
        
        calibrated_fenxings = []
        for f in self.analyzer.fenxings:
            calibrated_fenxings.append({
                'type': f.type,
                'index': f.index,
                'date': date_to_str(f.date),
                'price': self._apply_calibration(f.price)
            })
        
        calibrated_bis = []
        for b in self.analyzer.bis:
            calibrated_bis.append({
                'direction': b.direction,
                'start_date': date_to_str(b.start_date),
                'start_price': self._apply_calibration(b.start_price),
                'end_date': date_to_str(b.end_date),
                'end_price': self._apply_calibration(b.end_price)
            })
        
        calibrated_zhongshus = []
        for z in self.analyzer.zhongshus:
            calibrated_zhongshus.append({
                'start_date': date_to_str(z.start_date),
                'end_date': date_to_str(z.end_date),
                'zg': self._apply_calibration(z.zg),
                'zd': self._apply_calibration(z.zd)
            })
        
        calibrated_points = []
        for p in self.analyzer.buy_sell_points:
            # 30分钟下跌段过滤：三买时如果30分钟连续≥2支下跌笔，不显示该买点
            if p.type == 'buy' and p.level == 3 and self._check_m30_downtrend(p.date):
                continue
            # 获取多级别确认信息（如果存在）
            multilevel_info = {}
            if hasattr(p, 'multilevel_confirmation'):
                multilevel_info = p.multilevel_confirmation
            
            calibrated_points.append({
                'type': p.type,
                'level': p.level,
                'date': date_to_str(p.date),
                'price': self._apply_calibration(p.price),
                'reason': p.reason,
                'confirmed': p.confirmed,
                'multilevel_confirmation': multilevel_info
            })
        
        # MACD 数据不需要校准（是差值指标）
        macd_data = [{'date': date_to_str(m.date), 'dif': m.dif, 'dea': m.dea, 'macd': m.macd} for m in self.analyzer.macd_data]
        
        # 转换为 JSON
        # -- 按日期过滤：从 display_start_date 开始显示（默认2020-01-01），覆盖6年+数据 --
        DISPLAY_START = date_to_str(self.display_start_date)
        if calibrated_klines and calibrated_klines[0]['date'] < DISPLAY_START:
            # 找到第一个 >= DISPLAY_START 的K线索引
            trim_idx = 0
            for i, k in enumerate(calibrated_klines):
                if k['date'] >= DISPLAY_START:
                    trim_idx = i
                    break
            earliest_date = calibrated_klines[trim_idx]['date']
            calibrated_klines = calibrated_klines[trim_idx:]
            # 调整分型索引并过滤到保留日期范围
            calibrated_fenxings = [
                {**f, 'index': f['index'] - trim_idx}
                for f in calibrated_fenxings
                if f['date'] >= earliest_date
            ]
            # MACD 数据同步过滤
            macd_data = [m for m in macd_data if m['date'] >= earliest_date]
        
        klines_json = json.dumps(calibrated_klines)
        fenxings_json = json.dumps(calibrated_fenxings)
        bis_json = json.dumps(calibrated_bis)
        zhongshus_json = json.dumps(calibrated_zhongshus)
        points_json = json.dumps(calibrated_points)
        macd_json = json.dumps(macd_data)
        
        # ── 线段中枢数据（双视角支持）──
        seg_zhongshus_json = "[]"
        seg_points_json = "[]"
        segments_json = "[]"
        seg_count = 0
        seg_zs_count = 0
        seg_bs_count = 0
        if self.segment_result:
            seg_count = len(self.segment_result.segments)
            seg_zs_count = len(self.segment_result.seg_zhongshus)
            seg_bs_buys = [p for p in self.segment_result.seg_buy_sell_points if p.type == 'buy']
            seg_bs_count = len(seg_bs_buys)
            seg_zhongshus_json = json.dumps([{
                'start_date': date_to_str(z.start_date),
                'end_date': date_to_str(z.end_date),
                'zg': self._apply_calibration(z.zg),
                'zd': self._apply_calibration(z.zd),
                'gg': self._apply_calibration(z.gg),
                'dd': self._apply_calibration(z.dd),
                'segment_count': z.segment_count,
            } for z in self.segment_result.seg_zhongshus])
            seg_points_json = json.dumps([{
                'type': p.type,
                'level': p.level,
                'date': date_to_str(p.date),
                'price': self._apply_calibration(p.price),
                'reason': p.reason,
            } for p in self.segment_result.seg_buy_sell_points])
            segments_json = json.dumps([{
                'start_date': date_to_str(s.start_date),
                'start_price': self._apply_calibration(s.start_price),
                'end_date': date_to_str(s.end_date),
                'end_price': self._apply_calibration(s.end_price),
                'direction': s.direction,
            } for s in self.segment_result.segments])
        
        # ── 最新线段中枢信息 ──
        seg_zs_info = "无"
        if self.segment_result and self.segment_result.seg_zhongshus:
            latest_seg_zs = self.segment_result.seg_zhongshus[-1]
            seg_zs_info = f"¥{latest_seg_zs.zd:.2f} - ¥{latest_seg_zs.zg:.2f}"
        
        latest_price = self._apply_calibration(self.analyzer.klines[-1].close) if self.analyzer.klines else 0
        latest_date = date_to_str(self.analyzer.klines[-1].date) if self.analyzer.klines else ''
        start_date = date_to_str(self.analyzer.klines[0].date) if self.analyzer.klines else ''
        
        latest_zs = self.analyzer.zhongshus[-1] if self.analyzer.zhongshus else None
        zs_info = f"¥{latest_zs.zd:.2f} - ¥{latest_zs.zg:.2f}" if latest_zs else "无"
        position = "中枢上方" if latest_zs and latest_price > latest_zs.zg else ("中枢内部" if latest_zs and latest_price >= latest_zs.zd else "中枢下方") if latest_zs else "无中枢"
        
        latest_bi = self.analyzer.bis[-1] if self.analyzer.bis else None
        bi_info = f"{'上升' if latest_bi and latest_bi.direction=='up' else '下降'}笔，¥{latest_bi.start_price:.2f} → ¥{latest_bi.end_price:.2f}" if latest_bi else "无"
        
        # 生成交易指令单 HTML
        trade_order_html = ""
        if self.trade_signal:
            signal = self.trade_signal
            action_color = "var(--up)" if signal.action == "BUY" else ("var(--down)" if signal.action == "SELL" else "#aaa")
            action_cn = "买入" if signal.action == "BUY" else ("卖出" if signal.action == "SELL" else "持有")
            urgency_cn = "高" if signal.urgency == "HIGH" else ("中" if signal.urgency == "MEDIUM" else "低")
            pct_sl = (signal.stop_loss / signal.entry_price - 1) * 100 if signal.stop_loss > 0 and signal.entry_price > 0 else 0
            pct_tp = (signal.take_profit / signal.entry_price - 1) * 100 if signal.take_profit > 0 and signal.entry_price > 0 else 0
            ratio = abs((signal.take_profit - signal.entry_price) / (signal.stop_loss - signal.entry_price)) if signal.stop_loss > 0 and signal.entry_price > 0 else 0
            
            trade_order_html = f'''
                <div class="trade-order">
                    <h4>交易指令单</h4>
                    <div class="trade-row" style="border-left:3px solid {action_color}; padding-left:10px;">
                        <div style="font-size:20px; font-weight:700; color:{action_color};">{action_cn}</div>
                        <div style="font-size:12px; color:rgba(255,255,255,0.5);">建议仓位: {signal.position_size*100:.0f}% · 优先级: {urgency_cn}</div>
                    </div>
                    <div class="trade-detail">
                        <div class="trade-field"><span class="trade-label">入场价</span><span class="trade-val">¥{signal.entry_price:.2f}</span></div>'''
            if signal.action != 'HOLD' and signal.stop_loss > 0:
                trade_order_html += f'''
                        <div class="trade-field"><span class="trade-label">止损</span><span class="trade-val down">¥{signal.stop_loss:.2f} ({pct_sl:+.1f}%)</span></div>'''
            if signal.action != 'HOLD' and signal.take_profit > 0:
                trade_order_html += f'''
                        <div class="trade-field"><span class="trade-label">止盈</span><span class="trade-val up">¥{signal.take_profit:.2f} ({pct_tp:+.1f}%)</span></div>'''
            if signal.action != 'HOLD' and signal.stop_loss > 0:
                trade_order_html += f'''
                        <div class="trade-field"><span class="trade-label">盈亏比</span><span class="trade-val">{ratio:.2f}</span></div>'''
            trade_order_html += f'''
                        <div class="trade-field" style="grid-column:1/-1;"><span class="trade-label">理由</span><span class="trade-val" style="font-size:12px; color:rgba(255,255,255,0.7);">{signal.reason}</span></div>
                    </div>
                </div>'''
        
        html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self.name}({self.symbol}) - 缠论择时分析</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; color: #fff; }}
        .header {{ background: rgba(255,255,255,0.05); padding: 20px 40px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
        .header h1 {{ font-size: 24px; font-weight: 600; }}
        .header .subtitle {{ font-size: 14px; color: rgba(255,255,255,0.6); margin-top: 5px; }}
        .container {{ max-width: 1600px; margin: 0 auto; padding: 20px; }}
        .stats-panel {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .stat-card {{ background: rgba(255,255,255,0.05); border-radius: 10px; padding: 15px; border: 1px solid rgba(255,255,255,0.1); }}
        .stat-card .label {{ font-size: 12px; color: rgba(255,255,255,0.6); margin-bottom: 5px; }}
        .stat-card .value {{ font-size: 20px; font-weight: 600; }}
        .stat-card .value.up {{ color: #00d4aa; }}
        .stat-card .value.down {{ color: #ff6b6b; }}
        .controls {{ display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }}
        .toggle-btn {{ padding: 8px 16px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.1); color: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; transition: all 0.3s; }}
        .toggle-btn:hover {{ background: rgba(255,255,255,0.2); }}
        .toggle-btn.active {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-color: transparent; }}
        .chart-container {{ background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1); }}
        #main-chart {{ width: 100%; height: 550px; }}
        #macd-chart {{ width: 100%; height: 180px; }}
        .two-col {{ display: grid; grid-template-columns: 1fr 320px; gap: 20px; }}
        @media (max-width: 1200px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
        .signal-list {{ background: rgba(255,255,255,0.03); border-radius: 12px; padding: 20px; border: 1px solid rgba(255,255,255,0.1); max-height: 500px; overflow-y: auto; }}
        .signal-list h3 {{ font-size: 16px; margin-bottom: 15px; }}
        .signal-item {{ display: flex; justify-content: space-between; padding: 12px; background: rgba(255,255,255,0.03); border-radius: 8px; margin-bottom: 8px; }}
        .signal-item.buy {{ border-left: 3px solid #00d4aa; }}
        .signal-item.sell {{ border-left: 3px solid #ff6b6b; }}
        .signal-type {{ font-weight: 600; font-size: 14px; }}
        .signal-type.buy {{ color: #00d4aa; }}
        .signal-type.sell {{ color: #ff6b6b; }}
        .signal-item.potential {{ opacity: 0.55; }}
        .signal-info {{ text-align: right; font-size: 13px; color: rgba(255,255,255,0.7); }}
        .analysis-box {{ background: rgba(255,255,255,0.05); border-radius: 10px; padding: 15px; margin-bottom: 15px; border: 1px solid rgba(255,255,255,0.1); }}
        .analysis-box h4 {{ font-size: 14px; margin-bottom: 10px; color: rgba(255,255,255,0.9); }}
        .analysis-box p {{ font-size: 13px; color: rgba(255,255,255,0.7); line-height: 1.6; }}
        .price-tag {{ font-size: 18px; font-weight: 600; color: #ffd93d; }}
        :root {{ --up: #00d4aa; --down: #ff6b6b; }}
        .trade-order {{ background: rgba(255,255,255,0.05); border-radius: 10px; padding: 15px; margin-bottom: 15px; border: 1px solid rgba(255,255,255,0.1); }}
        .trade-order h4 {{ font-size: 14px; margin-bottom: 10px; color: rgba(255,255,255,0.9); }}
        .trade-detail {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }}
        .trade-field {{ display: flex; flex-direction: column; }}
        .trade-label {{ font-size: 11px; color: rgba(255,255,255,0.5); margin-bottom: 2px; }}
        .trade-val {{ font-size: 15px; font-weight: 600; color: #fff; }}
        .trade-val.up {{ color: var(--up); }}
        .trade-val.down {{ color: var(--down); }}   
    </style>
</head>
<body>
    <div class="header">
        <h1>{self.name}({self.symbol}) 缠论择时分析</h1>
        <div class="subtitle">数据范围：{start_date} 至 {latest_date} · 分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
    </div>
    <div class="container">
        <div class="stats-panel">
            <div class="stat-card"><div class="label">最新价格</div><div class="value price-tag">¥{latest_price:.2f}</div></div>
            <div class="stat-card"><div class="label">当前位置</div><div class="value">{position}</div></div>
            <div class="stat-card"><div class="label">分型数量</div><div class="value">{len(self.analyzer.fenxings)}</div></div>
            <div class="stat-card"><div class="label">笔数量</div><div class="value">{len(self.analyzer.bis)}</div></div>
            <div class="stat-card"><div class="label">中枢数量</div><div class="value">{len(self.analyzer.zhongshus)}</div></div>
            <div class="stat-card"><div class="label">买点</div><div class="value up">{len(calibrated_points)}</div></div>
            <div class="stat-card"><div class="label">卖点</div><div class="value down">{len([p for p in self.analyzer.buy_sell_points if p.type=='sell'])}</div></div>
        </div>
        <div class="controls">
            <button class="toggle-btn active" onclick="toggleLayer('kline')">K 线</button>
            <button class="toggle-btn active" onclick="toggleLayer('fenxing')">分型</button>
            <button class="toggle-btn active" onclick="toggleLayer('bi')">笔</button>
            <button class="toggle-btn active" onclick="toggleLayer('zs')">中枢</button>
            <button class="toggle-btn active" onclick="toggleLayer('bs')">买卖点</button>
            <span style="margin-left:20px; color:rgba(255,255,255,0.5);">|</span>
            <button class="toggle-btn" id="seg-toggle" onclick="toggleView()">🔀 线段中枢</button>
            <button class="toggle-btn" onclick="setRange(120)">近半年</button>
            <button class="toggle-btn" onclick="setRange(250)">近一年</button>
            <button class="toggle-btn active" onclick="setRange(0)">全部</button>
        </div>
        <div class="two-col">
            <div>
                <div class="chart-container"><div id="main-chart"></div></div>
                <div class="chart-container"><div id="macd-chart"></div></div>
            </div>
            <div>
                <div class="analysis-box">
                    <h4>当前位置分析</h4>
                    <p>最新收盘价 <span class="price-tag">¥{latest_price:.2f}</span></p>
                    <p style="margin-top:8px;">最近中枢区间：{zs_info}</p>
                    <p style="margin-top:8px;">最近一笔：{bi_info}</p>
                </div>
                {trade_order_html}
                <div class="signal-list"><h3>买卖点信号</h3><div id="signal-list"></div></div>
            </div>
        </div>
    </div>
    <script>
        const klines = {klines_json};
        const fenxings = {fenxings_json};
        const bis = {bis_json};
        const zhongshus = {zhongshus_json};
        const points = {points_json};
        const macdData = {macd_json};
        const segZhongshus = {seg_zhongshus_json};
        const segPoints = {seg_points_json};
        const segments = {segments_json};
        let viewMode = 'bi';  // 'bi' | 'segment'
        let layers = {{ kline: true, fenxing: true, bi: true, zs: true, bs: true }};
        let mainChart, macdChart;
        function init() {{
            mainChart = echarts.init(document.getElementById('main-chart'));
            macdChart = echarts.init(document.getElementById('macd-chart'));
            render();
            renderSignals();
            window.addEventListener('resize', () => {{ mainChart.resize(); macdChart.resize(); }});
        }}
        function toggleLayer(name) {{
            layers[name] = !layers[name];
            event.target.classList.toggle('active', layers[name]);
            render(viewRange);
        }}
        function toggleView() {{
            viewMode = viewMode === 'bi' ? 'segment' : 'bi';
            const btn = document.getElementById('seg-toggle');
            btn.classList.toggle('active', viewMode === 'segment');
            btn.textContent = viewMode === 'segment' ? '🔀 线段中枢 ✓' : '🔀 线段中枢';
            render(viewRange);
            renderSignals();
        }}
        let viewRange = 0;
        function render(limit = 0) {{
            viewRange = limit;
            const data = limit > 0 ? klines.slice(-limit) : klines;
            const dates = data.map(d => d.date);
            const zhongshuData = viewMode === 'bi' ? zhongshus : segZhongshus;
            const pointData = viewMode === 'bi' ? points : segPoints;
            const series = [];
            if (layers.kline) {{
                series.push({{ type: 'candlestick', data: data.map(d => [d.open, d.close, d.low, d.high]), itemStyle: {{ color: '#00d4aa', color0: '#ff6b6b', borderColor: '#00d4aa', borderColor0: '#ff6b6b' }} }});
            }}
            if (layers.fenxing) {{
                const tops = fenxings.filter(f => f.type === 'top' && dates.includes(f.date));
                const bottoms = fenxings.filter(f => f.type === 'bottom' && dates.includes(f.date));
                series.push({{ type: 'scatter', data: tops.map(f => [f.date, f.price * 1.01]), symbol: 'triangle', symbolRotate: 180, symbolSize: 8, itemStyle: {{ color: '#ff6b6b' }} }});
                series.push({{ type: 'scatter', data: bottoms.map(f => [f.date, f.price * 0.99]), symbol: 'triangle', symbolSize: 8, itemStyle: {{ color: '#00d4aa' }} }});
            }}
            if (layers.bi) {{
                if (viewMode === 'bi') {{
                    const biLines = bis.filter(b => dates.includes(b.start_date) || dates.includes(b.end_date)).map(b => ({{ coords: [[b.start_date, b.start_price], [b.end_date, b.end_price]], lineStyle: {{ color: b.direction === 'up' ? '#00d4aa' : '#ff6b6b', width: 2 }} }}));
                    series.push({{ type: 'lines', coordinateSystem: 'cartesian2d', data: biLines }});
                }} else if (segments.length) {{
                    const segLines = segments.filter(s => dates.includes(s.start_date) || dates.includes(s.end_date)).map(s => ({{ coords: [[s.start_date, s.start_price], [s.end_date, s.end_price]], lineStyle: {{ color: s.direction === 'up' ? '#ffd93d' : '#ff6b6b', width: 3 }} }}));
                    series.push({{ type: 'lines', coordinateSystem: 'cartesian2d', data: segLines }});
                }}
            }}
            const markAreas = layers.zs ? zhongshuData.map(z => [{{ xAxis: z.start_date, yAxis: z.zd }}, {{ xAxis: z.end_date, yAxis: z.zg }}]) : [];
            if (layers.bs) {{
                const buys = pointData.filter(p => p.type === 'buy' && dates.includes(p.date));
                const sells = pointData.filter(p => p.type === 'sell' && dates.includes(p.date));
                const bsLabel = viewMode === 'bi' ? 'B' : 'SB';
                const ssLabel = viewMode === 'bi' ? 'S' : 'SS';
                const confirmedBuys = buys;
                if (confirmedBuys.length) {{
                    series.push({{ type: 'scatter', data: confirmedBuys.map(p => [p.date, p.price * 0.97]), symbol: 'pin', symbolSize: 20, itemStyle: {{ color: '#ffd93d' }}, label: {{ show: true, formatter: d => bsLabel + (confirmedBuys.find(b => b.date === d.data[0])?.level || ''), position: 'bottom', fontSize: 10, color: '#ffd93d' }} }}); 
                }}
                series.push({{ type: 'scatter', data: sells.map(p => [p.date, p.price * 1.03]), symbol: 'pin', symbolSize: 20, symbolRotate: 180, itemStyle: {{ color: '#ff6b6b' }}, label: {{ show: true, formatter: d => ssLabel + sells.find(s => s.date === d.data[0])?.level, position: 'top', fontSize: 10, color: '#ff6b6b' }} }});
            }}
mainChart.setOption({{ backgroundColor: 'transparent', tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }}, xAxis: {{ type: 'category', data: dates, axisLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.2)' }} }}, axisLabel: {{ color: 'rgba(255,255,255,0.6)' }} }}, yAxis: {{ type: 'value', scale: true, splitLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.05)' }} }}, axisLabel: {{ color: 'rgba(255,255,255,0.6)' }} }}, dataZoom: [{{ type: 'inside', start: 0, end: 100 }}, {{ type: 'slider', start: 0, end: 100, height: 20 }}], series: series.length ? series : [{{ type: 'candlestick', data: [] }}] }}, true);
            if (markAreas.length && series.length) {{
                mainChart.setOption({{ series: [{{ markArea: {{ silent: true, data: markAreas, itemStyle: {{ color: 'rgba(102,126,234,0.15)', borderColor: 'rgba(102,126,234,0.5)', borderWidth: 1 }} }} }}] }});
            }}
            const macdFiltered = macdData.filter(m => dates.includes(m.date));
            macdChart.setOption({{ backgroundColor: 'transparent', tooltip: {{ trigger: 'axis' }}, xAxis: {{ type: 'category', data: macdFiltered.map(m => m.date), axisLabel: {{ show: false }} }}, yAxis: {{ type: 'value', scale: true, splitLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.05)' }} }} }}, dataZoom: [{{ type: 'inside', start: 0, end: 100 }}], series: [{{ type: 'line', data: macdFiltered.map(m => m.dif), lineStyle: {{ color: '#667eea', width: 1 }}, symbol: 'none' }}, {{ type: 'line', data: macdFiltered.map(m => m.dea), lineStyle: {{ color: '#ffd93d', width: 1 }}, symbol: 'none' }}, {{ type: 'bar', data: macdFiltered.map(m => ({{ value: m.macd, itemStyle: {{ color: m.macd >= 0 ? '#00d4aa' : '#ff6b6b' }} }})) }}] }}, true);
            echarts.connect([mainChart, macdChart]);
        }}        function renderSignals() {{
            const list = document.getElementById("signal-list");
            const ptData = viewMode === 'bi' ? points : segPoints;
            const sorted = [...ptData].sort((a, b) => new Date(b.date) - new Date(a.date));
            const bsLabel = viewMode === 'bi' ? 'B' : 'SB';
            const ssLabel = viewMode === 'bi' ? 'S' : 'SS';
            list.innerHTML = sorted.map(p => {{
                const ml = p.multilevel_confirmation || {{}};
                const confidence = ml.confidence_score || 0;
                const high = ml.high_confidence ? "⭐" : "";
                const confType = ml.confirmation_type || "none";
                const typeTag = {{"direct": "直", "divergence": "背", "none": "无"}}[confType] || "?";
                const m30 = ml.m30_confirmation ? `30min✓(${{typeTag}})` : "30min✗";
                let confidenceBadge = "";
                if (Object.keys(ml).length > 0) {{
                    confidenceBadge = `<div style="font-size:10px; margin-top:2px; color:${{high ? "#ffd93d" : "#aaa"}}">${{high}}置信度: ${{confidence}}/5 (${{m30}})</div>`;
                }}
                const label = p.type === "buy" ? bsLabel : ssLabel;
                const labelNum = p.level;
                return `<div class="signal-item ${{p.type}}">
                    <div>
                        <div class="signal-type ${{p.type}}">${{label}}${{labelNum}}</div>
                        <div style="font-size:11px;color:rgba(255,255,255,0.5);margin-top:3px;">${{p.reason}}</div>
                        ${{confidenceBadge}}
                    </div>
                    <div class="signal-info">
                        <div>${{p.date}}</div>
                        <div style="font-weight:600;">¥${{p.price.toFixed(2)}}</div>
                    </div>
                </div>`;
            }}).join("");
        }}
        function setRange(days) {{
            document.querySelectorAll('.controls .toggle-btn').forEach((btn, i) => {{ if (i >= 5) btn.classList.remove('active'); }});
            event.target.classList.add('active');
            render(days);
        }}
        init();
    </script>
</body>
</html>'''
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        return output_path

class RecursiveTimingSystem:
    def __init__(self, data_manager):
        self.dm = data_manager
        self.analyses = {}

    def run_full_analysis(self, symbol, reference_price=None, start_date=None, end_date=None):
        daily_data = self.dm.get_klines(symbol, level='daily', start_date=start_date, end_date=end_date)
        daily_analyzer = ChanLunAnalyzer(level='daily').analyze(self.dm.to_json_list(daily_data))
        
        # 日线数据价格校准（统一方法，避免重复逻辑）
        if reference_price and daily_data is not None and not daily_data.empty:
            ChanLunAnalyzer.calibrate_prices(
                daily_analyzer,
                float(daily_data.iloc[-1]['close']),
                reference_price,
                caller_tag="RecursiveTimingSystem"
            )
        
        # 获取30分钟数据进行分析（用于多级别验证）
        # v5.3.4(D3-①): 30min 窗口截断至近2年——原实现 start_date=None 时
        # 全历史拉取（缓存实测 600332 达 101552 行），包含合并/分型/笔划分
        # 全量白算。消费面排查结论（2026-08-23）：_check_m30_confirmation
        # 仅用日线点 ±5 天窗口、_check_m30_downtrend 只看最近几笔，2 年前
        # 老点的 30min 确认自然失效属预期诚实降级；回测引擎自管窗口不受影响。
        # 调用方显式传 start_date 时尊重原值。
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')
            print(f"[RecursiveTimingSystem] 30min 窗口截断至近2年: {start_date} 起")
        m30_data = self.dm.get_klines(symbol, level='30min', start_date=start_date, end_date=end_date)
        m30_analyzer = None
        if not m30_data.empty:
            m30_analyzer = ChanLunAnalyzer(level='30min').analyze(self.dm.to_json_list(m30_data))
            self.analyses['30min'] = m30_analyzer
            # 进行多级别验证
            self._perform_multilevel_validation(daily_analyzer, m30_analyzer)
        
        self.analyses['daily'] = daily_analyzer
        return daily_analyzer
    
    def _perform_multilevel_validation(self, daily_analyzer, m30_analyzer):
        """执行日线与30分钟级别的多级别验证，计算置信度分数"""
        if not daily_analyzer.buy_sell_points:
            return
        
        # 为每个日线买卖点计算多级别确认分数
        for point in daily_analyzer.buy_sell_points:
            # 基础分数：一类=2，二类=2，三类=4（v6.3修正）
            # 三买base=4：自带确认属性（回踩不进中枢就是确认）
            # 一买base=2：缠论一买本身严格（需中枢背驰），有30min直接确认时达到4
            # 二买base=2：需要30min直接确认达到4
            # 一买/二买有30min direct(+2)时 confidence=4 → 触发+5
            base_score = {1: 2, 2: 2, 3: 4}.get(point.level, 1)
            
            # 检查30分钟确认
            m30_confirmation_info = self._check_m30_confirmation(point, m30_analyzer)
            m30_confirmed = m30_confirmation_info['confirmed']
            confirmation_type = m30_confirmation_info['type']
            
            # 根据确认类型给分
            if confirmation_type == 'direct':
                confirmation_score = 2
                confirmation_details = m30_confirmation_info.get('details', '30分钟买卖点确认')
            elif confirmation_type == 'divergence':
                confirmation_score = 1
                confirmation_details = m30_confirmation_info.get('details', '30分钟笔背驰确认')
            elif confirmation_type == 'macd':
                confirmation_score = 1
                confirmation_details = m30_confirmation_info.get('details', '30分钟MACD收缩确认')
            else:
                confirmation_score = 0
                confirmation_details = m30_confirmation_info.get('details', '无30分钟确认')
            
            # 总置信度分数（1-5分）
            confidence_score = base_score + confirmation_score
            
            # 标记高置信度信号（≥4分）
            high_confidence = confidence_score >= 4
            
            # 存储验证结果到买卖点对象（添加新属性）
            point.multilevel_confirmation = {
                'base_score': base_score,
                'm30_confirmation': m30_confirmed,
                'confirmation_type': confirmation_type,
                'confirmation_score': confirmation_score,
                'confirmation_details': confirmation_details,
                'confidence_score': confidence_score,
                'high_confidence': high_confidence
            }
    
    def _check_m30_confirmation(self, daily_point, m30_analyzer):
        """检查30分钟级别确认，返回详细信息"""
        if not m30_analyzer:
            return {'type': 'none', 'confirmed': False, 'details': '无30分钟分析器'}
        
        # 检查直接买卖点确认
        direct_confirmation = self._check_m30_direct_confirmation(daily_point, m30_analyzer)
        if direct_confirmation['confirmed']:
            return {'type': 'direct', 'confirmed': True, 'details': '30分钟同向买卖点', 'date_diff': direct_confirmation['date_diff']}
        
        # 检查笔背驰确认
        divergence_confirmation = self._check_m30_bi_divergence(daily_point, m30_analyzer)
        if divergence_confirmation['confirmed']:
            return {'type': 'divergence', 'confirmed': True, 'details': '30分钟笔背驰结构', 'bi_direction': divergence_confirmation['bi_direction']}

        # 检查MACD柱收缩确认（新增：验证MACD柱连续收缩）
        macd_confirmation = self._check_m30_macd_divergence(daily_point, m30_analyzer)
        if macd_confirmation['confirmed']:
            return {'type': 'macd', 'confirmed': True, 'details': '30分钟MACD柱收缩确认', 'shrink_count': macd_confirmation['shrink_count']}

        return {'type': 'none', 'confirmed': False, 'details': '无确认信号'}
    
    def _check_m30_direct_confirmation(self, daily_point, m30_analyzer):
        """检查30分钟级别是否有同向买卖点直接确认"""
        if not m30_analyzer.buy_sell_points:
            return {'confirmed': False, 'details': '无30分钟买卖点'}
        
        # 日线买卖点日期
        target_date = daily_point.date
        from datetime import datetime
        try:
            if isinstance(target_date, str):
                target_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
            else:
                target_dt = target_date
        except:
            return {'confirmed': False, 'details': '日期解析失败'}
        
        # 在30分钟买卖点中寻找同向确认
        for m30_point in m30_analyzer.buy_sell_points:
            # 类型必须相同（买点/卖点）
            if m30_point.type != daily_point.type:
                continue
            
            # 30分钟点日期（可能包含时间）
            m30_date = m30_point.date
            try:
                if isinstance(m30_date, str):
                    if ' ' in m30_date:
                        m30_dt = datetime.strptime(m30_date, '%Y-%m-%d %H:%M:%S').date()
                    else:
                        m30_dt = datetime.strptime(m30_date, '%Y-%m-%d').date()
                else:
                    m30_dt = m30_date
            except:
                continue
            
            # 日期在日线点之前5天内（v4.2 修复：只允许过去，不允许未来）
            # 原实现 date_diff <= 5 允许"未来5天的30分钟点"确认今天的日线点，
            # 在回测中构成前视偏差（generate_analysis.py L1724-1726）
            date_diff = (target_dt - m30_dt).days
            if 0 <= date_diff <= 5:
                return {'confirmed': True, 'date_diff': date_diff, 'details': f'30分钟{daily_point.type}点确认，时间差{date_diff}天'}
        
        return {'confirmed': False, 'details': '无时间窗口内同向买卖点'}
    
    def _check_m30_bi_divergence(self, daily_point, m30_analyzer):
        """检查30分钟笔的背驰结构确认"""
        if not m30_analyzer or not m30_analyzer.bis:
            return {'confirmed': False, 'details': '无30分钟笔数据'}
        
        # 日线买卖点日期
        target_date = daily_point.date
        from datetime import datetime
        try:
            if isinstance(target_date, str):
                target_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
            else:
                target_dt = target_date
        except:
            return {'confirmed': False, 'details': '日期解析失败'}
        
        # 寻找时间窗口内的30分钟笔
        window_bis = []
        for bi in m30_analyzer.bis:
            bi_date = bi.end_date
            try:
                if isinstance(bi_date, str):
                    if ' ' in bi_date:
                        bi_dt = datetime.strptime(bi_date, '%Y-%m-%d %H:%M:%S').date()
                    else:
                        bi_dt = datetime.strptime(bi_date, '%Y-%m-%d').date()
                else:
                    bi_dt = bi_date
            except:
                continue
            
            # 笔结束日期在日线点之前5天内（v4.2 修复：只允许过去）
            date_diff = (target_dt - bi_dt).days
            if 0 <= date_diff <= 5:
                window_bis.append((bi, date_diff))
        
        if not window_bis:
            return {'confirmed': False, 'details': '无时间窗口内笔'}
        
        # 检查背驰：需要MACD面积比较
        # 这里简化：检查笔的方向是否与日线买卖点类型一致
        # 买点：寻找下降笔结束（底分型）
        # 卖点：寻找上升笔结束（顶分型）
        for bi, date_diff in window_bis:
            if daily_point.type == 'buy' and bi.direction == 'down':
                # 下降笔结束，可能是买点
                return {'confirmed': True, 'bi_direction': 'down', 'date_diff': date_diff, 'details': f'30分钟下降笔结束，时间差{date_diff}天'}
            elif daily_point.type == 'sell' and bi.direction == 'up':
                # 上升笔结束，可能是卖点
                return {'confirmed': True, 'bi_direction': 'up', 'date_diff': date_diff, 'details': f'30分钟上升笔结束，时间差{date_diff}天'}
        
        return {'confirmed': False, 'details': '无匹配方向笔'}

    def _check_m30_macd_divergence(self, daily_point, m30_analyzer):
        """检查30分钟MACD柱是否在买卖点附近出现连续收缩（绿柱缩短/红柱缩短）的确认信号"""
        if not m30_analyzer or not m30_analyzer.macd_data:
            return {'confirmed': False, 'details': '无30分钟MACD数据'}

        # 日线买卖点日期
        target_date = daily_point.date
        from datetime import datetime
        try:
            if isinstance(target_date, str):
                target_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
            else:
                target_dt = target_date
        except:
            return {'confirmed': False, 'details': '日期解析失败'}

        # 在日线点前后±5天的30分钟MACD数据中查找
        window_macd = []
        for md in m30_analyzer.macd_data:
            md_date = md.date
            try:
                if isinstance(md_date, str):
                    if ' ' in md_date:
                        md_dt = datetime.strptime(md_date, '%Y-%m-%d %H:%M:%S').date()
                    else:
                        md_dt = datetime.strptime(md_date, '%Y-%m-%d').date()
                else:
                    md_dt = md_date
            except:
                continue
            date_diff = (target_dt - md_dt).days
            if 0 <= date_diff <= 5:
                window_macd.append((md, date_diff))

        if len(window_macd) < 4:
            return {'confirmed': False, 'details': f'MACD数据不足（仅{len(window_macd)}条）'}

        # 按日期排序
        window_macd.sort(key=lambda x: x[0].date)

        # 取最近的一段MACD柱值（靠近买卖点日期的后一半数据）
        half = len(window_macd) // 2
        recent_macd = window_macd[half:]

        # 如果仍然不足4条，使用全部
        if len(recent_macd) < 4:
            recent_macd = window_macd

        macd_values = [md.macd for md, _ in recent_macd]
        abs_values = [abs(v) for v in macd_values]

        # 检查MACD柱连续收缩（至少3根以上绝对值递减）
        shrink_count = 0
        for i in range(1, len(abs_values)):
            if abs_values[i] < abs_values[i - 1]:
                shrink_count += 1
            else:
                shrink_count = 0  # 不连续则重置
            if shrink_count >= 2:  # 连续3根递减（count=2表示3根中有2次递减关系）
                # 方向匹配检查
                if daily_point.type == 'buy':
                    # 买点：MACD柱应为负值（绿柱），且向零轴收缩
                    if all(v < 0 for v in macd_values[-3:]):
                        return {'confirmed': True, 'shrink_count': shrink_count + 1, 'details': f'买点附近MACD绿柱连续{shrink_count+1}根缩短'}
                elif daily_point.type == 'sell':
                    # 卖点：MACD柱应为正值（红柱），且向零轴收缩
                    if all(v > 0 for v in macd_values[-3:]):
                        return {'confirmed': True, 'shrink_count': shrink_count + 1, 'details': f'卖点附近MACD红柱连续{shrink_count+1}根缩短'}

        # 放宽条件：如果绝对值连续递减但未严格匹配方向，也给予确认（趋势减弱信号）
        if shrink_count >= 2:
            direction_hint = '负值区域' if all(v < 0 for v in macd_values[-3:]) else ('正值区域' if all(v > 0 for v in macd_values[-3:]) else '零轴附近')
            return {'confirmed': True, 'shrink_count': shrink_count + 1, 'details': f'MACD柱连续{shrink_count+1}根缩短（{direction_hint}，趋势减弱）'}

        return {'confirmed': False, 'details': f'MACD柱未连续收缩（最高连续{shrink_count+1 if shrink_count > 0 else 0}根）'}
