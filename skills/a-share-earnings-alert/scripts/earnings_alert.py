#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股业绩预警监控 - v2.0 增量版
功能：
1. 自动推断当前应查询的报告期
2. SQLite 本地缓存，增量 diff，只报告新增数据
3. 业绩预告 + 业绩快报双数据源
4. 生成 Markdown 报告（新增明细 + 全量统计）

数据源：东方财富（通过 akshare 库，免费）
缓存：SQLite（earnings_cache.db），零依赖
"""

import akshare as ak
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import argparse
import os
import sys
import re

sys.stdout.reconfigure(encoding='utf-8')


# ═══════════════════════════════════════════════════════════════
# 报告期辅助
# ═══════════════════════════════════════════════════════════════

class ReportPeriodHelper:
    """报告期辅助类"""

    VACUUM_MONTHS = [5, 6, 11, 12]

    @classmethod
    def get_current_period(cls, date=None):
        """根据日期推断应查询的报告期"""
        if date is None:
            date = datetime.now()
        year = date.year
        month = date.month

        if month in [1, 2, 3]:
            return f"{year-1}1231", f"{year-1}年报"
        elif month == 4:
            return f"{year-1}1231", f"{year-1}年报"
        elif month in [5, 6]:
            return f"{year}0331", f"{year}一季报"
        elif month in [7, 8]:
            return f"{year}0630", f"{year}中报"
        elif month == 9:
            return f"{year}0630", f"{year}中报"
        elif month == 10:
            return f"{year}0930", f"{year}三季报"
        else:  # 11-12月
            return f"{year}0930", f"{year}三季报"

    @classmethod
    def is_vacuum_period(cls, date=None):
        if date is None:
            date = datetime.now()
        return date.month in cls.VACUUM_MONTHS

    @classmethod
    def get_vacuum_warning(cls, date=None):
        if date is None:
            date = datetime.now()
        month = date.month
        if month in [5, 6]:
            return "当前为业绩真空期（5-6月），一季报已披露完毕，中报尚未开始"
        elif month in [11, 12]:
            return "当前为业绩真空期（11-12月），三季报已披露完毕，年报尚未开始"
        return None


# ═══════════════════════════════════════════════════════════════
# SQLite 缓存层
# ═══════════════════════════════════════════════════════════════

class EarningsCache:
    """业绩数据本地缓存（SQLite）"""

    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._init_tables()

    def _init_tables(self):
        """建表（幂等）"""
        self.conn.executescript("""
            -- 业绩预告缓存
            CREATE TABLE IF NOT EXISTS yjyg_cache (
                stock_code     TEXT NOT NULL,
                announce_date  TEXT NOT NULL,
                forecast_metric TEXT NOT NULL,
                stock_name     TEXT,
                forecast_type  TEXT,
                change_text    TEXT,
                change_pct     REAL,
                industry       TEXT,
                fetch_time     TEXT DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (stock_code, announce_date, forecast_metric)
            );

            -- 业绩快报缓存
            CREATE TABLE IF NOT EXISTS yjkb_cache (
                stock_code     TEXT NOT NULL,
                announce_date  TEXT NOT NULL,
                stock_name     TEXT,
                eps            REAL,
                revenue        REAL,
                revenue_yoy    REAL,
                net_profit     REAL,
                net_profit_yoy REAL,
                industry       TEXT,
                fetch_time     TEXT DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (stock_code, announce_date)
            );

            -- 运行记录
            CREATE TABLE IF NOT EXISTS run_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_time    TEXT DEFAULT (datetime('now', 'localtime')),
                report_period TEXT,
                yjyg_total  INTEGER,
                yjyg_new    INTEGER,
                yjkb_total  INTEGER,
                yjkb_new    INTEGER
            );
        """)
        self.conn.commit()

    def diff_and_insert_yjyg(self, df, report_period):
        """
        业绩预告增量插入
        复合主键: (stock_code, announce_date, forecast_metric)
        返回: (new_df, total_cached_count)
        """
        if df is None or len(df) == 0:
            return pd.DataFrame(), self._count('yjyg_cache', report_period)

        # 标准化列名：AKShare -> 缓存列
        col_map = {
            '股票代码': 'stock_code',
            '股票简称': 'stock_name',
            '公告日期': 'announce_date',
            '预测指标': 'forecast_metric',
            '预告类型': 'forecast_type',
            '业绩变动': 'change_text',
            '业绩变动幅度': 'change_pct',
        }
        # 只选存在的列
        existing_cols = {k: v for k, v in col_map.items() if k in df.columns}
        mapped = df[list(existing_cols.keys())].rename(columns=existing_cols).copy()

        # 行业列可能叫 '所处行业' 或不存在（yjyg 无行业字段）
        if '所处行业' in df.columns:
            mapped['industry'] = df['所处行业']
        else:
            mapped['industry'] = None

        # 公告日期统一为字符串
        mapped['announce_date'] = pd.to_datetime(mapped['announce_date']).dt.strftime('%Y-%m-%d')

        # 用临时表做 diff（SQLite INSERT OR IGNORE 靠主键冲突自动跳过）
        mapped.to_sql('_tmp_yjyg', self.conn, if_exists='replace', index=False)

        before = self._count('yjyg_cache', report_period)

        self.conn.executescript("""
            INSERT OR IGNORE INTO yjyg_cache
                (stock_code, announce_date, forecast_metric, stock_name, forecast_type, change_text, change_pct, industry)
            SELECT stock_code, announce_date, forecast_metric, stock_name, forecast_type, change_text, change_pct, industry
            FROM _tmp_yjyg;
            DROP TABLE IF EXISTS _tmp_yjyg;
        """)
        self.conn.commit()

        after = self._count('yjyg_cache', report_period)
        new_count = after - before

        # 取出新增记录
        if new_count > 0:
            new_df = pd.read_sql_query("""
                SELECT * FROM yjyg_cache
                WHERE stock_code || announce_date || forecast_metric IN (
                    SELECT stock_code || announce_date || forecast_metric FROM yjyg_cache
                    ORDER BY fetch_time DESC LIMIT ?
                )
            """, self.conn, params=(new_count,))
        else:
            new_df = pd.DataFrame()

        print(f"   业绩预告缓存: {before} -> {after} (+{new_count} 新增)")
        return new_df, after

    def diff_and_insert_yjkb(self, df, report_period):
        """
        业绩快报增量插入
        复合主键: (stock_code, announce_date)
        返回: (new_df, total_cached_count)
        """
        if df is None or len(df) == 0:
            return pd.DataFrame(), self._count('yjkb_cache', report_period)

        col_map = {
            '股票代码': 'stock_code',
            '股票简称': 'stock_name',
            '公告日期': 'announce_date',
            '每股收益': 'eps',
            '营业收入-营业收入': 'revenue',
            '营业收入-同比增长': 'revenue_yoy',
            '净利润-净利润': 'net_profit',
            '净利润-同比增长': 'net_profit_yoy',
        }
        existing_cols = {k: v for k, v in col_map.items() if k in df.columns}
        mapped = df[list(existing_cols.keys())].rename(columns=existing_cols).copy()

        if '所处行业' in df.columns:
            mapped['industry'] = df['所处行业']

        mapped['announce_date'] = pd.to_datetime(mapped['announce_date']).dt.strftime('%Y-%m-%d')

        mapped.to_sql('_tmp_yjkb', self.conn, if_exists='replace', index=False)

        before = self._count('yjkb_cache', report_period)

        self.conn.executescript("""
            INSERT OR IGNORE INTO yjkb_cache
                (stock_code, announce_date, stock_name, eps, revenue, revenue_yoy, net_profit, net_profit_yoy, industry)
            SELECT stock_code, announce_date, stock_name, eps, revenue, revenue_yoy, net_profit, net_profit_yoy, industry
            FROM _tmp_yjkb;
            DROP TABLE IF EXISTS _tmp_yjkb;
        """)
        self.conn.commit()

        after = self._count('yjkb_cache', report_period)
        new_count = after - before

        if new_count > 0:
            new_df = pd.read_sql_query("""
                SELECT * FROM yjkb_cache
                ORDER BY fetch_time DESC LIMIT ?
            """, self.conn, params=(new_count,))
        else:
            new_df = pd.DataFrame()

        print(f"   业绩快报缓存: {before} -> {after} (+{new_count} 新增)")
        return new_df, after

    def _count(self, table, report_period):
        """统计缓存中某报告期的记录数"""
        # yjyg_cache 用 announce_date 和 forecast_metric 做 PR; 无需额外过滤
        cursor = self.conn.execute(f"SELECT COUNT(*) FROM {table}")
        return cursor.fetchone()[0]

    def get_cumulative_yjyg(self, report_period):
        """获取缓存中该报告期的全部预告数据"""
        return pd.read_sql_query(
            "SELECT * FROM yjyg_cache",
            self.conn
        )

    def get_cumulative_yjkb(self, report_period):
        """获取缓存中该报告期的全部快报数据"""
        return pd.read_sql_query(
            "SELECT * FROM yjkb_cache",
            self.conn
        )

    def log_run(self, report_period, yjyg_total, yjyg_new, yjkb_total, yjkb_new):
        """记录本次运行"""
        self.conn.execute(
            "INSERT INTO run_log (report_period, yjyg_total, yjyg_new, yjkb_total, yjkb_new) VALUES (?, ?, ?, ?, ?)",
            (report_period, yjyg_total, yjyg_new, yjkb_total, yjkb_new)
        )
        self.conn.commit()

    def get_last_run(self, report_period):
        """获取上次运行记录"""
        cursor = self.conn.execute(
            "SELECT * FROM run_log WHERE report_period = ? ORDER BY id DESC LIMIT 1",
            (report_period,)
        )
        row = cursor.fetchone()
        return row

    def reset_period(self, report_period):
        """清空指定报告期的缓存（重新全量导入）"""
        self.conn.execute("DELETE FROM yjyg_cache")
        self.conn.execute("DELETE FROM yjkb_cache")
        self.conn.execute("DELETE FROM run_log")
        self.conn.commit()
        print(f"[RESET] 缓存已清空，下次运行将全量导入")

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════════
# 主业务类
# ═══════════════════════════════════════════════════════════════

class AStockEarningsAlert:
    """A股业绩预警监控"""

    def __init__(self, output_dir=None, db_path=None):
        self.today = datetime.now()
        self.today_str = self.today.strftime('%Y-%m-%d')
        self.today_short = self.today.strftime('%Y%m%d')

        if output_dir is None:
            self.output_dir = "/mnt/d/常用文件/业绩预警/"
        else:
            self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        if db_path is None:
            db_path = os.path.join(self.output_dir, "earnings_cache.db")
        self.cache = EarningsCache(db_path)

        self.df_yjyg = None
        self.df_yjkb = None
        self.df_yjyg_new = None  # 本次新增
        self.df_yjkb_new = None
        self.yjyg_total = 0
        self.yjyg_new = 0
        self.yjkb_total = 0
        self.yjkb_new = 0
        self.report_period = None
        self.report_period_name = None

    def set_report_period(self, period=None):
        if period:
            self.report_period = period
            year = period[:4]
            suffix = period[4:]
            suffix_map = {'0331': '一季报', '0630': '中报', '0930': '三季报', '1231': '年报'}
            self.report_period_name = f"{year}{suffix_map.get(suffix, '')}"
        else:
            self.report_period, self.report_period_name = ReportPeriodHelper.get_current_period()
        print(f"[INFO] 报告期: {self.report_period} ({self.report_period_name})")

    def fetch_yjyg_data(self):
        print(f"\n{'='*60}")
        print(f"获取业绩预告数据 - {self.report_period}")
        print(f"{'='*60}")
        try:
            df = ak.stock_yjyg_em(date=self.report_period)
            self.df_yjyg = df
            print(f"[OK] 获取到 {len(df)} 条业绩预告（全量）")
            return df
        except Exception as e:
            print(f"[ERROR] 获取业绩预告数据失败: {e}")
            self.df_yjyg = pd.DataFrame()
            return self.df_yjyg

    def fetch_yjkb_data(self):
        print(f"\n{'='*60}")
        print(f"获取业绩快报数据 - {self.report_period}")
        print(f"{'='*60}")
        try:
            df = ak.stock_yjkb_em(date=self.report_period)
            self.df_yjkb = df
            print(f"[OK] 获取到 {len(df)} 条业绩快报（全量）")
            return df
        except Exception as e:
            print(f"[ERROR] 获取业绩快报数据失败: {e}")
            self.df_yjkb = pd.DataFrame()
            return self.df_yjkb

    def do_diff(self):
        """增量 diff：对比本地缓存，只保留新增"""
        print(f"\n{'='*60}")
        print("增量对比（vs 本地缓存）")
        print(f"{'='*60}")

        self.df_yjyg_new, self.yjyg_total = self.cache.diff_and_insert_yjyg(
            self.df_yjyg, self.report_period
        )
        self.df_yjkb_new, self.yjkb_total = self.cache.diff_and_insert_yjkb(
            self.df_yjkb, self.report_period
        )

        self.yjyg_new = len(self.df_yjyg_new) if self.df_yjyg_new is not None else 0
        self.yjkb_new = len(self.df_yjkb_new) if self.df_yjkb_new is not None else 0

        # 记录本次运行
        self.cache.log_run(self.report_period, self.yjyg_total, self.yjyg_new,
                           self.yjkb_total, self.yjkb_new)

    def extract_percentage(self, text):
        if pd.isna(text):
            return None
        text = str(text)
        patterns = [
            r'[增减降]+[:：]\s*([+-]?\d+\.?\d*)%',
            r'([+-]?\d+\.?\d*)%',
            r'([+-]?\d+\.?\d*)\s*%'
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return float(match.group(1))
                except:
                    continue
        return None

    def analyze_cumulative(self):
        """全量分析（基于缓存中的数据）"""
        df_yjyg = self.cache.get_cumulative_yjyg(self.report_period)
        df_yjkb = self.cache.get_cumulative_yjkb(self.report_period)

        result = {
            'yjyg': self._analyze_yjyg_cumulative(df_yjyg),
            'yjkb': self._analyze_yjkb_cumulative(df_yjkb),
        }
        return result

    def _analyze_yjyg_cumulative(self, df):
        if df is None or len(df) == 0:
            return {'total': 0}

        analysis = {'total': len(df), 'by_type': {}, 'by_range': {}, 'extremes': {}}

        if 'forecast_type' in df.columns:
            analysis['by_type'] = df['forecast_type'].value_counts().to_dict()

        if 'change_pct' in df.columns:
            pct = pd.to_numeric(df['change_pct'], errors='coerce')
            analysis['by_range'] = {
                '大幅增长 (>100%)': int((pct > 100).sum()),
                '中等增长 (50%-100%)': int(((pct >= 50) & (pct <= 100)).sum()),
                '小幅增长 (0%-50%)': int(((pct >= 0) & (pct < 50)).sum()),
                '小幅下降 (-50%-0%)': int(((pct >= -50) & (pct < 0)).sum()),
                '大幅下降 (<-50%)': int((pct < -50).sum()),
                '无法解析': int(pct.isna().sum()),
            }
            pct_valid = pct.dropna()
            if len(pct_valid) > 0:
                max_idx = pct_valid.idxmax()
                min_idx = pct_valid.idxmin()
                analysis['extremes'] = {
                    'max': {'股票': df.loc[max_idx, 'stock_name'],
                            '代码': df.loc[max_idx, 'stock_code'],
                            '变动': f"{pct_valid[max_idx]:.2f}%",
                            '类型': df.loc[max_idx, 'forecast_type']},
                    'min': {'股票': df.loc[min_idx, 'stock_name'],
                            '代码': df.loc[min_idx, 'stock_code'],
                            '变动': f"{pct_valid[min_idx]:.2f}%",
                            '类型': df.loc[min_idx, 'forecast_type']},
                }

        return analysis

    def _analyze_yjkb_cumulative(self, df):
        if df is None or len(df) == 0:
            return {'total': 0}

        analysis = {'total': len(df), 'high_growth': [], 'high_decline': []}

        if 'net_profit_yoy' in df.columns:
            pct = pd.to_numeric(df['net_profit_yoy'], errors='coerce')
            high_growth = df[pct > 100].nlargest(10, 'net_profit_yoy')
            high_decline = df[pct < -50].nsmallest(10, 'net_profit_yoy')

            analysis['high_growth'] = high_growth[[
                'stock_code', 'stock_name', 'net_profit', 'net_profit_yoy', 'industry'
            ]].to_dict('records') if len(high_growth) > 0 else []

            analysis['high_decline'] = high_decline[[
                'stock_code', 'stock_name', 'net_profit', 'net_profit_yoy', 'industry'
            ]].to_dict('records') if len(high_decline) > 0 else []

        return analysis

    def generate_markdown_report(self):
        """生成 Markdown 报告"""
        cumulative = self.analyze_cumulative()
        yjyg_c = cumulative['yjyg']
        yjkb_c = cumulative['yjkb']
        vacuum_warning = ReportPeriodHelper.get_vacuum_warning()

        # 上次运行时间
        last_run = self.cache.get_last_run(self.report_period)

        md = f"""# A股业绩预警报告

**报告日期**: {self.today_str}  
**报告期**: {self.report_period_name} ({self.report_period})  
**数据来源**: 东方财富 (AKShare)  
"""

        if last_run:
            md += f"**上次运行**: {last_run[1]}  \n"

        md += "\n---\n\n"

        # 真空期
        if vacuum_warning:
            md += f"> **⚠️ 提示**: {vacuum_warning}\n\n"

        # ── 本次增量 ──
        md += f"""## 🆕 本次新增

| 数据类型 | 本次新增 | 累计缓存 |
|:---------|:--------|:---------|
| 业绩预告 | **{self.yjyg_new}** 条 | {self.yjyg_total} 条 |
| 业绩快报 | **{self.yjkb_new}** 条 | {self.yjkb_total} 条 |

"""

        if self.yjyg_new == 0 and self.yjkb_new == 0:
            md += "*本次运行无新增数据，以下为全量统计。*\n\n"

        # ── 新增明细 ──
        if self.yjyg_new > 0 and self.df_yjyg_new is not None and len(self.df_yjyg_new) > 0:
            md += """### 新增业绩预告

| 股票代码 | 股票简称 | 预告类型 | 业绩变动 | 公告日期 |
|:---------|:---------|:---------|:---------|:---------|
"""
            for _, row in self.df_yjyg_new.iterrows():
                code = row.get('stock_code', '')
                name = row.get('stock_name', '')
                ftype = row.get('forecast_type', '')
                change = str(row.get('change_text', ''))[:40]
                adate = row.get('announce_date', '')
                md += f"| {code} | {name} | {ftype} | {change} | {adate} |\n"
            md += "\n"

        if self.yjkb_new > 0 and self.df_yjkb_new is not None and len(self.df_yjkb_new) > 0:
            md += """### 新增业绩快报

| 股票代码 | 股票简称 | 净利润 | 同比增长 | 行业 | 公告日期 |
|:---------|:---------|:-------|:---------|:-----|:---------|
"""
            for _, row in self.df_yjkb_new.iterrows():
                code = row.get('stock_code', '')
                name = row.get('stock_name', '')
                profit = row.get('net_profit', '')
                yoy = row.get('net_profit_yoy', '')
                industry = row.get('industry', '')
                adate = row.get('announce_date', '')
                md += f"| {code} | {name} | {profit} | {yoy} | {industry} | {adate} |\n"
            md += "\n"

        md += "---\n\n"

        # ── 全量统计 ──
        md += "## 📊 全量统计\n\n"

        # 业绩预告
        md += f"### 业绩预告（累计 {yjyg_c.get('total', 0)} 条）\n\n"

        if yjyg_c.get('by_type'):
            md += "#### 预告类型分布\n\n| 预告类型 | 数量 | 占比 |\n|:---------|:-----|:-----|\n"
            total = yjyg_c['total']
            for t, c in sorted(yjyg_c['by_type'].items(), key=lambda x: x[1], reverse=True):
                pct = c / total * 100 if total > 0 else 0
                md += f"| {t} | {c} | {pct:.1f}% |\n"
            md += "\n"

        if yjyg_c.get('by_range'):
            md += "#### 业绩变动幅度分布\n\n| 变动区间 | 数量 |\n|:---------|:-----|\n"
            for r, c in yjyg_c['by_range'].items():
                md += f"| {r} | {c} |\n"
            md += "\n"

        if yjyg_c.get('extremes'):
            md += "#### 业绩变动极值\n\n"
            if 'max' in yjyg_c['extremes']:
                m = yjyg_c['extremes']['max']
                md += f"- **最大增长**: {m['股票']} ({m['代码']}) - **{m['变动']}** ({m['类型']})\n"
            if 'min' in yjyg_c['extremes']:
                m = yjyg_c['extremes']['min']
                md += f"- **最大下降**: {m['股票']} ({m['代码']}) - **{m['变动']}** ({m['类型']})\n"
            md += "\n"

        # 业绩快报
        md += f"### 业绩快报（累计 {yjkb_c.get('total', 0)} 条）\n\n"

        if yjkb_c.get('high_growth'):
            md += "#### 净利润高增长 (>100%)\n\n| 股票代码 | 股票简称 | 净利润 | 同比增长 | 行业 |\n|:---------|:---------|:-------|:---------|:-----|\n"
            for item in yjkb_c['high_growth'][:15]:
                md += f"| {item.get('stock_code', '')} | {item.get('stock_name', '')} | {item.get('net_profit', '')} | {item.get('net_profit_yoy', '')} | {item.get('industry', '')} |\n"
            md += "\n"

        if yjkb_c.get('high_decline'):
            md += "#### 净利润大幅下降 (<-50%)\n\n| 股票代码 | 股票简称 | 净利润 | 同比增长 | 行业 |\n|:---------|:---------|:-------|:---------|:-----|\n"
            for item in yjkb_c['high_decline'][:15]:
                md += f"| {item.get('stock_code', '')} | {item.get('stock_name', '')} | {item.get('net_profit', '')} | {item.get('net_profit_yoy', '')} | {item.get('industry', '')} |\n"
            md += "\n"

        md += f"""---

*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

        return md

    def save_report(self, content):
        filename = f"业绩预警报告_{self.today_short}.md"
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"\n[OK] 报告已保存: {filepath}")
        return filepath

    def run(self, report_period=None, force_full=False, reset_cache=False):
        """运行完整流程"""
        print("=" * 60)
        print("A股业绩预警监控 v2.0 (增量)")
        print("=" * 60)

        self.set_report_period(report_period)

        # 重置缓存
        if reset_cache:
            self.cache.reset_period(self.report_period)

        # 真空期提示
        if ReportPeriodHelper.is_vacuum_period():
            warning = ReportPeriodHelper.get_vacuum_warning()
            print(f"\n⚠️  {warning}")

        # 获取全量数据
        self.fetch_yjyg_data()
        self.fetch_yjkb_data()

        # 增量 diff
        self.do_diff()

        if self.yjyg_new == 0 and self.yjkb_new == 0 and not force_full:
            print("\n[INFO] 本次无新增数据，跳过报告生成。")
            print("[INFO] 使用 --force-full 可强制生成全量报告。")
            self.cache.close()
            return None

        # 生成报告
        print(f"\n{'='*60}")
        print("生成 Markdown 报告")
        print(f"{'='*60}")

        report_content = self.generate_markdown_report()
        filepath = self.save_report(report_content)

        self.cache.close()

        print(f"\n{'='*60}")
        print("执行完毕")
        print(f"{'='*60}")

        return filepath


def main():
    parser = argparse.ArgumentParser(description='A股业绩预警监控 v2.0')
    parser.add_argument('--period', type=str, help='报告期 (如20241231)')
    parser.add_argument('--output', type=str, default='/mnt/d/常用文件/业绩预警/',
                        help='输出目录')
    parser.add_argument('--reset', action='store_true', help='清空缓存，全量重新导入')
    parser.add_argument('--force-full', action='store_true',
                        help='即使无新增数据也生成全量报告')

    args = parser.parse_args()

    alert = AStockEarningsAlert(output_dir=args.output)
    alert.run(report_period=args.period, force_full=args.force_full,
              reset_cache=args.reset)


if __name__ == "__main__":
    main()
