"""
date_utils.py — 统一的日期解析工具

解决脆弱的 str(date)[:10] 解析方式，提供健壮的日期处理函数。
"""

from datetime import datetime, date, timedelta
import pandas as pd

def parse_date_to_str(dt, fmt='%Y-%m-%d') -> str:
    """
    统一解析各种日期格式为字符串
    
    Args:
        dt: 可以是 datetime对象、date对象、pandas.Timestamp、字符串
        fmt: 输出格式，默认'%Y-%m-%d'
    
    Returns:
        格式化后的日期字符串，解析失败返回空字符串
    """
    if dt is None:
        return ''
    
    try:
        # 已经是datetime或date对象
        if isinstance(dt, (datetime, date)):
            return dt.strftime(fmt)
        
        # pandas Timestamp
        if isinstance(dt, pd.Timestamp):
            return dt.strftime(fmt)
            
        # 字符串
        if isinstance(dt, str):
            # 尝试常见格式
            for fmt_try in ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S']:
                try:
                    return datetime.strptime(dt.strip(), fmt_try).strftime(fmt)
                except ValueError:
                    continue
            # 如果字符串长度>=10，至少返回前10个字符
            if len(dt) >= 10:
                return dt[:10]
            return dt
            
    except Exception as e:
        print(f'[date_utils] 警告: 日期解析失败 {dt}: {e}')
        
    return ''


def parse_date_to_datetime(dt) -> datetime or None:
    """
    解析各种日期格式为datetime对象
    
    Args:
        dt: 可以是 datetime对象、date对象、pandas.Timestamp、字符串
        
    Returns:
        datetime对象，解析失败返回None
    """
    if dt is None:
        return None
        
    try:
        if isinstance(dt, datetime):
            return dt
        if isinstance(dt, date):
            return datetime(dt.year, dt.month, dt.day)
        if isinstance(dt, pd.Timestamp):
            return dt.to_pydatetime()
        if isinstance(dt, str):
            for fmt_try in ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S']:
                try:
                    return datetime.strptime(dt.strip(), fmt_try)
                except ValueError:
                    continue
    except Exception as e:
        print(f'[date_utils] 警告: 日期转换失败 {dt}: {e}')
        
    return None


def date_to_str(dt, fmt='%Y-%m-%d') -> str:
    """
    将日期对象转换为字符串（简写函数）
    用法: date_to_str(p.date) 替代 str(p.date)[:10]
    """
    return parse_date_to_str(dt, fmt)


# 常用格式
def date_key(dt) -> str:
    """返回用于字典key的日期字符串，如 '2026-05-04'"""
    return parse_date_to_str(dt, '%Y-%m-%d')


def date_yyyymmdd(dt) -> str:
    """返回YYYYMMDD格式，如 '20260504'"""
    return parse_date_to_str(dt, '%Y%m%d')


def latest_report_dates(today=None):
    """按披露日历推断最新已披露财报期（v5.3.4-D1：替代硬编码 [20260331] 等）。

    A股披露节奏（不考虑延期）：年报1-4月、一季报4月内、中报7-8月、
    三季报10月内。据此：
      1-4月   → (去年年报, 去年三季报)
      5-8月   → (当年一季报, 去年年报)
      9-10月  → (当年中报, 当年一季报)
      11-12月 → (当年三季报, 当年中报)

    Returns:
        (primary, fallback): 形如 ("20260630", "20260331") 的 yyyymmdd 字符串
    """
    t = today or datetime.now()
    y = t.year
    if t.month <= 4:
        return f"{y-1}1231", f"{y-1}0930"
    if t.month <= 8:
        return f"{y}0331", f"{y-1}1231"
    if t.month <= 10:
        return f"{y}0630", f"{y}0331"
    return f"{y}0930", f"{y}0630"


def recent_year_window(n: int = 5, today=None):
    """最近 n 个自然年年份列表，升序（v5.3.4-D1：替代硬编码 [2021..2025]）。

    例：2026-08-23 调用 recent_year_window(5) → [2022, 2023, 2024, 2025, 2026]。
    未来尚未披露的年份查询自然返回空数据，调用方按缺失处理即可。
    """
    t = today or datetime.now()
    return list(range(t.year - n + 1, t.year + 1))


def recent_weekday_keys(n_days: int = 14, today=None):
    """最近 n 天内的**工作日** yyyymmdd 键列表，新日期在前（v5.3.4-D1）。

    用于 iwencai 估值类键（如 市盈率(pe,ttm)[yyyymmdd]）的候选日期——
    该类键按交易日快照存储，节假日无数据；get_val 遍历候选列表即可
    自动跳过非交易日。替代硬编码 [20260514]/[20260513] 这类时间炸弹。
    """
    t = today or datetime.now()
    keys = []
    for i in range(n_days):
        d = t - timedelta(days=i)
        if d.weekday() < 5:  # 周一~周五（法定假日靠"查无数据自动跳过"兜底）
            keys.append(d.strftime("%Y%m%d"))
    return keys
