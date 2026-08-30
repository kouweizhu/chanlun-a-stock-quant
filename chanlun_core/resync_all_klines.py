"""
resync_all_klines.py - 全量重算 K线前复权数据 (定期修正基准漂移 + 同步 Parquet 缓存)

背景: 存储的是前复权(adjustflag='2'), 其历史价格以最新交易日为基准。
      个股除权除息后, 全部历史前复权值会重算, 纯增量追加会导致历史段基准错位。
      本脚本对股票池内每只股票在其【现有窗口内】重新拉取前复权K线:
      a) 过滤停牌零成交、日期截断到日后覆盖写入主库(只修基准、不扩范围);
      b) 用同一份 Baostock 原始行(完整时间戳、不过滤)整文件覆写
         data_cache/{code}_{level}.parquet —— 与 data_manager 写缓存规范一致。
      ⚠ 缓存必须用原始行直写; 从主库回读生成会丢日内时序(P0-6 禁止)。

用法:
    python resync_all_klines.py                     # 默认: 起点=AUTO(逐股对齐现库最早日)
    python resync_all_klines.py --test              # 仅前3只/表 真实覆盖写入验证
    python resync_all_klines.py --dry-run           # 不写库不写缓存, 仅打印统计
    python resync_all_klines.py --start 2015-01-01  # 强制统一起点(覆盖AUTO)
    python resync_all_klines.py --force             # 跳过行数守卫强制覆盖

安全设计:
    1. 先拉到数据才删旧; 拉取失败/空结果一律保留旧数据;
    2. 每股事务原子(DELETE+INSERT+commit, 异常回滚);
    3. 行数守卫: 新行数 < 旧行数*GUARD_RATIO 判为异常拉取, 自动跳过并记录(--force越过);
    4. 每 PROGRESS_EVERY 只向 logs/resync_all.log 追加进度行, 中途被杀也可追溯;
    5. DB 口径: 停牌零成交行(volume<=0)不入库; 缓存口径: 与 data_manager 一致不过滤;
    6. Parquet 写失败不影响主库(次级层), 记入 [PQ-FAIL] 与汇总;
    7. 非交易日无新增属正常; 推送通道不可用自动跳过, 不影响数据。

版本指纹(供调度方校验, 防文件被旧版覆盖):
    DEFAULT_START = "AUTO" 且含 GUARD_RATIO 与 vol <= 0 过滤 —— 缺任一即为被回退的旧版。
"""

import os
import sys
import sqlite3
import time
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from baostock_utils import to_bs_code, login, logout, query_with_retry
from parquet_cache_sync import write_full

# dbhub 当前主库 (与 incremental_kline_sync.py 一致); Parquet 缓存目录与其同级
DB_PATH = r"D:\常用文件\DeepSeek Harness项目\trading-skills\chanlun_core\data_cache\chanlun_klines.db"
CACHE_DIR = os.path.dirname(DB_PATH)
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "resync_all.log")

ADJUST_FLAG = "2"          # 前复权, 与存量一致
FIELDS = "date,open,high,low,close,volume"
SLEEP_PER_STOCK = 0.12     # 限速, 避免 Baostock 限频
TEST_LIMIT = 3
DEFAULT_START = "AUTO"     # AUTO=逐股对齐现库MIN(date); 或传具体日期强制统一起点
GUARD_RATIO = 0.90         # 行数守卫阈值: 新 < 旧*RATIO 视为异常拉取
PROGRESS_EVERY = 50        # 每处理多少只写一行进度日志


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_pool(conn, table):
    """返回 [(stock_code, 现库最早日期), ...]: 重算起点逐股对齐现有窗口"""
    return [(r[0], r[1]) for r in conn.execute(
        f"SELECT stock_code, MIN(date) FROM {table} GROUP BY stock_code ORDER BY stock_code"
    ).fetchall()]


def fetch_kline_raw(bs, code6, level, start_date, end_date):
    """拉取单只股票历史 K线原始行(不过滤、保留完整时间戳)。

    返回 [(date_full,o,h,l,c,vol), ...]; 行序=Baostock 返回顺序(时间升序)。
    主库行与缓存行都由这份原始数据派生, 保证两层同源。
    """
    bs_code = to_bs_code(code6)
    freq = "d" if level == "daily" else "30"

    def _q():
        return bs.query_history_k_data_plus(
            code=bs_code, frequency=freq,
            start_date=start_date, end_date=end_date,
            fields=FIELDS, adjustflag=ADJUST_FLAG)

    rs = query_with_retry(bs, _q)
    if rs is None or not hasattr(rs, "next"):
        return []

    out = []
    while rs.next():
        r = rs.get_row_data()
        if not r or len(r) != 6:
            continue
        d = str(r[0]).strip()
        if not d:
            continue
        try:
            vol = int(float(r[5])) if r[5] not in ("", None) else 0
            out.append((d, float(r[1]), float(r[2]), float(r[3]), float(r[4]), vol))
        except (ValueError, TypeError):
            continue
    return out


def log_line(msg):
    """追加一行日志(立即落盘, 不等结尾) - 中途被杀也可追溯进度"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def resync_table(conn, bs, level, table, stocks, start_mode, end_date, force=False, dry_run=False):
    """单表全量重算: 先拉后覆盖主库 + 直写 Parquet 缓存; 守卫拦截异常缩水; 进度落盘"""
    total = pq_ok = 0
    failures, skipped, pq_fail = [], [], []
    n = len(stocks)
    log_line(f"[{table}] 开始: 共{n}只, start={start_mode}")
    for i, (code, own_min) in enumerate(stocks, 1):
        try:
            start_date = own_min if (start_mode == "AUTO" and own_min) else start_mode
            raw = fetch_kline_raw(bs, code, level, start_date, end_date)
            # 主库行: 过滤停牌零成交 + 截断日期(库内口径); 行序仍为时间升序
            db_rows = [(code, d[:10], o, h, l, c, v) for (d, o, h, l, c, v) in raw if v > 0]
            if not db_rows:
                continue  # 无有效数据不删, 保留旧
            old_n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE stock_code=?", (code,)
            ).fetchone()[0]
            if not force and len(db_rows) < old_n * GUARD_RATIO:
                skipped.append((code, len(db_rows), old_n))
                log_line(f"[GUARD] {table} {code}: 新{len(db_rows)} < 旧{old_n}x{GUARD_RATIO}, 跳过保留旧")
                continue
            if dry_run:
                total += len(db_rows)
                print(f"  [DRY] {code} {level}: 库{len(db_rows)}行(旧{old_n}) "
                      f"+ 缓存{len(raw)}行 ({start_date}~{end_date})")
                continue
            # 关键: 先拉到且通过守卫, 才覆盖; 此前的 continue 都保留了旧数据
            conn.execute(f"DELETE FROM {table} WHERE stock_code=?", (code,))
            conn.executemany(
                f"INSERT INTO {table} (stock_code,date,open,high,low,close,volume) "
                f"VALUES (?,?,?,?,?,?,?)",
                db_rows,
            )
            conn.commit()
            total += len(db_rows)
            # 缓存同步: 用同一份原始行直写(完整时间戳、含停牌行), 失败不影响主库
            ok, _n_pq, note = write_full(code, level, raw, CACHE_DIR)
            if ok:
                pq_ok += 1
            else:
                pq_fail.append((code, note))
                log_line(f"[PQ-FAIL] {table} {code}: {note}")
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            failures.append((code, str(e)[:120]))
        time.sleep(SLEEP_PER_STOCK)
        if i % PROGRESS_EVERY == 0:
            log_line(f"[PROGRESS] {table} {i}/{n} 写库{total}行 缓存{pq_ok}只 "
                     f"失败{len(failures)} 守护{len(skipped)}")
    log_line(f"[{table}] 结束: 写库{total}行, 缓存{pq_ok}只, "
             f"失败{len(failures)}, 守护跳过{len(skipped)}, 缓存失败{len(pq_fail)}")
    return {"total": total, "fail": failures, "skip": skipped,
            "pq_ok": pq_ok, "pq_fail": pq_fail}


def build_message(d, m, elapsed):
    lines = [
        f"[RESYNC] K线全量重算 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"日线:   写库 {d['total']} 行, 失败 {len(d['fail'])}, 守护跳过 {len(d['skip'])}"
        f"; 缓存 {d['pq_ok']} 只, 缓存失败 {len(d['pq_fail'])}",
        f"30min: 写库 {m['total']} 行, 失败 {len(m['fail'])}, 守护跳过 {len(m['skip'])}"
        f"; 缓存 {m['pq_ok']} 只, 缓存失败 {len(m['pq_fail'])}",
        f"耗时:  {elapsed}s",
    ]
    if d["skip"]:
        lines.append("日线守护跳过: " + ", ".join(f"{c}(新{a}/旧{b})" for c, a, b in d["skip"][:10]))
    if m["skip"]:
        lines.append("30min守护跳过: " + ", ".join(f"{c}(新{a}/旧{b})" for c, a, b in m["skip"][:10]))
    if d["pq_fail"]:
        lines.append("日线缓存失败: " + ", ".join(f"{c}({n})" for c, n in d["pq_fail"][:10]))
    if m["pq_fail"]:
        lines.append("30min缓存失败: " + ", ".join(f"{c}({n})" for c, n in m["pq_fail"][:10]))
    if d["fail"]:
        lines.append("日线失败: " + ", ".join(f"{c}({msg})" for c, msg in d["fail"][:10]))
    if m["fail"]:
        lines.append("30min失败: " + ", ".join(f"{c}({msg})" for c, msg in m["fail"][:10]))
    return "\n".join(lines)


def write_log(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n" + "=" * 40 + "\n")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="仅前3只/表验证")
    parser.add_argument("--dry-run", action="store_true", help="不写库不写缓存, 仅打印统计")
    parser.add_argument("--force", action="store_true", help="跳过行数守卫强制覆盖")
    parser.add_argument("--start", default=DEFAULT_START,
                        help="AUTO=逐股对齐现库MIN(date)(默认); 或指定统一起点如 2015-01-01")
    args = parser.parse_args()

    os.chdir(SCRIPT_DIR)
    start_mode = (args.start or "AUTO").upper()
    start_ts = datetime.now()
    end_date = datetime.now().strftime("%Y-%m-%d")
    print(f"[START] {start_ts}  DB={DB_PATH}  CACHE={CACHE_DIR}  start={start_mode} end={end_date}")

    conn = get_conn()
    d = {"total": 0, "fail": [], "skip": [], "pq_ok": 0, "pq_fail": []}
    m = {"total": 0, "fail": [], "skip": [], "pq_ok": 0, "pq_fail": []}
    try:
        bs, _ = login()
        d_codes = get_pool(conn, "kline_daily")
        m_codes = get_pool(conn, "kline_30min")
        print(f"[POOL] daily={len(d_codes)} 30min={len(m_codes)}")
        if args.test:
            d_codes = d_codes[:TEST_LIMIT]
            m_codes = m_codes[:TEST_LIMIT]
        d = resync_table(conn, bs, "daily", "kline_daily", d_codes,
                         start_mode, end_date,
                         force=args.force, dry_run=args.dry_run)
        m = resync_table(conn, bs, "30min", "kline_30min", m_codes,
                         start_mode, end_date,
                         force=args.force, dry_run=args.dry_run)
    except Exception as e:
        import traceback
        print("[FATAL] " + traceback.format_exc())
        d["fail"].append(("FATAL", str(e)[:120]))
    finally:
        try:
            logout()
        except Exception:
            pass
        conn.close()

    elapsed = int((datetime.now() - start_ts).total_seconds())
    msg = build_message(d, m, elapsed)
    print(msg)
    write_log(msg)


if __name__ == "__main__":
    main()
