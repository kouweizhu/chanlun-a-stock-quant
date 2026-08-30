"""
risk_filter.py — 7项黑名单风控过滤器

自动检测（基于 AKShare 数据）：
  1. ST股
  2. 资不抵债（资产负债率 > 100% 或 每股净资产 < 0）
  3. 连亏3年
  4. 经营现金流近3年中 ≥2年为负
  5. 被立案调查（基于公告搜索）
  6. 非标准审计报告（基于财务摘要审计意见字段）

人工标记（基于 config.yaml manual_blacklist）：
  7. 财务造假 / 频繁换所 / 高商誉 / 高质押 / 管理层动荡

用法:
    from risk_filter import check_risk
    blocked, reasons = check_risk('000002', '万科A')
    if blocked:
        print(f'⛔ 排除: {reasons}')
"""

import sys
from date_utils import date_to_str, parse_date_to_datetime
import os
import re
from datetime import datetime, timedelta
from typing import Tuple, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _parse_pct(val) -> float:
    """解析百分比字符串: '77.13%' → 77.13"""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace('%', '').strip()
    try:
        return float(s)
    except ValueError:
        return 0


def _parse_amount(val) -> float:
    """解析金额字符串: '-59.52亿' → -5952000000, '426.33亿' → 42633000000"""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace(',', '').strip()
    try:
        if '亿' in s:
            return float(s.replace('亿', '')) * 1e8
        if '万' in s:
            return float(s.replace('万', '')) * 1e4
        return float(s)
    except ValueError:
        return 0


# ── v5.3.1(F13): 财务风控降级可见性 ──
# AKShare 失败时资不抵债/连亏/现金流三项检查全部跳过, 下游此前无从得知
# 覆盖率为零(整批静默放行)。模块级标记 + 汇总查询函数。
RISK_CHECK_DEGRADED_CODES = []


def get_risk_check_degraded() -> List[str]:
    """返回本轮因数据获取失败而跳过财务风控的股票代码列表（跨股票累积）"""
    return list(RISK_CHECK_DEGRADED_CODES)


def reset_risk_check_degraded():
    RISK_CHECK_DEGRADED_CODES.clear()


def check_risk(code: str, name: str,
               manual_blacklist: dict = None) -> Tuple[bool, List[str]]:
    """
    执行8项风控检查

    Args:
        code: 股票代码
        name: 股票名称
        manual_blacklist: {code: reason} 人工黑名单（从 config 加载）

    Returns:
        (is_blocked: bool, reasons: List[str])
    """
    reasons = []
    manual_blacklist = manual_blacklist or {}

    # ── 0. 人工黑名单（优先，不需要财务数据）──────
    if code in manual_blacklist:
        reasons.append(manual_blacklist[code])
        return True, reasons

    # ── 1. ST股 ──────────────────────────────────
    if 'ST' in name.upper():
        reasons.append(f'ST股({name})')
        return True, reasons  # ST直接排除，不继续查财务

    # ── 获取财务数据 ──────────────────────────────
    try:
        import akshare as ak
        df = ak.stock_financial_abstract_ths(symbol=code, indicator='按年度')
        if df is None or df.empty or len(df) < 3:
            RISK_CHECK_DEGRADED_CODES.append(code)  # v5.3.1(F13)
            return False, []
    except Exception as e:
        # 数据获取失败：不阻塞（可能是新上市/数据缺失）
        # v5.3.1(F13): 但必须留下降级痕迹——限流日整批跳过=财务风控失效
        print(f"[risk_filter] ⚠ {code} 财务数据获取失败, 跳过资不抵债/连亏检查: {str(e)[:60]}")
        RISK_CHECK_DEGRADED_CODES.append(code)
        return False, []

    latest = df.iloc[-1]
    recent_3y = df.iloc[-3:] if len(df) >= 3 else df

    # ── 2. 资不抵债 ───────────────────────────────
    leverage = _parse_pct(latest.get('资产负债率', 0))
    equity_ps_raw = latest.get('每股净资产', 0)
    try:
        equity_ps = float(equity_ps_raw) if equity_ps_raw else 0
    except (ValueError, TypeError):
        equity_ps = 0

    if leverage > 100 or equity_ps < 0:
        detail = f'负债率{leverage:.1f}%' if leverage > 100 else f'每股净资产{equity_ps:.2f}'
        reasons.append(f'资不抵债({detail})')

    # ── 3. 连亏3年 ─────────────────────────────────
    net_profits = []
    for _, row in recent_3y.iterrows():
        np_val = _parse_amount(row.get('净利润', 0))
        net_profits.append(np_val)

    if len(net_profits) >= 3 and all(np < 0 for np in net_profits):
        years = [str(row['报告期'])[:4] for _, row in recent_3y.iterrows()]
        reasons.append(f'连亏3年({",".join(years)})')

    # ── 4. 经营现金流近3年≥2年为负 ──────────────────
    cf_neg_count = 0
    for _, row in recent_3y.iterrows():
        try:
            cf_val = float(row.get('每股经营现金流', 0) or 0)
        except (ValueError, TypeError):
            cf_val = 0
        if cf_val < 0:
            cf_neg_count += 1

    if cf_neg_count >= 2:
        reasons.append(f'经营现金流负({cf_neg_count}/3年)')

    # ── 5. 被立案调查（搜索公告关键词）────────────
    try:
        import akshare as ak
        # 搜索最近1年的公告
        # v5.0.1 修复：stock_announcement_report 已在 akshare 1.18+ 移除，
        # 改用 stock_individual_notice_report（按个股查询，支持日期范围）
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        if hasattr(ak, 'stock_individual_notice_report'):
            announcements = ak.stock_individual_notice_report(
                security=code, symbol='全部',
                begin_date=start_date, end_date=end_date)
        elif hasattr(ak, 'stock_announcement_report'):
            announcements = ak.stock_announcement_report(symbol=code, start_date=start_date, end_date=end_date)
        else:
            announcements = None
        if announcements is not None and not announcements.empty:
            # 兼容列名：新版可能是 "公告标题"，旧版也是 "公告标题"
            title_col = '公告标题' if '公告标题' in announcements.columns else announcements.columns[0]
            for _, row in announcements.iterrows():
                title = str(row.get(title_col, ''))
                if '立案调查' in title or '被立案' in title or '立案告知' in title:
                    reasons.append(f'被立案调查({title[:20]}...)')
                    break
    except Exception as e:
        print(f'[risk_filter] 警告: 公告查询失败 {code}: {e}')  # 公告接口失败不影响其他检查

    # ── 6. 非标准审计报告───────────────────────
    try:
        # 检查最新的审计意见类型
        audit_opinion = latest.get('审计意见', '')
        if audit_opinion and '标准' not in str(audit_opinion):
            reasons.append(f'非标准审计报告({audit_opinion})')
    except Exception as e:
        print(f'[risk_filter] 警告: 审计意见检查失败 {code}: {e}')

    return (len(reasons) > 0, reasons)
