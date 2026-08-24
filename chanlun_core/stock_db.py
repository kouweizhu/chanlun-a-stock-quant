"""SQLite 股票评分追踪数据库 - 替代 Hermes memory
用法:
  初始化:   python stock_db.py init
  写入:     python stock_db.py write <json_string>
  查询:     python stock_db.py query <代码> [limit=5]
  列表:     python stock_db.py list [limit=10]
  趋势:     python stock_db.py trend <代码>
  清理:     python stock_db.py clean <代码> [keep=10]

JSON 写入格式:
  {"stock_code":"000001","stock_name":"平安银行","report_date":"2026-04-29",
   "tech_score":70,"fund_score":63,"news_score":64,"composite_score":65.75,
   "decision":"推荐","position_suggestion":"30%-50%",
   "stock_type_probs":"蓝筹66.7%/成长16.7%/周期16.7%",
   "veto_triggered":0,"core_conflict":"技术面突破但估值高位",
   "observation_points":"4/30一季报验证;跌破28止损",
   "report_path":"~/hermes/profiles/commander/analysis_reports/平安银行_000001_2026-04-29.md"}
"""
import sys, json, os, sqlite3
from date_utils import date_to_str, parse_date_to_datetime
from datetime import datetime

# v5.3.4(A3): 本文件大量输出含 emoji（📈/✅等），GBK 控制台下 print 直接
# UnicodeEncodeError。统一重配为 UTF-8 + replace，不可编码字符降级为 ? 而非崩溃。
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB_PATH = os.path.expanduser("~/.hermes/data/stock_scores.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


_conn_ready = False


def _connect():
    """原始连接（不触发建表），供 init_db 使用避免递归"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_conn():
    """获取连接；首次调用幂等建表（v5.3.4审计A3：查询类命令不再因表缺失崩）"""
    global _conn_ready
    conn = _connect()
    if not _conn_ready:
        init_db()  # CREATE TABLE IF NOT EXISTS，幂等且代价极低
        _conn_ready = True
    return conn


def init_db():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS score_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            report_date TEXT NOT NULL,
            tech_score REAL,
            fund_score REAL,
            news_score REAL,
            composite_score REAL,
            decision TEXT,
            position_suggestion TEXT,
            stock_type_probs TEXT,
            veto_triggered INTEGER DEFAULT 0,
            core_conflict TEXT,
            observation_points TEXT,
            report_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_stock_code_date 
        ON score_history(stock_code, report_date DESC)
    """)
    conn.commit()
    conn.close()
    # v5.3.4(A3): ✅ emoji 在 GBK 控制台会 UnicodeEncodeError（get_conn 幂等建表
    # 后首次暴露）——成功提示改为 ASCII 安全输出
    try:
        print(f"[OK] DB initialized: {DB_PATH}")
    except UnicodeEncodeError:
        pass


# v5.4(B-07): 幽灵键映射表——旧版 SKILL.md 曾文档化错误键名(score/stock_type/
# veto/note), 调用方照文档传参时旧实现静默 .get() 写出整行 NULL 空壳记录。
_KEY_ALIASES = {
    "score": "composite_score",
    "stock_type": "stock_type_probs",
    "veto": "veto_triggered",
    "note": "core_conflict",
}


def write_record(data):
    """写入一条评分记录

    v5.4(B-07) 显式契约:
      ① 缺少 stock_code → 抛 ValueError（旧行为: 静默写 NULL 行）
      ② 识别已知幽灵键并自动映射到正确列 + stderr 告警（老脚本兼容过渡期）
      ③ 其余未知键忽略不计入（SQLite INSERT 为显式列清单, 本就不会写入）
    """
    if isinstance(data, str):
        data = json.loads(data)

    # ① 必需键检查
    if not data.get("stock_code"):
        raise ValueError(
            f"write_record: 缺少必需键 stock_code (收到键: {sorted(data.keys())}); "
            f"正确键名见 score_history 表结构(composite_score 而非 score 等)")

    # ② 幽灵键映射（仅当规范键缺失时生效, 规范键优先）
    _fixed = {}
    for k, v in data.items():
        canonical = _KEY_ALIASES.get(k)
        if canonical and data.get(canonical) is None:
            try:
                sys.stderr.write(f"[stock_db] WARNING: 幽灵键 '{k}' 已自动映射为 "
                                 f"'{canonical}'——请修正调用方键名\n")
            except Exception:
                pass
            if k == "veto" and isinstance(v, bool):
                v = int(v)
            _fixed[canonical] = v
    if _fixed:
        data = {**data, **_fixed}

    conn = get_conn()
    conn.execute("""
        INSERT INTO score_history 
        (stock_code, stock_name, report_date, tech_score, fund_score, 
         news_score, composite_score, decision, position_suggestion,
         stock_type_probs, veto_triggered, core_conflict, observation_points, report_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("stock_code"),
        data.get("stock_name"),
        data.get("report_date", datetime.now().strftime("%Y-%m-%d")),
        data.get("tech_score"),
        data.get("fund_score"),
        data.get("news_score"),
        data.get("composite_score"),
        data.get("decision"),
        data.get("position_suggestion"),
        data.get("stock_type_probs"),
        int(data.get("veto_triggered") or 0),
        data.get("core_conflict"),
        data.get("observation_points"),
        data.get("report_path"),
    ))
    conn.commit()
    conn.close()
    try:
        print(f"[OK] DB write: {data.get('stock_code')} {data.get('report_date')}")
    except UnicodeEncodeError:
        pass


def query_stock(code, limit=5):
    """查询某股票的历史评分"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT report_date, composite_score, tech_score, fund_score, news_score,
               decision, position_suggestion, stock_type_probs, core_conflict
        FROM score_history 
        WHERE stock_code = ?
        ORDER BY report_date DESC
        LIMIT ?
    """, (code, limit)).fetchall()
    conn.close()
    
    if not rows:
        print(f"🔍 {code}: 无历史记录")
        return
    
    print(f"📊 {code} 评分历史 (最近{len(rows)}次):")
    print(f"{'日期':<12} {'综合':>6} {'技术':>6} {'基本面':>6} {'消息面':>6} {'决策':<10} {'仓位':<10}")
    print("-" * 62)
    for r in rows:
        print(f"{r['report_date']:<12} {r['composite_score']:>6.1f} {r['tech_score']:>6.1f} {r['fund_score']:>6.1f} {r['news_score']:>6.1f} {r['decision']:<10} {r['position_suggestion'] or '-':<10}")


def list_recent(limit=10):
    """列出最近记录"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT stock_code, stock_name, report_date, composite_score, decision
        FROM score_history 
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    
    print(f"📋 最近{len(rows)}条记录:")
    print(f"{'代码':<8} {'名称':<10} {'日期':<12} {'综合分':>6} {'决策':<10}")
    print("-" * 46)
    for r in rows:
        print(f"{r['stock_code']:<8} {(r['stock_name'] or ''):<10} {r['report_date']:<12} {r['composite_score']:>6.1f} {r['decision']:<10}")


def show_trend(code):
    """显示评分趋势（含变化量）"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT report_date, composite_score, tech_score, fund_score, news_score, decision
        FROM score_history 
        WHERE stock_code = ?
        ORDER BY report_date ASC
    """, (code,)).fetchall()
    conn.close()
    
    if len(rows) < 2:
        print(f"📈 {code}: 数据不足(仅{len(rows)}条)，无法显示趋势")
        if rows:
            r = rows[0]
            print(f"  最近: {r['report_date']} 综合={r['composite_score']} {r['decision']}")
        return
    
    print(f"📈 {code} 评分趋势:")
    print(f"{'日期':<12} {'综合':>6} {'变化':>7} {'技术':>6} {'基本面':>6} {'消息面':>6} {'决策':<10}")
    print("-" * 59)
    
    prev = None
    for r in rows:
        change = ""
        if prev is not None:
            diff = r['composite_score'] - prev
            change = f"{'+' if diff > 0 else ''}{diff:.1f}"
        print(f"{r['report_date']:<12} {r['composite_score']:>6.1f} {change:>7} {r['tech_score']:>6.1f} {r['fund_score']:>6.1f} {r['news_score']:>6.1f} {r['decision']:<10}")
        prev = r['composite_score']


def clean_records(code, keep=10):
    """保留最近N条记录，删除更旧的"""
    conn = get_conn()
    # 获取第N条记录的ID
    rows = conn.execute("""
        SELECT id FROM score_history 
        WHERE stock_code = ?
        ORDER BY report_date DESC
    """, (code,)).fetchall()
    
    if len(rows) <= keep:
        print(f"  {code}: 仅{len(rows)}条，无需清理")
        conn.close()
        return
    
    keep_ids = [r['id'] for r in rows[:keep]]
    deleted = conn.execute("""
        DELETE FROM score_history 
        WHERE stock_code = ? AND id NOT IN ({})
    """.format(','.join('?' * len(keep_ids))), [code] + keep_ids).rowcount
    conn.commit()
    conn.close()
    print(f"🧹 {code}: 清理{deleted}条旧记录，保留最近{keep}条")


def count_all():
    """统计总记录数和股票数"""
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM score_history").fetchone()[0]
    stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM score_history").fetchone()[0]
    conn.close()
    print(f"📊 总计: {total}条记录, {stocks}只股票")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python stock_db.py init|write|query|list|trend|clean|count")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "init":
        init_db()
    elif cmd == "write":
        if len(sys.argv) < 3:
            print("请提供 JSON 数据")
            sys.exit(1)
        write_record(sys.argv[2])
    elif cmd == "query":
        code = sys.argv[2] if len(sys.argv) > 2 else "000001"
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        query_stock(code, limit)
    elif cmd == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        list_recent(limit)
    elif cmd == "trend":
        code = sys.argv[2] if len(sys.argv) > 2 else "000001"
        show_trend(code)
    elif cmd == "clean":
        code = sys.argv[2] if len(sys.argv) > 2 else "000001"
        keep = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        clean_records(code, keep)
    elif cmd == "count":
        count_all()
    else:
        print(f"未知命令: {cmd}")
        print("可用: init | write | query | list | trend | clean | count")
