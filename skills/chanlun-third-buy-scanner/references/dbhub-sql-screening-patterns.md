# DBHub SQL + Python 混合筛选模式

## 场景：多条件跨表筛选

日线表（kline_daily, 515 stocks）和 30分钟表（kline_30min, 48 stocks）覆盖范围不同，无法在单次 SQL 内完成全部条件筛选。需要**分层策略**：

```
Layer 1: DBHub SQL on kline_daily → 日线底分型 + 放量条件
Layer 2: 交叉验证 30分钟表可用股票
Layer 3: 直接 SQLite Python → 30分钟三买分析
```

## 第一步：检查数据覆盖

```sql
-- 两表日期范围 + 股票数
SELECT COUNT(DISTINCT stock_code) FROM kline_daily;
SELECT COUNT(DISTINCT stock_code) FROM kline_30min;

-- 各表的最新日期分布
SELECT date, COUNT(DISTINCT stock_code) as cnt
FROM kline_daily GROUP BY date ORDER BY date DESC LIMIT 5;
```

## 第二步：日线底分型 + 放量（DBHub SQL）

### 正确写法：CTE + JOIN（⚠️ 不要用子查询在 WHERE 里算均量）

```sql
WITH daily_rn AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
    FROM kline_daily
    WHERE stock_code IN (SELECT DISTINCT stock_code FROM kline_30min)
),
triple_bars AS (
    SELECT 
        a.stock_code, a.date, a.close, a.low AS curr_low, a.volume AS curr_vol,
        b.low AS prev1_low,
        c.low AS prev2_low
    FROM daily_rn a
    JOIN daily_rn b ON a.stock_code = b.stock_code AND b.rn = a.rn + 1
    JOIN daily_rn c ON a.stock_code = c.stock_code AND c.rn = a.rn + 2
    WHERE a.rn <= 10
      AND c.low > b.low          -- 底分型：左 > 中
      AND a.low > b.low          -- 底分型：右 > 中
),
vol_avg AS (
    SELECT 
        t.stock_code, t.date,
        AVG(d.volume) AS avg_vol_5d
    FROM triple_bars t
    JOIN kline_daily d ON t.stock_code = d.stock_code
    WHERE d.date < t.date
    GROUP BY t.stock_code, t.date
    HAVING COUNT(*) >= 5
)
SELECT 
    t.stock_code, t.date, t.close,
    t.curr_vol, ROUND(v.avg_vol_5d) as avg_vol_5d,
    ROUND(CAST(t.curr_vol AS REAL) / v.avg_vol_5d, 2) as vol_ratio
FROM triple_bars t
JOIN vol_avg v ON t.stock_code = v.stock_code AND t.date = v.date
WHERE t.curr_vol > v.avg_vol_5d * 1.5
ORDER BY vol_ratio DESC;
```

### ⚠️ 踩坑：以下写法在 DBHub 中返回空结果

```sql
-- ❌ 错误：子查询在 WHERE 里 LIMIT 5 会返回空
AND a.volume > (SELECT AVG(volume) FROM (SELECT volume FROM kline_daily d2 
    WHERE d2.stock_code = a.stock_code AND d2.date < a.date 
    ORDER BY d2.date DESC LIMIT 5)) * 1.5

-- ✅ 正确：CTE + GROUP BY 预计算均量
```

## 第三步：30分钟三买检测（本地 Python + SQLite）

DBHub 不支持复杂 CTE + 窗口函数的组合，30分钟三买分析必须用 Python 直接连接 SQLite。

### 三买检测逻辑

```python
import sqlite3

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.execute("""
    SELECT date, id, open, high, low, close, volume 
    FROM kline_30min 
    WHERE stock_code = ? 
    ORDER BY date DESC, id DESC 
    LIMIT 120
""", (code,))
rows = [dict(r) for r in cur.fetchall()]
all_data = rows[::-1]  # 时间正序

# 找最窄的30根连续区间（中枢）
best_range = 999
for i in range(len(all_data) - 30):
    seg = all_data[i:i+30]
    h = max(s['high'] for s in seg)
    l = min(s['low'] for s in seg)
    r = h - l
    if r < best_range:
        best_range, best_high, best_low, best_start = r, h, l, i

# 中枢后检测
post_data = all_data[best_start+30:]
post_high = max(s['high'] for s in post_data)
post_low = min(s['low'] for s in post_data)

# 三买判定
breakout = post_high > best_high
pullback_holds = post_low > best_high  # 严格版：不进入中枢
bounce = post_data[-1]['close'] > post_data[-3]['close']  # 最新3根反弹
```

### 判读等级

| 等级 | 条件 | 说明 |
|:---:|------|------|
| ✅ 三买 | breakout + pullback_holds + bounce | 严格三买 |
| ★★ 类三买 | breakout + (pullback进中枢) + bounce | 回踩稍深，偏二买 |
| ★ 待观察 | breakout 但 bounce 待确认 | 仅突破，等回踩 |
| ❌ 无效 | 无突破或中枢后区间更窄 | 未形成结构 |

## 底层限制

- 两表股票集不重叠时，三买验证不可用
- 30分钟表数据结束日期可能早于日线表（特别是最近1-2个交易日）
- 建议先检查两张表的最新日期分布