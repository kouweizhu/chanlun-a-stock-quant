"""
incremental_kline_sync.py - 每日增量同步 Baostock K线 -> 主库 + Parquet 缓存 (双写)

功能:
    每个交易日 21:30 由 DSH 定时自动化触发，增量拉取 Baostock 的
    日线(daily) + 30分钟(30min) K线:
      a) 过滤停牌零成交、截断日期后覆盖写入主库(幂等: DELETE [起点,今天] 再 INSERT);
      b) 写库成功后用【同一份原始行】调用 parquet_cache_sync.merge_incremental()
         合并刷新 data_cache/{code}_{level}.parquet —— 与 data_manager 写缓存规范一致。
         ⚠ 严禁从 SQLite 回读生成缓存(P0-6 红线: 会丢失日内行序)。

设计要点:
    1. 股票池取自库内现有 DISTINCT stock_code (daily 517 / 30min 243 各自增量)。
    2. 复权固定 adjustflag='2' (前复权)，与存量数据保持一致。
    3. 口径统一(v5.5): 主库行过滤停牌零成交(volume<=0 不入库); 缓存行不过滤,
       与 data_manager 写缓存的既有惯例一致——两层各自与其存量口径对齐。
    4. 幂等覆盖式写库: 对每只股票计算起点(MAX(date)+1; 今日已写过则重拉今天),
       DELETE [起点, 今天] 再 INSERT，重跑可修正当日不完整数据、不堆重复。
    5. 缓存合并守卫(在 merge_incremental 内): 文件缺失/损坏跳过留给管线重建;
       新旧间隔>10个自然日视为缺口跳过(陈旧但连续好过有洞)。
    6. 单只失败不影响整体; 缓存失败/跳过记 [PQ-FAIL]/[PQ-GAP], 不影响主库结果。

用法:
    python incremental_kline_sync.py            # 全量增量同步(库+缓存) + 微信推送
    python incremental_kline_sync.py --test     # 仅前3只/表 验证, 推送标[测试]
    python incremental_kline_sync.py --dry-run  # 不写库不写缓存不推送, 仅打印统计

版本指纹(供调度方校验, 防文件被旧版覆盖):
    含 `from parquet_cache_sync import ... merge_incremental` 与 `vol > 0` 过滤
    —— 缺任一即为被回退的旧版(无缓存双写/无停牌过滤)。
"""

import os
import sys
import sqlite3
import time
import argparse
from datetime import datetime, timedelta

# 确保同目录模块可 import (baostock_utils / weixin_pusher 均依赖 date_utils)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from baostock_utils import to_bs_code, login, logout, query_with_retry
from parquet_cache_sync import merge_incremental


def _load_hermes_env():
    """独立 python 进程不继承 Hermes 的 WEIXIN_* 环境变量, 从 ~/.hermes/.env 加载作为 fallback。
    必须在 weixin_pusher 实例化(读取 token)之前执行, 否则推送会因 token 缺失失败。"""
    try:
        from pathlib import Path
        envf = Path.home() / ".hermes" / ".env"
        if envf.exists():
            for line in envf.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k.startswith("WEIXIN_") and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


_load_hermes_env()


def _push_available():
    """探测 Hermes 推送通道(gateway 包)是否可用; 独立 python 环境通常不可用。"""
    try:
        from gateway.platforms.weixin import check_weixin_requirements, send_weixin_direct
        return True
    except Exception:
        return False


PUSH_ENABLED = _push_available()
from weixin_pusher import wx_send

# ===== 唯一主库 = pipeline 同目录 data_cache 下的 SQLite (v5.3, 2026-08-22) =====
# 历史变更: 原 C:\Users\13120\chanlun-quant\data_cache\chanlun_klines.db 已弃用，
# 其全部独有数据已于 2026-08-22 合并入本库(_merge_cdb_into_d.py)。dbhub MCP 的
# --dsn 已同步指向本库。Parquet 缓存目录与主库同级(data_manager 约定)。
DB_PATH = os.path.join(SCRIPT_DIR, "data_cache", "chanlun_klines.db")
CACHE_DIR = os.path.dirname(DB_PATH)
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "incremental_sync.log")

ADJUST_FLAG = "2"          # 前复权，与存量一致
FIELDS = "date,open,high,low,close,volume"
SLEEP_PER_STOCK = 0.08     # 单只之间间隔，规避 Baostock 限频
TEST_LIMIT = 3             # --test 模式每只表处理的股票数


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_pool(conn: sqlite3.Connection, table: str):
    """返回该表内现有 DISTINCT stock_code 列表 (6位代码)"""
    rows = conn.execute(
        f"SELECT DISTINCT stock_code FROM {table} ORDER BY stock_code"
    ).fetchall()
    return [r[0] for r in rows]


def get_max_date(conn: sqlite3.Connection, table: str, code: str):
    cur = conn.execute(
        f"SELECT MAX(date) FROM {table} WHERE stock_code=?", (code,)
    ).fetchone()
    return cur[0]  # str 'YYYY-MM-DD' 或 None


def fetch_kline_raw(bs, code6: str, level: str, start_date: str, end_date: str):
    """拉取单只股票区间 K线原始行(不过滤、保留完整时间戳)。

    返回 [(date_full,o,h,l,c,vol), ...]; 行序=Baostock 返回顺序(时间升序)。
    库行与缓存行都由这份原始数据派生, 保证两层同源(同源成对写入原则)。
    """
    bs_code = to_bs_code(code6)
    freq = "d" if level == "daily" else "30"

    def _q():
        return bs.query_history_k_data_plus(
            code=bs_code,
            frequency=freq,
            start_date=start_date,
            end_date=end_date,
            fields=FIELDS,
            adjustflag=ADJUST_FLAG,
        )

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


def sync_table(conn, bs, level, table, codes, dry_run=False):
    """对单表做增量同步(库+缓存双写)，返回 stats dict"""
    total = pq_ok = 0
    failures, pq_fail, pq_gap = [], [], []
    today = datetime.now().strftime("%Y-%m-%d")

    for code in codes:
        try:
            max_date = get_max_date(conn, table, code)
            if max_date is None:
                # 理论上池内股票都应有数据；兜底全量
                start = "2010-01-01"
            elif max_date == today:
                # 今天已写过(可能不全) -> 重新拉今天覆盖修正
                start = today
            else:
                start = (datetime.strptime(max_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

            if start > today:
                continue

            raw = fetch_kline_raw(bs, code, level, start, today)
            # 库行: 过滤停牌零成交 + 截断日期(v5.5 口径统一); 行序仍为时间升序
            db_rows = [(code, d[:10], o, h, l, c, v) for (d, o, h, l, c, v) in raw if v > 0]
            if not db_rows:
                continue  # 非交易日/停牌无新数据，不动库不动缓存

            if dry_run:
                total += len(db_rows)
                print(f"  [DRY] {code} {level}: 库+{len(db_rows)}行 缓存合并{len(raw)}行 ({start}~{today})")
                continue

            # 覆盖式写入: 删除增量区间再插入
            conn.execute(
                f"DELETE FROM {table} WHERE stock_code=? AND date>=?", (code, start)
            )
            conn.executemany(
                f"INSERT INTO {table} (stock_code,date,open,high,low,close,volume) "
                f"VALUES (?,?,?,?,?,?,?)",
                db_rows,
            )
            conn.commit()
            total += len(db_rows)

            # 缓存合并: 用同一份原始行(完整时间戳、不过滤), 失败/跳过不影响主库
            status, _n, note = merge_incremental(code, level, raw, CACHE_DIR)
            if status == "ok":
                pq_ok += 1
            elif status == "skip":
                pq_gap.append((code, note))
            else:
                pq_fail.append((code, note))
            time.sleep(SLEEP_PER_STOCK)

        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            failures.append((code, str(e)[:120]))

    return {"ins": total, "fail": failures,
            "pq_ok": pq_ok, "pq_fail": pq_fail, "pq_gap": pq_gap}


def build_message(d, m, elapsed, test_mode):
    tag = "[测试] " if test_mode else ""
    lines = [
        f"{tag}[SYNC] K线增量同步 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"日线:   +{d['ins']} 行, 失败 {len(d['fail'])}; 缓存 {d['pq_ok']} 只, "
        f"跳过 {len(d['pq_gap'])}, 缓存失败 {len(d['pq_fail'])}",
        f"30min: +{m['ins']} 行, 失败 {len(m['fail'])}; 缓存 {m['pq_ok']} 只, "
        f"跳过 {len(m['pq_gap'])}, 缓存失败 {len(m['pq_fail'])}",
        f"耗时:  {elapsed}s",
    ]
    if d["pq_fail"]:
        lines.append("日线缓存失败: " + ", ".join(f"{c}({n})" for c, n in d["pq_fail"][:10]))
    if m["pq_fail"]:
        lines.append("30min缓存失败: " + ", ".join(f"{c}({n})" for c, n in m["pq_fail"][:10]))
    if d["fail"]:
        lines.append("日线失败: " + ", ".join(f"{c}({msg})" for c, msg in d["fail"][:10]))
    if m["fail"]:
        lines.append("30min失败: " + ", ".join(f"{c}({msg})" for c, msg in m["fail"][:10]))
    if d["ins"] == 0 and m["ins"] == 0 and not d["fail"] and not m["fail"]:
        lines.append("（今日无新交易日数据）")
    return "\n".join(lines)


def write_log(msg: str):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n" + "=" * 40 + "\n")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="K线增量同步(库+缓存双写)")
    parser.add_argument("--test", action="store_true", help="仅前3只/表验证")
    parser.add_argument("--dry-run", action="store_true", help="不写库不写缓存不推送")
    args = parser.parse_args()

    os.chdir(SCRIPT_DIR)
    start_ts = datetime.now()
    print(f"[START] {start_ts}  DB={DB_PATH}  CACHE={CACHE_DIR}")

    conn = get_conn()
    d_stats = {"ins": 0, "fail": [], "pq_ok": 0, "pq_fail": [], "pq_gap": []}
    m_stats = {"ins": 0, "fail": [], "pq_ok": 0, "pq_fail": [], "pq_gap": []}

    try:
        bs, _ = login()
        daily_codes = get_pool(conn, "kline_daily")
        m30_codes = get_pool(conn, "kline_30min")
        print(f"[POOL] daily={len(daily_codes)} 30min={len(m30_codes)}")

        if args.test:
            daily_codes = daily_codes[:TEST_LIMIT]
            m30_codes = m30_codes[:TEST_LIMIT]

        d_stats = sync_table(conn, bs, "daily", "kline_daily", daily_codes, args.dry_run)
        m_stats = sync_table(conn, bs, "30min", "kline_30min", m30_codes, args.dry_run)
    except Exception as e:
        import traceback
        print("[FATAL] " + traceback.format_exc())
        d_stats["fail"].append(("FATAL", str(e)[:120]))
    finally:
        try:
            logout()
        except Exception:
            pass
        conn.close()

    elapsed = int((datetime.now() - start_ts).total_seconds())
    msg = build_message(d_stats, m_stats, elapsed, args.test)
    print(msg)
    write_log(msg)

    if not args.dry_run:
        if not PUSH_ENABLED:
            print("[PUSH] 跳过: 本机缺少 Hermes gateway 包，微信推送通道不可用 (数据同步不受影响)")
        else:
            try:
                ok = wx_send(msg)
                print(f"[PUSH] 微信推送{'成功' if ok else '失败(不影响数据)'}")
            except Exception as e:
                print(f"[PUSH] 微信推送失败(不影响数据): {e}")


if __name__ == "__main__":
    main()
