"""
DBHub 适配器 — 将 DBHub SQLite K 线数据转为 Alpha Zoo 的宽表 panel 格式。

panel 格式约定：
    dict[str, pd.DataFrame]  key=列名(open/high/low/close/volume)
    每个 value 是宽表: index=日期(DatetimeIndex), columns=股票代码(str)

用法：
    from dbhub_panel import load_panel
    panel = load_panel(["000001.SZ", "000002.SZ", ...], "2023-01-01", "2024-12-31")
    from zoo import compute
    factor = compute("gtja191_171", panel)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd


# DBHub 数据库路径 — 可配置
DBHUB_PATH = Path(r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core") / "data_cache" / "chanlun_klines.db"


def load_panel(
    stock_codes: list[str],
    start_date: str,
    end_date: str,
    db_path: Optional[str] = None,
    table: str = "kline_daily",
) -> dict[str, pd.DataFrame]:
    """从 DBHub 读取数据，构建 Alpha Zoo 所需的宽表 panel。

    Args:
        stock_codes: 股票代码列表，如 ["000001.SZ", "600519.SH"]
        start_date: 起始日期 "YYYY-MM-DD"
        end_date: 截止日期 "YYYY-MM-DD"
        db_path: SQLite 数据库路径，默认 ~/work/chanlun_core/data/kline.db
        table: 表名，默认 kline_daily

    Returns:
        panel: {col_name: wide_DataFrame} 可直接传入 zoo.compute()

    Raises:
        FileNotFoundError: 数据库文件不存在
        ValueError: 查询无数据
    """
    path = Path(db_path) if db_path else DBHUB_PATH
    if not path.exists():
        raise FileNotFoundError(f"DBHub 数据库不存在: {path}")

    if not stock_codes:
        raise ValueError("stock_codes 不能为空")

    # 用参数化查询避免 SQL 注入
    placeholders = ",".join("?" * len(stock_codes))
    sql = f"""
    SELECT date, stock_code, open, high, low, close, volume
    FROM {table}
    WHERE stock_code IN ({placeholders})
      AND date BETWEEN ? AND ?
    ORDER BY date, stock_code
    """
    params = [*stock_codes, start_date, end_date]

    conn = sqlite3.connect(str(path))
    try:
        df = pd.read_sql(sql, conn, params=params, parse_dates=["date"])
    finally:
        conn.close()

    if df.empty:
        raise ValueError(
            f"查询无数据: {len(stock_codes)} 只股票, "
            f"{start_date}~{end_date}"
        )

    # 构建宽表 panel
    panel = {}
    for col in ["open", "high", "low", "close", "volume"]:
        panel[col] = df.pivot(index="date", columns="stock_code", values=col)
        panel[col].index.name = None  # 去掉 index 名称

    # 确保 date 是 DatetimeIndex 且已排序
    for col in panel:
        panel[col].index = pd.to_datetime(panel[col].index)
        panel[col].sort_index(inplace=True)

    return panel


def get_stock_codes(
    db_path: Optional[str] = None,
    table: str = "kline_daily",
    limit: int = 50,
    min_days: int = 200,
) -> list[str]:
    """获取 DBHub 中可用的股票代码列表，按数据天数排序。

    Args:
        db_path: 数据库路径
        table: 表名
        limit: 最多返回数量
        min_days: 最少要有多少天数据

    Returns:
        stock_codes: 股票代码列表
    """
    path = Path(db_path) if db_path else DBHUB_PATH
    if not path.exists():
        raise FileNotFoundError(f"数据库不存在: {path}")

    conn = sqlite3.connect(str(path))
    try:
        sql = f"""
        SELECT stock_code, COUNT(*) as cnt
        FROM {table}
        GROUP BY stock_code
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ?
        """
        rows = conn.execute(sql, (min_days, limit)).fetchall()
    finally:
        conn.close()

    return [r[0] for r in rows]