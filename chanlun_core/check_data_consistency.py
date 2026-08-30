"""
check_data_consistency.py - 主库(SQLite) 与 Parquet 缓存 一致性抽检工具

用途:
    手动诊断"同源成对写入"体系下两层数据的健康度。
    主库口径: 过滤停牌零成交行(volume<=0 不入库);
    缓存口径: 不过滤(data_manager 惯例) —— 因此"缓存比库多出零成交行"是预期差异。

判定规则(逐股逐层):
    [OK]    范围与行数完全一致
    [INFO]  仅行数不同 / 缓存尾部多出的行全部 volume<=0(停牌行) / 缓存历史比库更深
            —— 两层口径或窗口差异, 属预期, 无需处理
    [WARN]  缓存落后于库(max 更早) / 缓存缺头部(min 更晚) / 尾部多出行中含非零成交
            / 文件缺失 / 文件不可读 —— 需要关注(通常管线读时自愈可修复)

用法:
    python check_data_consistency.py                 # 每表随机抽40只(deterministic)
    python check_data_consistency.py --sample 100
    python check_data_consistency.py --full          # 全池
    python check_data_consistency.py --level daily   # 只查日线 (默认 both)
    python check_data_consistency.py --strict        # 有 WARN 时退出码 2
"""

import os
import sys
import sqlite3
import random
import argparse

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "data_cache", "chanlun_klines.db")
CACHE_DIR = os.path.join(SCRIPT_DIR, "data_cache")

TABLES = {"daily": "kline_daily", "30min": "kline_30min"}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def db_stats(conn, table, codes):
    """一次查询取整批股票的 (min,max,count)"""
    out = {}
    q = ("SELECT stock_code, MIN(date), MAX(date), COUNT(*) FROM {} "
         "WHERE stock_code IN ({}) GROUP BY stock_code")
    marks = ",".join("?" * len(codes))
    for r in conn.execute(q.format(table, marks), codes):
        out[r[0]] = (r[1], r[2], r[3])
    return out


def pq_stats(code, level):
    """读取单股缓存, 返回 (min,max,count,tail_nonzero, status)"""
    path = os.path.join(CACHE_DIR, f"{code}_{level}.parquet")
    if not os.path.exists(path):
        return None, None, 0, False, "missing"
    try:
        df = pd.read_parquet(path)
        if df.empty or "date" not in df.columns:
            return None, None, 0, False, "bad"
        d10 = df["date"].astype(str).str.slice(0, 10)
        return d10.min(), d10.max(), len(df), False, "ok"
    except Exception as e:
        return None, None, 0, False, f"error:{type(e).__name__}"


def pq_extra_tail_nonzero(code, level, db_max):
    """检查缓存超出 db_max 的尾部行是否全为停牌零成交行"""
    path = os.path.join(CACHE_DIR, f"{code}_{level}.parquet")
    try:
        df = pd.read_parquet(path)
        d10 = df["date"].astype(str).str.slice(0, 10)
        tail = df[d10 > db_max]
        if tail.empty:
            return True
        return bool((tail["volume"].astype(float) <= 0).all())
    except Exception:
        return False


def classify(db, pq, code, level):
    """db=(min,max,cnt) / pq=(min,max,cnt,status) -> (tag, detail)"""
    pmin, pmax, pcnt, _, status = pq
    if status == "missing":
        return "MISS", "无缓存文件"
    if status == "bad":
        return "WARN", "缓存空/损坏"
    if status.startswith("error"):
        return "WARN", status
    dmin, dmax, dcnt = db
    if pmin == dmin and pmax == dmax:
        if pcnt == dcnt:
            return "OK", ""
        return "INFO", f"行数差{pcnt - dcnt}(停牌行口径)"
    notes = []
    if pmin != dmin:
        notes.append("min 库{}缓{}".format(dmin, pmin) +
                     ("(缓存更深)" if pmin < dmin else "(缓存缺头部)"))
    if pmax != dmax:
        if pmax > dmax:
            ok_zero = pq_extra_tail_nonzero(code, level, dmax)
            notes.append(f"max 库{dmax}缓{pmax}" +
                         ("(尾部零成交)" if ok_zero else "(尾部含有效K线!)"))
        else:
            notes.append(f"max 库{dmax}缓{pmax}(缓存落后)")
    # 出现任一真问题关键词即 WARN; 其余范围差异均为口径/窗口性 INFO
    if any(k in n for n in notes for k in ("缺头部", "落后", "有效K线")):
        return "WARN", "; ".join(notes)
    return "INFO", "; ".join(notes)


def check_level(conn, level, sample_n, full, verbose):
    table = TABLES[level]
    pool = [r[0] for r in conn.execute(
        f"SELECT DISTINCT stock_code FROM {table} ORDER BY stock_code").fetchall()]
    picks = pool if full else random.Random(42).sample(pool, min(sample_n, len(pool)))
    picks.sort()
    stats_map = db_stats(conn, table, picks)
    print(f"\n===== {level} ({table}) 抽检 {len(picks)}/{len(pool)} 只 =====")
    counts = {"OK": 0, "INFO": 0, "WARN": 0, "MISS": 0}
    warn_items = []
    for code in picks:
        db = stats_map.get(code)
        pq = pq_stats(code, level)
        if db is None:
            tag, detail = "WARN", "库内无该股?"
            pshow = "-"
        else:
            tag, detail = classify(db, pq, code, level)
            pshow = f"缓存[{pq[0]}~{pq[1]}]n={pq[2]}"
        counts[tag] = counts.get(tag, 0) + 1
        if verbose or tag in ("WARN", "MISS"):
            print(f"[{tag}] {code}: 库[{db[0]}~{db[1]}]n={db[2]} | {pshow}"
                  + (f" | {detail}" if detail else ""))
        if tag in ("WARN", "MISS"):
            warn_items.append((code, detail))
    print(f"-- {level} 小结: OK={counts['OK']} INFO={counts['INFO']} "
          f"WARN={counts['WARN']} MISS={counts['MISS']}")
    return warn_items


def main():
    ap = argparse.ArgumentParser(description="主库 vs Parquet 缓存一致性抽检")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--level", choices=["daily", "30min", "both"], default="both")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="打印每只结果(默认只打异常)")
    ap.add_argument("--strict", action="store_true", help="存在WARN时退出码2")
    args = ap.parse_args()

    levels = ["daily", "30min"] if args.level == "both" else [args.level]
    conn = get_conn()
    all_warn = []
    try:
        for lv in levels:
            all_warn += [(lv, c, d) for c, d in check_level(
                conn, lv, args.sample, args.full, args.verbose)]
    finally:
        conn.close()

    print("\n===== 总结 =====")
    if all_warn:
        for lv, c, d in all_warn[:20]:
            print(f"[WARN] {lv} {c}: {d}")
        print(f"共 {len(all_warn)} 项需关注。提示: 缓存落后类问题会被管线"
              f"读时自愈(get_klines 增量探测), 无需手工修。")
        sys.exit(2 if args.strict else 0)
    print("全部抽检通过(允许 INFO 级口径差异)。")
    sys.exit(0)


if __name__ == "__main__":
    main()
