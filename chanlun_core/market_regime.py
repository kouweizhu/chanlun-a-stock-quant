"""
market_regime.py — 市场环境判定 + 仓位上限输出

流程：
  1. 读沪深300月K线 → 缠论分析 → 判定趋势状态
  2. 读最近一期宏观早报 → 提取风险信号
  3. 综合判定 → 输出仓位上限

用法：
  python market_regime.py                     # 判定当前市场环境
  python market_regime.py --output regimes.csv  # 输出到文件供 pool_screener 读取

仓位档位（4档）：
  1.0 — 满仓（大盘缠论买点 + 宏观利好）
  0.8 — 积极（向上趋势 + 宏观中性）
  0.5 — 中性（震荡 / 趋势不明）
  0.3 — 防御（大盘卖点 / 宏观风险）
"""
import sys
from date_utils import date_to_str, parse_date_to_datetime
import os
import re
import json
import glob
from datetime import datetime
from typing import Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer
from sentiment_analyzer import SentimentAnalyzer, get_analyzer

# ============================================================
# 配置
# ============================================================
MACRO_REPORT_DIR = "D:/常用文件/宏观数据监控"
REGIME_OUTPUT = None  # 由 --output 参数指定

# 宏观风险阈值（量化）
RISK_THRESHOLDS = {
    # 国际
    "gold_surge":        {"keyword": "黄金.*急涨|黄金.*暴涨|黄金.*飙升", "weight": -1},
    "gold_extreme":      {"keyword": "黄金.*5[5-9]\\d{2}", "weight": -2},  # >$5500
    "wti_crash":         {"keyword": "原油.*暴跌|WTI.*跌破.*8[0-5]", "weight": -1},
    "wti_extreme":       {"keyword": "原油.*1[2-9]\\d|WTI.*1[2-9]\\d", "weight": -1},
    "us_bond_surge":     {"keyword": "美债.*急升|美债.*飙升|美债收益率.*5\\.[5-9]|10年期.*5\\.[5-9]", "weight": -1},
    "us_bond_extreme":   {"keyword": "美债.*6\\.|10年期.*6\\.", "weight": -2},
    "rmb_depreciation":  {"keyword": "人民币.*贬|人民币.*跌破.*7\\.[5-9]|离岸.*7\\.[5-9]", "weight": -1},
    "dxy_surge":         {"keyword": "美元指数.*1[01]\\d", "weight": -1},
    # 国内
    "pmi_weak":          {"keyword": "PMI.*跌破.*50|PMI.*4[0-9]\\.|制造业PMI.*收缩", "weight": -1},
    "pmi_strong":        {"keyword": "PMI.*5[1-9]\\.|PMI.*扩张.*加快", "weight": +1},
    "pbc_tighten":       {"keyword": "央行.*回笼|央行.*净回笼|公开市场.*净回笼", "weight": -1},
    "pbc_ease":          {"keyword": "央行.*净投放|降准|降息|MLF.*下调|LPR.*下调", "weight": +1},
    "cn_bond_low":       {"keyword": "中国10年.*1\\.[5-9]|国债收益率.*1\\.[5-9]", "weight": +1},  # 宽松
    "cn_bond_high":      {"keyword": "中国10年.*[3-9]\\.|国债收益率.*[3-9]\\.", "weight": -1},
    # 地缘
    "geopolitics":       {"keyword": "战争|军事冲突|制裁.*升级|地缘.*紧张|台海|南海", "weight": -1},

    # ──── 跨市场联动（v1.4+）────
    "sp500_overnight_up":    {"keyword": "标普.*[涨上].*[1-9]\\.[0-9]%|标普.*收涨|隔夜标普.*涨", "weight": +1},
    "sp500_overnight_down":  {"keyword": "标普.*[跌下].*[2-9]\\.[0-9]%|标普.*重挫|标普.*暴跌", "weight": -1},
    "nasdaq_overnight_down": {"keyword": "纳指.*[跌下].*[3-9]\\.[0-9]%|纳指.*重挫|纳指.*暴跌", "weight": -1},
    "rmb_appreciation":      {"keyword": "人民币.*升值|人民币.*走强|在岸.*6\\.[5-9]|CNY.*6\\.[5-9]", "weight": +1},
    "rmb_sharp_depreciation":{"keyword": "人民币.*急贬|人民币.*跌.*7\\.[5-9]|离岸.*突破.*7\\.[5-9]", "weight": -1},
    "europe_crash":          {"keyword": "欧股.*[跌下].*[3-9]\\.[0-9]%|欧股.*暴跌|欧洲.*重挫", "weight": -1},
    "global_risk_on":        {"keyword": "全球股市.*齐涨|外围.*普涨|risk.on|风险偏好.*回暖", "weight": +1},
    "global_risk_off":       {"keyword": "全球股市.*齐跌|外围.*普跌|risk.off|恐慌.*蔓延", "weight": -1},
}

# ============================================================
# Step 1: 沪深300月线缠论分析
# ============================================================

def analyze_hs300() -> Optional[Dict]:
    """对沪深300月K线做缠论分析，返回结构信号"""
    
    try:
        # AKShare 获取日线数据 → 合成月线
        import akshare as ak
        df_daily = ak.stock_zh_index_daily(symbol="sh000300")
        if df_daily is None or df_daily.empty:
            print("[market_regime] 沪深300日线数据获取失败")
            return None
        
        # 合成月线
        df_daily['date_str'] = df_daily['date'].astype(str).str[:7]  # YYYY-MM
        # AKShare 列名: date, open, high, low, close, volume
        cols = {'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'}
        monthly_grouped = df_daily.groupby('date_str').agg(
            open=('open', 'first'),
            high=('high', 'max'),
            low=('low', 'min'),
            close=('close', 'last'),
            volume=('volume', 'sum'),
        ).reset_index()
        
        # 关键修正：过滤未闭合的当月K线（月线未收盘，不参与缠论分析）
        from datetime import datetime
        current_month = datetime.now().strftime('%Y-%m')
        monthly_grouped = monthly_grouped[monthly_grouped['date_str'] != current_month]
        if monthly_grouped.empty:
            print("[market_regime] 过滤当月后无月线数据")
            return None
        
        monthly_grouped['date'] = monthly_grouped['date_str'] + '-01'
        
        # 转成 ChanLun 需要的格式
        kline_list = []
        for _, row in monthly_grouped.iterrows():
            kline_list.append({
                'date': row['date'],
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
            })
        
        # 运行缠论分析
        analyzer = ChanLunAnalyzer(level='monthly').analyze(kline_list)
        
        # 提取关键信号
        bis = analyzer.bis or []
        points = analyzer.buy_sell_points or []
        zhongshus = analyzer.zhongshus or []
        
        last_bi = bis[-1] if bis else None
        bi_direction = last_bi.direction if last_bi else 'unknown'
        
        # 最近买卖点（过滤超过12个月的旧信号）
        recent_buy = None
        recent_sell = None
        today_str = datetime.now().strftime('%Y-%m-%d')
        for p in sorted(points, key=lambda p: p.date, reverse=True):
            p_date = date_to_str(p.date)
            # 忽略超过6个月的信号（原12个月，2026-05-08缩短）
            months_ago = (datetime.strptime(today_str[:7] + '-01', '%Y-%m-%d') - 
                         datetime.strptime(p_date[:7] + '-01', '%Y-%m-%d')).days / 30
            if months_ago > 12:
                continue
            if p.type == 'buy' and recent_buy is None:
                recent_buy = {'date': p_date, 'level': p.level, 'reason': str(p.reason)[:50]}
            if p.type == 'sell' and recent_sell is None:
                recent_sell = {'date': p_date, 'level': p.level, 'reason': str(p.reason)[:50]}
        
        # 新买点覆盖旧卖点（买点日期 > 卖点日期 → 卖点失效）
        if recent_buy and recent_sell:
            if recent_buy['date'] > recent_sell['date']:
                recent_sell = None  # 买点更新，覆盖卖点
                print(f"[market_regime] 卖点被更新的买点({recent_buy['date']})覆盖")

        # 价格相对中枢位置
        latest_close = float(monthly_grouped.iloc[-1]['close'])
        zs_position = 'unknown'
        if zhongshus:
            latest_zs = zhongshus[-1]
            if latest_close > latest_zs.zg:
                zs_position = 'above'  # 中枢上方
            elif latest_close >= latest_zs.zd:
                zs_position = 'inside'  # 中枢内部
            else:
                zs_position = 'below'  # 中枢下方
        
        result = {
            'index': '000300',
            'name': '沪深300',
            'latest_close': round(latest_close, 2),
            'bi_direction': bi_direction,
            'bi_count': len(bis),
            'zs_count': len(zhongshus),
            'zs_position': zs_position,
            'recent_buy': recent_buy,
            'recent_sell': recent_sell,
            'signal_count': len([p for p in points if p.type == 'buy']),
            'sell_count': len([p for p in points if p.type == 'sell']),
        }
        
        print(f"[market_regime] 沪深300: 现价{latest_close:.0f}, "
              f"笔方向={bi_direction}, 中枢位置={zs_position}, "
              f"最近买点={recent_buy['date'] if recent_buy else '无'}, "
              f"最近卖点={recent_sell['date'] if recent_sell else '无'}")
        
        return result
        
    except Exception as e:
        print(f"[market_regime] 沪深300分析异常: {e}")
        return None


# ============================================================
# Step 2: 宏观风险信号提取
# ============================================================

def find_latest_macro_report() -> Optional[str]:
    """找到最近一期的宏观早报"""
    pattern = os.path.join(MACRO_REPORT_DIR, "*宏观数据早报*.md")
    files = sorted(glob.glob(pattern), reverse=True)
    if not files:
        pattern = os.path.join(MACRO_REPORT_DIR, "*.md")
        files = sorted(glob.glob(pattern), reverse=True)
    
    if files:
        print(f"[market_regime] 宏观报告: {os.path.basename(files[0])}")
        return files[0]
    
    print("[market_regime] ⚠️ 未找到宏观报告")
    return None


def extract_macro_signals(report_path: str, use_sentiment: bool = True) -> Dict:
    """从宏观早报中提取风险信号

    Args:
        report_path: 宏观早报路径
        use_sentiment: True=使用SentimentAnalyzer(新), False=使用regex(旧)
    """
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"[market_regime] 读取宏观报告失败: {e}")
        return {'score': 0, 'signals': [], 'risk_level': 'unknown'}

    if use_sentiment:
        # ── 新模式：SentimentAnalyzer（否定翻转 + 强度修饰）──
        try:
            sa = get_analyzer()
            macro_score, signals_raw = sa.analyze(content)
            triggered = [
                f"{s['keyword']}({'利好' if s['weight']>0 else '利空'}, {s['weight']:+d})"
                + (f" [否定翻转]" if s['negated'] else "")
                + (f" [{s['category']}]" if s.get('category') else "")
                for s in signals_raw
            ]

            if macro_score >= 3:
                risk_level = 'favorable'
            elif macro_score >= 0:
                risk_level = 'neutral'
            elif macro_score >= -2:
                risk_level = 'caution'
            else:
                risk_level = 'risk_off'

            print(f"[market_regime] 宏观风险评分(语义): {macro_score:+d} ({risk_level})")
        except Exception as e:
            print(f"[market_regime] SentimentAnalyzer 失败({e})，回退 regex 模式")
            use_sentiment = False

    if not use_sentiment:
        # ── 旧模式：regex 关键词匹配（兜底）──
        macro_score = 0
        triggered = []

        for signal_name, config in RISK_THRESHOLDS.items():
            if re.search(config['keyword'], content, re.IGNORECASE):
                macro_score += config['weight']
                direction = '利好' if config['weight'] > 0 else '利空'
                triggered.append(f"{signal_name}({direction}, {config['weight']:+d})")

        if macro_score >= 3:
            risk_level = 'favorable'
        elif macro_score >= 0:
            risk_level = 'neutral'
        elif macro_score >= -2:
            risk_level = 'caution'
        else:
            risk_level = 'risk_off'

        print(f"[market_regime] 宏观风险评分(regex): {macro_score:+d} ({risk_level})")

    if triggered:
        for s in triggered:
            print(f"  - {s}")

    return {
        'score': macro_score,
        'signals': triggered,
        'risk_level': risk_level,
        'report_date': os.path.basename(report_path)[:10],
    }


# ============================================================
# Step 3: 综合判定
# ============================================================

def classify_regime(hs300: Optional[Dict], macro: Dict) -> Dict:
    """综合缠论结构 + 宏观信号，判定市场环境和仓位上限"""
    
    # 默认值
    regime = 'unknown'
    position_cap = 0.5
    reasons = []
    
    if hs300 is None:
        # 沪深300数据不可用
        regime = 'unknown'
        position_cap = 0.5
        reasons.append("沪深300数据不可用，默认中性仓位")
        return _build_result(regime, position_cap, reasons, hs300, macro)
    
    # === 缠论侧判定 ===
    bi_dir = hs300['bi_direction']
    zs_pos = hs300['zs_position']
    has_buy = hs300['recent_buy'] is not None
    has_sell = hs300['recent_sell'] is not None

    chanlun_bullish = False
    chanlun_bearish = False

    if has_sell and hs300['recent_sell']:
        sell_date = hs300['recent_sell']['date']
        # 新买点覆盖卖点：如果买点比卖点新，卖点失效
        if has_buy and hs300['recent_buy']:
            buy_date = hs300['recent_buy']['date']
            if buy_date > sell_date:
                # 买点更近 → 以买点为准
                chanlun_bullish = True
                reasons.append(f"沪深300买点({buy_date})覆盖卖点({sell_date})")
            else:
                chanlun_bearish = True
                reasons.append(f"沪深300月线出现卖点({sell_date})")
        else:
            chanlun_bearish = True
            reasons.append(f"沪深300月线出现卖点({sell_date})")
    elif bi_dir == 'up' and zs_pos == 'above':
        chanlun_bullish = True
        reasons.append("沪深300月线向上笔+中枢上方运行")
    elif bi_dir == 'up' and zs_pos == 'inside':
        chanlun_bullish = True
        reasons.append("沪深300月线向上笔+中枢内部运行")
    elif bi_dir == 'down' and zs_pos == 'below':
        chanlun_bearish = True
        reasons.append("沪深300月线向下笔+中枢下方运行")
    elif has_buy and hs300['recent_buy']:
        buy_date = hs300['recent_buy']['date']
        chanlun_bullish = True
        reasons.append(f"沪深300月线出现买点({buy_date})")
    else:
        reasons.append("沪深300月线方向不明")

    # === 宏观侧判定 ===
    macro_risk = macro.get('risk_level', 'neutral')
    macro_score = macro.get('score', 0)
    
    # === 综合判定 ===
    if chanlun_bullish and macro_risk in ('favorable', 'neutral') and macro_score >= 0:
        if chanlun_bullish and macro_risk == 'favorable':
            regime = 'bullish'        # 看涨
            position_cap = 1.0         # 满仓
            reasons.append("缠论看涨 + 宏观利好 → 满仓")
        else:
            regime = 'slow_bull'       # 慢牛/偏多
            position_cap = 0.8         # 8成
            reasons.append("缠论偏多 + 宏观中性 → 积极")
    
    elif chanlun_bearish and macro_risk in ('caution', 'risk_off'):
        regime = 'bearish'             # 看跌
        position_cap = 0.3             # 3成
        reasons.append("缠论看跌 + 宏观风险 → 防御")
    
    elif chanlun_bearish:
        regime = 'correction'          # 回调
        position_cap = 0.5             # 5成
        reasons.append("缠论偏空 + 宏观中性 → 中性")
    
    elif not chanlun_bullish and not chanlun_bearish:
        regime = 'range'               # 震荡
        position_cap = 0.5             # 5成
        reasons.append("缠论方向不明 → 中性")
    
    else:
        regime = 'range'
        position_cap = 0.5
        reasons.append("综合判定中性")
    
    return _build_result(regime, position_cap, reasons, hs300, macro)


def _build_result(regime: str, cap: float, reasons: list,
                  hs300: Optional[Dict], macro: Dict) -> Dict:
    return {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'regime': regime,
        'position_cap': cap,
        'position_label': f"{cap*100:.0f}%",
        'reasons': reasons,
        'hs300': hs300,
        'macro': {
            'score': macro.get('score', 0),
            'risk_level': macro.get('risk_level', 'unknown'),
            'signals': macro.get('signals', []),
        },
    }


# ============================================================
# Step 4: 输出
# ============================================================

def print_result(result: Dict):
    """终端输出"""
    labels = {
        'bullish': '🟢 看涨',
        'slow_bull': '🟢 慢牛/偏多',
        'range': '🟡 震荡',
        'correction': '🟠 回调',
        'bearish': '🔴 看跌',
        'unknown': '⚪ 未知',
    }
    
    cap_labels = {1.0: '满仓', 0.8: '8成', 0.5: '5成', 0.3: '3成'}
    
    print()
    print("=" * 50)
    print(f"  市场环境: {labels.get(result['regime'], result['regime'])}")
    print(f"  仓位上限: {cap_labels.get(result['position_cap'], result['position_label'])} ({result['position_label']})")
    print(f"  判定日期: {result['date']}")
    print("=" * 50)
    print()
    print("判定依据:")
    for r in result['reasons']:
        print(f"  • {r}")
    print()
    
    if result['hs300']:
        h = result['hs300']
        print(f"沪深300: ¥{h['latest_close']:.0f} | 笔:{h['bi_direction']} | 中枢:{h['zs_position']}")
    
    m = result['macro']
    print(f"宏观风险: 评分{m['score']:+d} | {m['risk_level']}")
    print("=" * 50)


def save_regime_csv(result: Dict, path: str):
    """保存到 CSV，供 pool_screener 读取"""
    import csv
    
    file_exists = os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['date', 'regime', 'position_cap', 'position_label', 
                           'hs300_price', 'hs300_bi', 'macro_score', 'macro_risk', 'reasons'])
        writer.writerow([
            result['date'],
            result['regime'],
            result['position_cap'],
            result['position_label'],
            result['hs300']['latest_close'] if result['hs300'] else '',
            result['hs300']['bi_direction'] if result['hs300'] else '',
            result['macro']['score'],
            result['macro']['risk_level'],
            ' | '.join(result['reasons']),
        ])
    
    print(f"[market_regime] 已写入: {path}")


# ============================================================
# 主入口
# ============================================================
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='市场环境判定')
    parser.add_argument('--output', type=str, 
                        default='D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core/regimes.csv',
                        help='输出 CSV 路径')
    args = parser.parse_args()
    
    print("=" * 50)
    print("  市场环境判定")
    print("=" * 50)
    print()
    
    # Step 1: 沪深300月线分析
    print("[Step 1] 沪深300月线缠论分析...")
    hs300 = analyze_hs300()
    
    # Step 2: 读宏观报告
    print("\n[Step 2] 宏观风险信号...")
    report_path = find_latest_macro_report()
    if report_path:
        macro = extract_macro_signals(report_path)
    else:
        macro = {'score': 0, 'signals': [], 'risk_level': 'unknown'}
        print("[market_regime] 无宏观报告，使用默认中性")
    
    # Step 3: 综合判定
    print("\n[Step 3] 综合判定...")
    result = classify_regime(hs300, macro)
    
    # Step 4: 输出
    print_result(result)
    
    if args.output:
        save_regime_csv(result, args.output)
    
    print("\n完毕。")
