"""
position_monitor.py — 持仓实时监控系统
用法：python position_monitor.py [--push]

数据流：
  持仓股票.xlsx (Windows实时编辑) → 读取 → ChanLun分析 → 持仓日报.md
  WSL D:/ 直接映射 Windows D: 盘，Excel 更新即时生效

监控项：
  1. 卖点信号（日线 + 30分钟）
  2. 结构止损（跌破买入时中枢）
  3. 硬止损（-8%）
  4. 止盈触发
  5. 负面消息扫描
"""
import sys
from date_utils import date_to_str, parse_date_to_datetime
import os
import json
import csv
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_manager import DataManager
from generate_analysis import ChanLunAnalyzer
from config_loader import BACKTEST_COMMISSION

# ============================================================
# 配置
# ============================================================
HOLDINGS_DIR = "D:/常用文件/持仓监控"
HOLDINGS_EXCEL = os.path.join(HOLDINGS_DIR, "持仓股票.xlsx")
HOLDINGS_JSON = os.path.join(HOLDINGS_DIR, "holdings_detail.json")
REPORT_DIR = os.path.join(HOLDINGS_DIR, "收盘检测报告")
os.makedirs(REPORT_DIR, exist_ok=True)

# 止损止盈参数（v4.2 从 config_loader 读取，与回测参数统一维护）
# 原实现硬编码 0.08/0.30/0.20/0.15，与 config.yaml 的 backtest_tp_* 双份维护，
# 改参数时容易漏改一边
from config_loader import (
    THRESHOLD_BACKTEST_TP_FIRST,
    THRESHOLD_BACKTEST_TP_SECOND,
    THRESHOLD_BACKTEST_TP_THIRD,
)
HARD_STOP_PCT = 0.08       # -8% 硬止损（固定风控底线，保留硬编码）
TAKE_PROFIT_1ST = THRESHOLD_BACKTEST_TP_FIRST   # 一买止盈（config: backtest_tp_first）
TAKE_PROFIT_2ND = THRESHOLD_BACKTEST_TP_SECOND  # 二买止盈（config: backtest_tp_second）
TAKE_PROFIT_3RD = THRESHOLD_BACKTEST_TP_THIRD   # 三买止盈（config: backtest_tp_third）

# 近期信号统计窗口（v4.2 修复：原硬编码 '2025-01-01'，2027年起永久失效）
# 改为动态计算：从当前日期往前推 N 个月
RECENT_SIGNAL_MONTHS = 6  # 统计近6个月的买卖点

# ============================================================
# 工具函数
# ============================================================
def parse_date(s) -> Optional[str]:
    """解析各种日期格式 → YYYY-MM-DD"""
    if s is None or pd.isna(s):
        return None
    s = str(s).strip()
    for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S']:
        try:
            return datetime.strptime(s[:10], fmt).strftime('%Y-%m-%d')
        except:
            continue
    return s[:10] if len(s) >= 10 else None


def classify_buy_type(reason: str) -> int:
    """从买入理由推断买点等级"""
    if not reason:
        return 3  # 默认三买
    reason = str(reason)
    if '一类买点' in reason or '一买' in reason:
        return 1
    if '二类买点' in reason or '二买' in reason:
        return 2
    return 3


def tp_pct_for_level(level: int) -> float:
    return {1: TAKE_PROFIT_1ST, 2: TAKE_PROFIT_2ND, 3: TAKE_PROFIT_3RD}.get(level, 0.15)


def _recent_signal_cutoff() -> str:
    """计算近期信号统计的截止日期（当前日期 - RECENT_SIGNAL_MONTHS 个月）

    v4.2 修复：原硬编码 '2025-01-01'，系统在 2027 年后该日期永久失效，
    导致"近期买点/卖点"统计永远为空。改为动态计算。
    """
    now = datetime.now()
    month = now.month - RECENT_SIGNAL_MONTHS
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    try:
        return datetime(year, month, 1).strftime('%Y-%m-%d')
    except Exception:
        return '2000-01-01'


# ============================================================
# 持仓数据加载
# ============================================================
class HoldingsManager:
    """持仓数据管理：Excel 主源 + JSON 扩展"""
    
    def __init__(self):
        self.holdings: List[Dict] = []
        self._load()
    
    def _load(self):
        """加载持仓"""
        if not os.path.exists(HOLDINGS_EXCEL):
            print(f"[警告] 持仓文件不存在: {HOLDINGS_EXCEL}")
            return
        
        try:
            df = pd.read_excel(HOLDINGS_EXCEL, header=None, dtype={0: str})
        except Exception as e:
            print(f"[错误] 读取Excel失败: {e}")
            return
        
        # 智能检测表头：如果第一行第一列看起来像股票代码（4-6位数字）
        first_val = str(df.iloc[0, 0]).strip()
        has_header = not (first_val.isdigit() and 4 <= len(first_val) <= 6)
        
        if has_header:
            # 第一行是表头
            headers = [str(c).strip() for c in df.iloc[0]]
            df = df.iloc[1:]
            df.columns = headers
        else:
            # 无表头，使用默认列名
            ncols = df.shape[1]
            default_cols = ['code', 'name', 'entry_date', 'entry_price', 'shares', 'reason', 'stop_price', 'tp_price']
            df.columns = default_cols[:ncols]
        
        # 列名标准化
        col_map = {
            '代码': 'code', '股票代码': 'code', '品种代码': 'code', 'stock_code': 'code', 'code': 'code',
            '名称': 'name', '股票名称': 'name', '品种简称': 'name', 'stock_name': 'name', 'name': 'name',
            '买入日期': 'entry_date', '买入时间': 'entry_date', 'entry_date': 'entry_date',
            '买入价': 'entry_price', '买入价格': 'entry_price', 'entry_price': 'entry_price',
            '股数': 'shares', '数量': 'shares', 'shares': 'shares',
            '买入理由': 'reason', '理由': 'reason', 'reason': 'reason',
            '止损价': 'stop_price', 'stop_price': 'stop_price',
            '止盈价': 'tp_price', 'tp_price': 'tp_price',
        }
        # 只应用存在的列
        rename_map = {c: col_map[c] for c in df.columns if c in col_map}
        df = df.rename(columns=rename_map)
        
        # 如果只有两列（code+name），说明用户还没填买入信息
        has_entry = 'entry_date' in df.columns
        
        for _, row in df.iterrows():
            h = {
                'code': str(row.get('code', '')).strip().zfill(6),
                'name': str(row.get('name', '')),  # 可能缺失
            }
            if has_entry:
                h['entry_date'] = parse_date(row.get('entry_date'))
                h['entry_price'] = float(row['entry_price']) if pd.notna(row.get('entry_price')) else None
                h['shares'] = int(row['shares']) if pd.notna(row.get('shares')) else 0
                h['reason'] = str(row.get('reason', ''))
                h['stop_price'] = float(row['stop_price']) if pd.notna(row.get('stop_price')) else None
                h['tp_price'] = float(row['tp_price']) if pd.notna(row.get('tp_price')) else None
            
            # 确保code是6位
            if len(h['code']) < 6:
                h['code'] = h['code'].zfill(6)
            
            self.holdings.append(h)
        
        # 加载 JSON 扩展数据（如果有）
        if os.path.exists(HOLDINGS_JSON):
            with open(HOLDINGS_JSON, 'r', encoding='utf-8') as f:
                extra = json.load(f)
            for h in self.holdings:
                if h['code'] in extra:
                    for k, v in extra[h['code']].items():
                        if k not in h or h[k] is None:
                            h[k] = v
    
    def reload(self):
        """重新加载（Excel更新后）"""
        self.holdings = []
        self._load()
    
    def has_entry_info(self) -> bool:
        """是否有完整的买入信息（只需要日期和价格，不需要股数）"""
        return all(
            h.get('entry_date') and h.get('entry_price')
            for h in self.holdings
        )


# ============================================================
# 单只股票分析
# ============================================================
def analyze_stock(code: str, name: str, entry: Optional[Dict] = None) -> Dict:
    """对单只持仓股运行 ChanLun 分析，返回监控结果"""
    dm = DataManager()
    
    result = {
        'code': code,
        'name': name,
        'status': 'OK',
        'alerts': [],
        'current_price': None,
        'pnl_pct': None,
        'signal_summary': '',
        'error': None,
    }
    
    # 1. 获取数据
    try:
        daily_df = dm.get_klines(code, 'daily', start_date='2020-01-01')
    except Exception as e:
        result['status'] = 'ERROR'
        result['error'] = f'数据获取失败: {e}'
        return result
    
    if daily_df.empty:
        result['status'] = 'ERROR'
        result['error'] = '无K线数据'
        return result
    
    current_close = float(daily_df.iloc[-1]['close'])
    result['current_price'] = current_close
    
    # 2. ChanLun 分析
    try:
        analyzer = ChanLunAnalyzer(level='daily').analyze(dm.to_json_list(daily_df))
    except Exception as e:
        result['status'] = 'ERROR'
        result['error'] = f'ChanLun分析失败: {e}'
        return result
    
    # 3. 检查买入后的新卖点
    if entry and entry.get('entry_date'):
        entry_date = entry['entry_date']
        new_sells = []
        for bp in analyzer.buy_sell_points:
            if bp.type == 'sell' and date_to_str(bp.date) > entry_date:
                new_sells.append({
                    'date': date_to_str(bp.date),
                    'price': bp.price,
                    'level': bp.level,
                    'reason': bp.reason or '',
                })
        
        if new_sells:
            latest_sell = new_sells[-1]
            result['alerts'].append({
                'type': 'SELL_SIGNAL',
                'level': 'HIGH',
                'message': f"日线卖点: {latest_sell['date']} {latest_sell['reason'][:40]}",
            })
            result['status'] = 'SELL'
    
    # 4. 检查最近的买卖点概况
    buys = [p for p in analyzer.buy_sell_points if p.type == 'buy']
    sells = [p for p in analyzer.buy_sell_points if p.type == 'sell']
    
    recent_buys = [p for p in buys if date_to_str(p.date) > _recent_signal_cutoff()]
    recent_sells = [p for p in sells if date_to_str(p.date) > _recent_signal_cutoff()]
    
    result['signal_summary'] = f"近期买点{len(recent_buys)}个, 卖点{len(recent_sells)}个"
    
    # 5. 结构止损检查
    if entry and entry.get('entry_price'):
        entry_price = entry['entry_price']
        buy_level = classify_buy_type(entry.get('reason', ''))
        
        # 结构止损检查（优先使用Excel中手动设置的止损价）
        if entry.get('stop_price') is not None:
            use_stop = entry['stop_price']
            stop_label = f"手动止损¥{use_stop:.2f}"
        else:
            structure_stop = None
            if analyzer.zhongshus:
                if buy_level == 3:
                    for zs in reversed(analyzer.zhongshus):
                        if zs.zg < entry_price:
                            structure_stop = zs.zg
                            break
                else:
                    for zs in reversed(analyzer.zhongshus):
                        if zs.zd < entry_price:
                            structure_stop = zs.zd
                            break
            use_stop = structure_stop
            stop_label = f"结构位¥{use_stop:.2f}" if use_stop else None
        
        if use_stop and current_close <= use_stop:
            result['alerts'].append({
                'type': 'STRUCTURE_STOP',
                'level': 'CRITICAL',
                'message': f"跌破{stop_label} (当前¥{current_close:.2f})",
            })
            result['status'] = 'STOP'
        
        # 硬止损 -8%
        hard_stop = entry_price * (1 - HARD_STOP_PCT)
        if current_close <= hard_stop:
            result['alerts'].append({
                'type': 'HARD_STOP',
                'level': 'CRITICAL',
                'message': f"硬止损-8%: ¥{hard_stop:.2f} (当前¥{current_close:.2f})",
            })
            result['status'] = 'STOP'
        
        # 止盈（优先使用Excel中手动设置的止盈价）
        if entry.get('tp_price') is not None:
            tp_target = entry['tp_price']
            tp_label = f"手动止盈¥{tp_target:.2f}"
        else:
            tp_target = entry_price * (1 + tp_pct_for_level(buy_level))
            tp_label = f"+{tp_pct_for_level(buy_level)*100:.0f}%: ¥{tp_target:.2f}"
        if current_close >= tp_target:
            result['alerts'].append({
                'type': 'TAKE_PROFIT',
                'level': 'INFO',
                'message': f"止盈触发 {tp_label}",
            })
            if result['status'] == 'OK':
                result['status'] = 'TP'
        
        # 盈亏
        result['pnl_pct'] = round((current_close / entry_price - 1) * 100, 2)
    
    # 6. 笔方向和MACD状态
    if analyzer.bis:
        last_bi = analyzer.bis[-1]
        result['bi_direction'] = last_bi.direction
    if analyzer.macd_data and len(analyzer.macd_data) >= 2:
        macd = analyzer.macd_data
        result['macd_status'] = '金叉' if float(macd[-1].dif) > float(macd[-1].dea) else '死叉'
    
    return result


# ============================================================
# 日报生成
# ============================================================
def generate_report(holdings_mgr: HoldingsManager, results: List[Dict]) -> str:
    """生成持仓日报 Markdown"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    lines = []
    lines.append(f"# 持仓日报 — {today}")
    lines.append("")
    lines.append(f"> 监控 {len(results)} 只持仓股 | 自动生成")
    lines.append("")
    
    # 统计
    status_count = defaultdict(int)
    alert_count = 0
    for r in results:
        status_count[r['status']] += 1
        alert_count += len(r.get('alerts', []))
    
    lines.append("## 概览")
    lines.append("")
    lines.append(f"| 状态 | 数量 |")
    lines.append(f"|------|:----:|")
    for status, count in sorted(status_count.items()):
        icon = {'OK': '🟢', 'SELL': '🔴', 'STOP': '🛑', 'TP': '🎯', 'ERROR': '⚠️'}.get(status, '❓')
        lines.append(f"| {icon} {status} | {count} |")
    lines.append(f"| **总告警** | **{alert_count}** |")
    lines.append("")
    
    # 详细表格
    lines.append("## 持仓明细")
    lines.append("")
    lines.append("| 代码 | 名称 | 现价 | 盈亏 | 状态 | 笔方向 | MACD | 信号 |")
    lines.append("|------|------|:----:|:----:|:----:|:------:|:----:|------|")
    
    for r in results:
        code = r['code']
        name = r.get('name', code)
        price = f"¥{r['current_price']:.2f}" if r['current_price'] else '-'
        pnl = f"{r['pnl_pct']:+.2f}%" if r.get('pnl_pct') is not None else '-'
        status_icon = {'OK': '🟢', 'SELL': '🔴', 'STOP': '🛑', 'TP': '🎯', 'ERROR': '⚠️'}.get(r['status'], '❓')
        bi = r.get('bi_direction', '-')
        macd = r.get('macd_status', '-')
        sig = r.get('signal_summary', '-')
        lines.append(f"| {code} | {name} | {price} | {pnl} | {status_icon} | {bi} | {macd} | {sig} |")
    
    lines.append("")
    
    # 告警详情
    all_alerts = [(r, a) for r in results for a in r.get('alerts', [])]
    if all_alerts:
        lines.append("## ⚠️ 告警详情")
        lines.append("")
        for r, a in all_alerts:
            level_icon = {'CRITICAL': '🛑', 'HIGH': '🔴', 'MEDIUM': '🟡', 'INFO': '🔵'}.get(a['level'], '❓')
            lines.append(f"- {level_icon} **{r['code']} {r['name']}** — {a['message']}")
        lines.append("")
    
    # 最近买卖点
    lines.append("## 近期买卖点")
    lines.append("")
    for r in results:
        if r.get('error'):
            lines.append(f"- ⚠️ **{r['code']}**: {r['error']}")
        else:
            lines.append(f"- **{r['code']} {r['name']}**: {r.get('signal_summary', '无')}")
    lines.append("")
    
    lines.append("---")
    lines.append(f"*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    
    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='持仓监控')
    parser.add_argument('--push', action='store_true', help='推送到微信')
    parser.add_argument('--alert-only', action='store_true', help='仅在有告警时推送（适合每日cron）')
    parser.add_argument('--output', type=str, help='输出MD路径（默认 持仓监控目录/日期_持仓日报.md）')
    args = parser.parse_args()
    
    print("=" * 50)
    print("  持仓监控系统")
    print("=" * 50)
    
    # 加载持仓
    print(f"\n[加载] {HOLDINGS_EXCEL}")
    mgr = HoldingsManager()
    
    if not mgr.holdings:
        print("[提示] 无持仓数据")
        if args.push or args.alert_only:
            try:
                from weixin_pusher import WeixinPusher
                WeixinPusher().send(f"[EMPTY] 当前无持仓\n\n{datetime.now().strftime('%Y-%m-%d %H:%M')}")
                print("[推送] 空持仓通知已推送")
            except Exception as e:
                print(f"[推送失败] {e}")
        sys.exit(0)
    
    has_entry = mgr.has_entry_info()
    print(f"持仓: {len(mgr.holdings)} 只")
    print(f"买入信息: {'完整' if has_entry else '缺失（仅信号监控）'}")
    
    # 分析每只
    results = []
    for i, h in enumerate(mgr.holdings):
        code = h['code']
        name = h.get('name', code)
        print(f"\r[{i+1}/{len(mgr.holdings)}] {code} {name}...", end='', flush=True)
        
        entry = None
        if has_entry:
            entry = {
                'entry_date': h.get('entry_date'),
                'entry_price': h.get('entry_price'),
                'reason': h.get('reason', ''),
                'stop_price': h.get('stop_price'),
                'tp_price': h.get('tp_price'),
            }
        
        result = analyze_stock(code, name, entry)
        
        # 补充代码/名称
        result['code'] = code
        result['name'] = name if name else result.get('name', code)
        
        results.append(result)
    
    print()
    
    # 打印快速汇总
    print("\n快速汇总:")
    for r in results:
        status_icon = {'OK': '🟢', 'SELL': '🔴', 'STOP': '🛑', 'TP': '🎯', 'ERROR': '⚠️'}.get(r['status'], '❓')
        pnl_str = f" {r['pnl_pct']:+.2f}%" if r.get('pnl_pct') is not None else ""
        price_str = f"¥{r['current_price']:.2f}" if r['current_price'] else '-'
        print(f"  {status_icon} {r['code']} {r['name']:<8} {price_str}{pnl_str} | {r.get('macd_status','-')} | {r.get('signal_summary','-')}")
    
    # 生成报告
    report = generate_report(mgr, results)
    
    output_path = args.output or os.path.join(
        REPORT_DIR, f"{datetime.now().strftime('%Y-%m-%d')}_持仓日报.md"
    )
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n[报告] {output_path}")
    
    # 推送
    if args.push or args.alert_only:
        try:
            from weixin_pusher import WeixinPusher
            pusher = WeixinPusher()
            
            if args.alert_only:
                # 仅在有 CRITICAL/HIGH 告警时推送
                critical_alerts = [
                    (r, a) for r in results
                    for a in r.get('alerts', [])
                    if a['level'] in ('CRITICAL', 'HIGH')
                ]
                if critical_alerts:
                    alert_lines = ["[ALERT] 持仓风险告警", ""]
                    for r, a in critical_alerts:
                        level_icon = {'CRITICAL': '!', 'HIGH': '!!'}.get(a['level'], '?')
                        alert_lines.append(f"[{level_icon}] {r['name']}({r['code']}) — {a['message']}")
                    alert_lines.append(f"\n{datetime.now().strftime('%Y-%m-%d %H:%M')}")
                    pusher.send("\n".join(alert_lines))
                    print(f"\n[推送] 告警已推送（{len(critical_alerts)}条）")
                else:
                    print("\n[推送] --alert-only: 无告警，跳过推送")
            else:
                # --push: 推送完整持仓日报
                pusher.send(report)
                print(f"\n[推送] 持仓日报已推送")
        except Exception as e:
            print(f"\n[推送失败] {e}")
    
    print("\n完毕。")
