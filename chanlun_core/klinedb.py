"""K 线 SQLite 数据库 — 供 DBHub 自然语言查询
配合 data_manager.py 使用，每次缓存 Parquet 同时同步写入 SQLite

用法:
  初始化:     python klinedb.py init
  迁移所有:   python klinedb.py migrate
  查询统计:   python klinedb.py stats
  清理旧数据: python klinedb.py vacuum
"""

import os, sys, json
import pandas as pd
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache", "chanlun_klines.db")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    return conn


def init_db():
    """创建表结构"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kline_daily (
            stock_code TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            PRIMARY KEY (stock_code, date)
        );

        CREATE TABLE IF NOT EXISTS kline_30min (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_daily_code_date ON kline_daily(stock_code, date);
        CREATE INDEX IF NOT EXISTS idx_30min_code_date ON kline_30min(stock_code, date);
    """)
    conn.commit()
    conn.close()
    print(f"✅ DB 初始化完成: {DB_PATH}")


def count_rows(conn=None):
    """返回各表行数"""
    close_after = False
    if conn is None:
        conn = get_conn()
        close_after = True
    daily = conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0]
    m30 = conn.execute("SELECT COUNT(*) FROM kline_30min").fetchone()[0]
    stocks_daily = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM kline_daily").fetchone()[0]
    stocks_30 = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM kline_30min").fetchone()[0]
    if close_after:
        conn.close()
    return daily, m30, stocks_daily, stocks_30


def stats():
    """打印数据库统计"""
    conn = get_conn()
    daily, m30, sd, s30 = count_rows(conn)
    conn.close()
    print(f"📊 数据库统计")
    print(f"   kline_daily:   {daily:>8} 行  ({sd} 只股票)")
    print(f"   kline_30min:   {m30:>8} 行  ({s30} 只股票)")
    print(f"   合计:          {daily+m30:>8} 行")
    print(f"   文件大小:       {os.path.getsize(DB_PATH)/1024/1024:.1f} MB")


def migrate_all():
    """将 data_cache/ 中所有 Parquet 文件迁移到 SQLite"""
    conn = get_conn()
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.parquet')]
    daily_files = sorted(f for f in files if 'daily' in f)
    m30_files = sorted(f for f in files if '30min' in f)

    print(f"找到 {len(daily_files)} 个日线文件, {len(m30_files)} 个30分钟文件")

    daily_count = 0
    for fname in daily_files:
        stock_code = fname.replace('_daily.parquet', '')
        df = pd.read_parquet(os.path.join(CACHE_DIR, fname))
        if df.empty:
            continue
        rows = [
            (stock_code, row['date'], row['open'], row['high'],
             row['low'], row['close'], int(row['volume']))
            for _, row in df.iterrows()
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO kline_daily "
            "(stock_code, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        daily_count += len(rows)
        if daily_count % 5000 == 0:
            conn.commit()
            print(f"  日线已迁移 {daily_count} 行...")
    conn.commit()
    print(f"✅ 日线迁移完成: {daily_count} 行")

    m30_count = 0
    for fname in m30_files:
        stock_code = fname.replace('_30min.parquet', '')
        df = pd.read_parquet(os.path.join(CACHE_DIR, fname))
        if df.empty:
            continue
        rows = [
            (stock_code, row['date'], row['open'], row['high'],
             row['low'], row['close'], int(row['volume']))
            for _, row in df.iterrows()
        ]
        conn.executemany(
            "INSERT INTO kline_30min "
            "(stock_code, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        m30_count += len(rows)
        if m30_count % 5000 == 0:
            conn.commit()
            print(f"  30分钟已迁移 {m30_count} 行...")
    conn.commit()
    conn.close()
    print(f"✅ 30分钟迁移完成: {m30_count} 行")
    print(f"🎉 全部完成! 总计 {daily_count + m30_count} 行")


def vacuum():
    conn = get_conn()
    # 清理重复/孤立数据（只保留最近3年）
    cutoff = (pd.Timestamp.now() - pd.Timedelta(days=365*3)).strftime("%Y-%m-%d")
    conn.execute(f"DELETE FROM kline_daily WHERE date < ?", (cutoff,))
    conn.execute(f"DELETE FROM kline_30min WHERE date < ?", (cutoff,))
    conn.execute("VACUUM")
    conn.commit()
    conn.close()
    print(f"✅ 清理完成，保留 {cutoff} 之后的数据")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python klinedb.py [init|migrate|stats|vacuum]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "init":
        init_db()
    elif cmd == "migrate":
        init_db()
        migrate_all()
    elif cmd == "stats":
        stats()
    elif cmd == "vacuum":
        vacuum()
    else:
        print(f"未知命令: {cmd}")