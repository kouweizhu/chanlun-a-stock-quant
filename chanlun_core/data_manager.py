import os
import contextlib
from date_utils import date_to_str, parse_date_to_datetime
import json
import pandas as pd
import baostock as bs
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Union

# v5.4(B-02): Baostock 会话是进程级全局单例(bs.login 全局生效), 并发抓取曾发生
# PE串包/会话互踩。与 akshare_fundamental 共用同一把 BS_SESSION_LOCK, 把
# DataManager 的登录+查询+迭代全程串行化(akshare_fundamental 已在用此锁)。
try:
    from baostock_utils import BS_SESSION_LOCK as _BS_LOCK
except Exception:
    _BS_LOCK = None

# akshare 采用惰性导入，避免模块级依赖

# Agent 层数据源桥接（当所有 Python 库失败时，通过文件读取 Agent MCP 工具的 fallback 数据）
try:
    from data_source_helper import check_agent_fallback, mark_python_sources_failed, clear_agent_fallback
    _HAS_HELPER = True
except ImportError:
    _HAS_HELPER = False

class DataManager:
    def __init__(self, cache_dir=None):
        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")
        self.cache_dir = cache_dir
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        self._bs_logged_in = False
        self._bs_failures = 0          # 连续失败计数

    def _login_baostock(self, force=False):
        """登录 Baostock，支持强制重新登录
        
        Args:
            force: 强制重新登录（即使 _bs_logged_in 为 True）
        Returns:
            bool: 登录是否成功
        """
        if force:
            if self._bs_logged_in:
                try:
                    bs.logout()
                except:
                    pass
            self._bs_logged_in = False
        
        if not self._bs_logged_in:
            try:
                bs.login()
                self._bs_logged_in = True
                self._bs_failures = 0
                return True
            except Exception as e:
                self._bs_logged_in = False
                print(f"[DataManager] Baostock login FAIL: {e}")
                return False
        return True

    def _mark_baostock_failure(self):
        """标记一次 Baostock 失败，连续 3 次后下次自动强制重登"""
        self._bs_failures += 1
        if self._bs_failures >= 3:
            print(f"[DataManager] Baostock {self._bs_failures} 次连续失败，下次将强制重登...")

    def _baostock_on_success(self):
        """Baostock 调用成功后重置失败计数"""
        if self._bs_failures > 0:
            print(f"[DataManager] Baostock 恢复 (之前 {self._bs_failures} 次失败已重置)")
        self._bs_failures = 0

    def _get_cache_path(self, symbol, level):
        return os.path.join(self.cache_dir, f"{symbol}_{level}.parquet")

    # ═══ v5.1 缓存新鲜度重构（2026-08-22）═══
    # 旧判据：mtime TTL（日线24h/30min 6h）——语义错误：衡量的是"文件多久没写"，
    # 而真实语义应为"缓存是否已覆盖最近已完结交易日"。周五上午拉的缓存在周六运行
    # 时被判过期 → 触发 Baostock 全量重拉（实测 10min+ 无产出卡死 Phase 1）。
    # 新判据：cache_max_date >= 最近已完结交易日 → HIT。
    # 节假日误判方向安全：最多多拉一次，不会漏数据（法定节假日表中缺的假日会
    # 让 expected 前移，重拉返回空数据后行为不变，仅浪费一次拉取）。

    # 当日 K 线发布保守阈值（小时）：Baostock 日线约 17:30-19:00 出当日数据、偶有延迟；
    # 30min 更晚。此前该时段内运行时 expected 取前一交易日，避免把"未出数据"误判为过期。
    _DATA_PUBLISH_HOUR = {'daily': 19, '30min': 20}

    def _expected_latest_trade_date(self, level='daily', end_date=None):
        """推断最近已完结交易日（星期法，不含法定节假日表）。

        - 周末回退到周五；当日发布时间阈值前（见 _DATA_PUBLISH_HOUR）不含当日，
          避免把盘中/收盘后数据未出时段误判为缓存过期。
        - 请求带历史 end_date（回测/区间取数）时以 min(end_date, 推断值) 为目标：
          只要缓存覆盖到请求终点即可，无需追到最新交易日。
        """
        now = datetime.now()
        d = now.date()
        if now.hour < self._DATA_PUBLISH_HOUR.get(level, 19) or d.weekday() > 4:
            d -= timedelta(days=1)
        while d.weekday() > 4:
            d -= timedelta(days=1)
        inferred = d.strftime('%Y-%m-%d')
        if end_date:
            try:
                target = str(end_date)[:10]
                if target < inferred:
                    return target
            except Exception:
                pass
        return inferred

    def _completeness_meta_path(self, symbol, level):
        return os.path.join(self.cache_dir, "meta", f"{symbol}_{level}.history_complete.json")

    # ── v5.3.1(P1-3): 假假日探测节流 ──
    _PROBE_META_PATH = None  # 类级缓存路径, 首次访问时初始化
    _PROBE_THROTTLE_HOURS = 4

    def _probe_meta_path(self):
        if self._PROBE_META_PATH is None:
            type(self)._PROBE_META_PATH = os.path.join(self.cache_dir, "meta", "_incremental_probe.json")
        return self._PROBE_META_PATH

    def _recently_probed_empty(self, symbol, level):
        """4小时内已探测过'无新数据' → 跳过重复网探（长假场景每股每轮都探测）"""
        try:
            with open(self._probe_meta_path(), encoding='utf-8') as f:
                meta = json.load(f)
            ts = datetime.fromisoformat(meta.get(f"{symbol}_{level}", ""))
            return (datetime.now() - ts).total_seconds() < self._PROBE_THROTTLE_HOURS * 3600
        except Exception:
            return False

    def _mark_probe_empty(self, symbol, level):
        try:
            os.makedirs(os.path.dirname(self._probe_meta_path()), exist_ok=True)
            meta = {}
            try:
                with open(self._probe_meta_path(), encoding='utf-8') as f:
                    meta = json.load(f)
            except Exception:
                pass
            meta[f"{symbol}_{level}"] = datetime.now().isoformat()
            # 只保留最近 500 条防无限膨胀
            if len(meta) > 500:
                meta = dict(sorted(meta.items(), key=lambda kv: kv[1], reverse=True)[:500])
            with open(self._probe_meta_path(), 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False)
        except Exception:
            pass  # 节流失败不影响主流程(只多一次网探)

    def _is_history_complete(self, symbol, level):
        """v5.1 次新股历史完整标记：该股曾因覆盖不足重拉、且未获得更早数据
        （即缓存起点就是数据源可得的最早历史，典型如上市晚于请求起点的次新股）
        → 后续覆盖校验豁免，不再每次重拉。"""
        try:
            with open(self._completeness_meta_path(symbol, level), encoding='utf-8') as f:
                json.load(f)
            return True
        except Exception:
            return False

    def _mark_history_complete(self, symbol, level, min_date):
        try:
            os.makedirs(os.path.dirname(self._completeness_meta_path(symbol, level)), exist_ok=True)
            with open(self._completeness_meta_path(symbol, level), 'w', encoding='utf-8') as f:
                json.dump({'min_date': min_date,
                           'confirmed_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                           'note': '重拉未获更早数据，缓存起点即数据源最早可得历史'},
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[DataManager] 写历史完整标记失败(不影响数据): {e}")

    # ═══ v5.3 本地主库数据源级（2026-08-22）═══
    # 唯一主库 = data_cache/chanlun_klines.db（C 盘旧库已弃用合并），
    # 由每日 21:30 任务(KLineIncrementalSync)与 pipeline 写缓存双路维护。
    # 读缓存过期时先查主库增量（零网络、毫秒级），主库也没有新数据才走在线源。

    _LOCAL_DB_TABLE = {'daily': 'kline_daily', '30min': 'kline_30min'}

    def _local_db_fetch(self, symbol, level, start_date=None, end_date=None):
        """从本地 SQLite 主库读取K线（零网络）。

        Returns:
            DataFrame: date/open/high/low/close/volume，升序；无库/无数据/异常时返回空 DataFrame
        """
        table = self._LOCAL_DB_TABLE.get(level)
        db_path = os.path.join(self.cache_dir, "chanlun_klines.db")
        if not table or not os.path.exists(db_path):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(db_path)
            try:
                sql = f"SELECT date, open, high, low, close, volume FROM {table} WHERE stock_code=?"
                params = [symbol]
                if start_date:
                    sql += " AND date>=?"
                    params.append(str(start_date)[:10])
                if end_date:
                    sql += " AND date<=?"
                    params.append(str(end_date)[:10])
                sql += " ORDER BY date"
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                conn.close()
            if not df.empty:
                df['date'] = df['date'].astype(str)
                df['volume'] = df['volume'].astype('int64')
            return df
        except Exception as e:
            print(f"[DataManager] 本地主库读取失败(转网络源): {e}")
            return pd.DataFrame()

    def _try_incremental_fetch(self, symbol, level, cache_max_date, old_close=None):
        """v5.2/v5.3: 缓存过期时先增量补拉 [cache_max_date, today]，避免全量重拉。

        v5.3 起增量第一优先查本地主库（零网络），主库无新数据（如每日任务未跑）
        才降级 Baostock 在线增量。

        从 cache_max_date 当天起拉（含当天），可顺带修正当天可能的不完整数据；
        拼接时按日期去重保留新值（幂等，重跑安全）。

        Returns:
            DataFrame: 增量行（严格晚于 cache_max_date），可为空 = 无新数据（假期/停牌）
            None: 增量不可用（异常/疑似除权），调用方应回退全量拉取
        """
        try:
            # ── 第一优先：本地主库增量（零网络、毫秒级）──
            # v5.3.1(P0-1修复): 仅 daily 走主库增量。主库 30min 表 date 只存到
            # "日"（同日8根bar共享同一日期字符串，且无 time 字段），ORDER BY date
            # 对同日多根的行序不保证——返回的"增量"无法正确重建日内结构，
            # 合并会摧毁日内K线。故 30min 直接走 Baostock（行序=时序）。
            if level == 'daily':
                inc = self._local_db_fetch(symbol, level, start_date=cache_max_date)
                src = '本地主库'
            else:
                inc = pd.DataFrame()
                src = 'Baostock'
            if inc.empty:
                print(f"[DataManager] 本地主库无增量 [{cache_max_date}~]，尝试 Baostock 在线增量 ...")
                today = datetime.now().strftime('%Y-%m-%d')
                inc = self.fetch_baostock_data(symbol, level, cache_max_date, today)
                src = 'Baostock'
            else:
                print(f"[DataManager] 本地主库命中增量 [{cache_max_date}~] {len(inc)} 行 (零网络)")
            if inc is None or inc.empty:
                return pd.DataFrame()
            inc = inc.copy()
            inc['_ds'] = inc['date'].astype(str).str[:10]
            # 除权检测：cache_max 当天的重拉收盘 vs 缓存收盘，差异 >0.1% 视为
            # 前复权因子变化（除权），增量拼接会造成价格断裂 → 放弃增量回退全量
            _same_day = inc[inc['_ds'] == cache_max_date]
            if not _same_day.empty and old_close:
                try:
                    _new_close = float(_same_day.iloc[0]['close'])
                    if abs(_new_close / old_close - 1) > 0.001:
                        print(f"[DataManager] 检测到前复权价变化({old_close:.2f}→{_new_close:.2f}，疑似除权)，放弃增量回退全量")
                        return None
                except Exception:
                    pass
            inc = inc[inc['_ds'] > cache_max_date].drop(columns=['_ds'])
            return inc
        except Exception as e:
            print(f"[DataManager] 增量补拉异常: {e}")
            return None

    def fetch_efinance_data(self, symbol, level='daily', start_date=None, end_date=None):
        """备选1: efinance（东方财富源，可能因网络受限失败）"""
        print(f"[DataManager] Trying efinance ({level})...")
        try:
            import efinance as ef
            fqt = 1  # 前复权
            if level == 'daily':
                klt = 101  # 日线
            elif level == '30min':
                klt = 30   # 30分钟
            else:
                return pd.DataFrame()

            df = ef.stock.get_quote_history(symbol, klt=klt, fqt=fqt)
            if df is None or df.empty:
                return pd.DataFrame()

            # efinance 字段映射
            df = df.rename(columns={
                '日期': 'date', '开盘': 'open', '最高': 'high',
                '最低': 'low', '收盘': 'close', '成交量': 'volume',
                '成交额': 'amount'
            })
            df['date'] = df['date'].astype(str)
            for col in ['open', 'high', 'low', 'close']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int)

            col_subset = ['date', 'open', 'high', 'low', 'close', 'volume']
            if 'amount' in df.columns:
                col_subset.append('amount')
            df = df[col_subset].dropna(subset=['date', 'open', 'close'])

            # 按start_date/end_date过滤
            if start_date and 'date' in df.columns:
                df = df[df['date'] >= str(start_date)]
            if end_date and 'date' in df.columns:
                df = df[df['date'] <= str(end_date)]

            print(f"[DataManager] efinance {level} OK: {len(df)} rows, latest={df.iloc[-1]['close']:.2f}")
            return df
        except Exception as e:
            msg = str(e)[:80]
            print(f"[DataManager] efinance fail: {msg}")
            return pd.DataFrame()

    def fetch_akshare_sina_data(self, symbol, level='daily', start_date=None, end_date=None):
        """备选2: AkShare 新浪源（绕过东方财富网络限制）"""
        import akshare as ak  # 惰性导入
        print(f"[DataManager] Trying AkShare Sina ({level})...")
        try:
            # 确定新浪代码格式
            sina_symbol = f"sz.{symbol}" if symbol.startswith('3') or symbol.startswith('0') else f"sh.{symbol}"
            # stock_zh_a_daily 实际用 sh/sz 前缀
            akshare_symbol = f"sz{symbol}" if symbol.startswith('3') or symbol.startswith('0') else f"sh{symbol}"

            if level == 'daily':
                df = ak.stock_zh_a_daily(symbol=akshare_symbol, adjust="qfq")
            elif level == '30min':
                # stock_zh_a_minute 用的是 sz301498 格式
                minute_symbol = f"sz.{symbol}" if symbol.startswith('3') or symbol.startswith('0') else f"sh.{symbol}"
                df = ak.stock_zh_a_minute(symbol=minute_symbol, period='30')
            else:
                return pd.DataFrame()

            if df is None or df.empty:
                return pd.DataFrame()

            # 统一字段名
            if level == 'daily':
                # stock_zh_a_daily: date, open, high, low, close, volume, amount
                pass  # 字段名一致
            else:
                # stock_zh_a_minute: day, open, high, low, close, volume, amount
                df = df.rename(columns={'day': 'date'})
                # 数值列转换为 float
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int)

            # 过滤日期
            if start_date and 'date' in df.columns:
                df['date_str'] = df['date'].astype(str).str[:10]
                df = df[df['date_str'] >= str(start_date)]
            if end_date and 'date' in df.columns:
                df['date_str'] = df['date'].astype(str).str[:10]
                df = df[df['date_str'] <= str(end_date)]
            if 'date_str' in df.columns:
                df = df.drop(columns=['date_str'])

            col_keeper = [c for c in ['date', 'open', 'high', 'low', 'close', 'volume'] if c in df.columns]
            df = df[col_keeper].dropna(subset=['date', 'open', 'close'])

            print(f"[DataManager] AkShare Sina {level} OK: {len(df)} rows, latest={df.iloc[-1]['close']:.2f}")
            return df
        except Exception as e:
            msg = str(e)[:80]
            print(f"[DataManager] AkShare Sina fail: {msg}")
            return pd.DataFrame()

    def fetch_akshare_data(self, symbol, level='daily', start_date=None, end_date=None):
        """备选3: AkShare 东方财富源（最后尝试）"""
        import akshare as ak  # 惰性导入
        print(f"[DataManager] Trying AkShare EM ({level})...")
        try:
            if level == 'daily':
                df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            elif level == '30min':
                df = ak.stock_zh_a_hist_min_em(symbol=symbol, period='30', start_date=start_date, end_date=end_date, adjust="qfq")
            else:
                return pd.DataFrame()
            if df.empty: return pd.DataFrame()
            rename_map = {'日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'}
            df = df.rename(columns=rename_map)[['date', 'open', 'high', 'low', 'close', 'volume']]
            df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
            df['volume'] = df['volume'].astype(int)
            return df
        except Exception as e:
            print(f"[DataManager] AkShare EM fail: {e}")
            return pd.DataFrame()

    def fetch_push2_data(self, symbol, level='daily', start_date=None, end_date=None):
        """备选3.7: 东财 push2his K线直连（v5.4 DS-01）

        AKShare EM 封装失败时同秒可用的直连通道（2026-06-10 海康威视案例实测，
        见 a-share-three-dim-analyzer references/akshare-quirks.md 第7章模板）。
        纪律: Referer 头必须带 quote.eastmoney.com；调用后 sleep≥2s 防限流。
        支持 daily(klt=101)/30min(klt=30)，fqt=1 前复权；
        klines 行序: date,open,close,high,low,volume(注意 close/high 次序!)。
        输出契约与其他源一致: date/open/high/low/close/volume。"""
        print(f"[DataManager] Trying push2his ({level})...")
        try:
            import requests
            import time as _time
            code = str(symbol).strip()
            # v5.4.1(P3): 北交所号段(43/83/87/92开头)误归沪市修正——旧规则把
            # '9'统一当sh, 92xxxx(北交所)会被拼成 1.92xxxx 查询必然失败。
            # 东财口径: 沪=1./深=0./北交所=0.
            if code.startswith(('6', '5')) or (code.startswith('9') and not code.startswith('92')):
                mkt = '1.'
            elif code.startswith(('4', '8', '92')):
                mkt = '0.'   # 北交所(东财侧与深市共用 market=0)
            else:
                mkt = '0.'
            secid = mkt + code
            if level == 'daily':
                klt = '101'
            elif level == '30min':
                klt = '30'
            else:
                return pd.DataFrame()
            # v5.4 实测修正: 超长区间(beg=1990)+日线组合会触发对端 WAF 断连
            # (RemoteDisconnected)，30min 窄窗正常——未指定 start 时改取近
            # 800 根(lmt)，并对请求异常做一次 3s 后重试。
            if start_date:
                beg = str(start_date).replace('-', '')[:8]
                lmt = '10000'
            else:
                beg = '0'
                lmt = '800'
            end = str(end_date).replace('-', '')[:8] if end_date else '20500101'
            url_path = '/api/qt/stock/kline/get'
            params = {
                'secid': secid, 'klt': klt, 'fqt': '1',
                'beg': beg, 'end': end, 'lmt': lmt,
                'fields1': 'f1,f2,f3,f4,f5,f6',
                'fields2': 'f51,f52,f53,f54,f55,f56,f57',
            }
            # v5.4 实测: 裸'Mozilla/5.0'短UA偶发被 WAF 断连；补全 UA 并做
            # 双域名轮换(主域失败切编号子域，与 AKShare 内部策略同思路)
            _headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'Referer': 'https://quote.eastmoney.com/'}
            _hosts = ['push2his.eastmoney.com', '21.push2his.eastmoney.com']
            import time as _time
            data_json = None
            for _h in _hosts:
                try:
                    r = requests.get(f'https://{_h}{url_path}', params=params,
                                     timeout=15, headers=_headers)
                    data_json = r.json()
                    if ((data_json or {}).get('data') or {}).get('klines'):
                        break
                except Exception as _e:
                    print(f"[DataManager] push2his[{_h}] fail: {str(_e)[:50]}")
                    _time.sleep(2)
            klines = ((data_json or {}).get('data') or {}).get('klines') or []
            _time.sleep(2)  # quirks 第7章频次纪律
            if not klines:
                print("[DataManager] push2his empty")
                return pd.DataFrame()
            rows = [k.split(',')[:6] for k in klines]
            df = pd.DataFrame(rows, columns=['date', 'open', 'close',
                                             'high', 'low', 'volume'])
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
            if level == 'daily':
                df['date'] = df['date'].astype(str).str[:10]
            df[['open', 'high', 'low', 'close']] = \
                df[['open', 'high', 'low', 'close']].astype(float)
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int)
            if start_date:
                df = df[df['date'] >= str(start_date)]
            if end_date:
                df = df[df['date'] <= str(end_date)]
            df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
            print(f"[DataManager] push2his {level} OK: {len(df)} rows")
            return df
        except Exception as e:
            print(f"[DataManager] push2his fail: {e}")
            return pd.DataFrame()

    def fetch_tencent_data(self, symbol, level='daily', start_date=None, end_date=None):
        """备选4: 腾讯K线（Baostock/mootdx 挂掉时的备用源，零鉴权不封IP）
        日线走 fqkline(前复权)，30分钟走 mkline(m30)。
        连续 5000+ 次后腾讯会限流返回空（非封IP），降速或换新浪即可恢复。
        """
        print(f"[DataManager] Trying Tencent ({level})...")
        try:
            import em_utils
            if level == 'daily':
                rows = em_utils.tencent_daily_kline(symbol, count=1200, adjust="qfq")
                if not rows:
                    rows = em_utils.tencent_daily_kline(symbol, count=1200, adjust="")
            elif level == '30min':
                rows = em_utils.tencent_minute_kline(symbol, period="m30", count=320)
            elif level in ('week', 'month'):
                # v5.4.1(P3): 静默错配修复——旧映射 week/month→m30, 调用方拿到
                # 的是 30 分钟级数据冒充周/月线(K线形态完全不同)。明确拒绝并提示。
                print(f"[DataManager] Tencent 不支持 {level} 级别(旧实现静默返回m30冒充), 请用 Baostock 周月线或 daily 重采样")
                return pd.DataFrame()
            else:
                # 其他级别（60min/15min/5min）走分钟K端点
                period_map = {'60min': 'm60', '15min': 'm15', '5min': 'm5'}
                period = period_map.get(level)
                if not period:
                    return pd.DataFrame()
                rows = em_utils.tencent_minute_kline(symbol, period=period, count=320)
            if not rows:
                print("[DataManager] Tencent empty")
                return pd.DataFrame()
            df = pd.DataFrame(rows)
            # 列名对齐 DataManager 标准：date/open/high/low/close/volume
            df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low',
                                    'close': 'close', 'volume': 'volume'})
            df['date'] = df['date'].astype(str).str[:10] if level == 'daily' else df['date']
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
            df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
            if start_date:
                df = df[df['date'] >= str(start_date)]
            if end_date:
                df = df[df['date'] <= str(end_date)]
            print(f"[DataManager] Tencent {level} OK: {len(df)} rows, latest={df.iloc[-1]['close']:.2f}")
            return df
        except Exception as e:
            print(f"[DataManager] Tencent fail: {e}")
            return pd.DataFrame()

    def fetch_baostock_data(self, symbol, level='daily', start_date=None, end_date=None, force_no_adjust=False):
        """
        获取 Baostock 数据（主数据源）
        force_no_adjust: 是否强制返回不复权原始价（用于特殊情况）
        
        自动重连：连续失败 3 次后强制重登；单次调用失败后重试 1 次
        """
        print(f"[DataManager] Trying Baostock ({level})...")

        # v5.4(B-02): 锁内执行登录+抓取+重试全程（见文件头 B-02 注释）
        with (_BS_LOCK if _BS_LOCK is not None else contextlib.nullcontext()):
            # 连续失败达阈值时强制重新登录
            if self._bs_failures >= 3:
                self._login_baostock(force=True)

            if not self._login_baostock():
                return pd.DataFrame()

            try:
                df = self._baostock_fetch_inner(symbol, level, start_date, end_date, force_no_adjust)
                if not df.empty:
                    self._baostock_on_success()
                    return df
                # 空数据不算失败（可能是新股无历史数据）
                return pd.DataFrame()

            except Exception as e:
                msg = str(e)[:80]
                print(f"[DataManager] Baostock error: {msg}")

                # 标记失败，尝试重登后重试一次
                self._mark_baostock_failure()
                if self._login_baostock(force=True):
                    print(f"[DataManager] Baostock 重登成功，重试...")
                    try:
                        df = self._baostock_fetch_inner(symbol, level, start_date, end_date, force_no_adjust)
                        if not df.empty:
                            self._baostock_on_success()
                            return df
                    except Exception as retry_e:
                        print(f"[DataManager] Baostock 重试仍然失败: {str(retry_e)[:80]}")
                return pd.DataFrame()

    def _baostock_fetch_inner(self, symbol, level, start_date, end_date, force_no_adjust):
        """Baostock 数据获取核心逻辑（不含重试）"""
        bs_symbol = f"sh.{symbol}" if (symbol.startswith('6') or symbol.startswith('5')) else f"sz.{symbol}"
        fields = "date,open,high,low,close,volume"
        
        # 获取 K 线数据
        if level == 'daily':
            adjust_param = '2' if not force_no_adjust else '3'
            rs = bs.query_history_k_data_plus(
                code=bs_symbol, 
                frequency='d', 
                start_date=start_date,
                end_date=end_date, 
                fields=fields,
                adjustflag=adjust_param
            ) 
            print(f"[DataManager] Baostock: Daily data with adjustflag='{adjust_param}' (2=前复权, 3=不复权)")
        elif level == '30min':
            adjust_param = '2' if not force_no_adjust else '3'
            rs = bs.query_history_k_data_plus(
                code=bs_symbol, 
                frequency='30', 
                start_date=start_date,
                end_date=end_date, 
                fields=fields,
                adjustflag=adjust_param
            )
            print(f"[DataManager] Baostock: 30min data with adjustflag='{adjust_param}' (前复权)")
        else:
            return pd.DataFrame()
        
        # 手动迭代获取数据（避免Baostock内部使用已废弃的df.append()）
        rows = []
        while rs.next():
            row = rs.get_row_data()
            rows.append(row)
        
        if not rows:
            return pd.DataFrame()
        
        # 从 Baostock 返回的 row 是字符串列表，顺序同 fields
        col_names = ['date', 'open', 'high', 'low', 'close', 'volume']
        dict_rows = []
        for r in rows:
            if r and len(r) == len(col_names):
                dict_rows.append(dict(zip(col_names, r)))
        
        df = pd.DataFrame(dict_rows)
        if df.empty:
            return pd.DataFrame()
        
        # 类型转换
        for col in ['open', 'high', 'low', 'close']:
            df[col] = df[col].astype(float)
        # 成交量可能有空字符串，转为0
        df['volume'] = df['volume'].fillna('0').replace('', '0').astype(float).astype(int)
        
        if level == 'daily' and not force_no_adjust:
            print(f"[DataManager] Baostock: Forward-adjusted daily. Latest close = {df['close'].iloc[-1]:.2f}")
        elif level == '30min' and not force_no_adjust:
            print(f"[DataManager] Baostock: Forward-adjusted 30min. Latest close = {df['close'].iloc[-1]:.2f}")
        
        return df

    def get_klines(self, symbol, level='daily', start_date=None, end_date=None, use_cache=True, cache_ttl_hours=None):
        """
        多源故障转移获取K线数据
        优先级链: Baostock → efinance → AkShare(Sina) → AkShare(EM) → investoday(Agent MCP, 兜底)

        v5.1（2026-08-22）缓存新鲜度判据重构：
            旧：mtime TTL（日线24h/30min 6h）→ 周末/假期运行时全部误判过期，
                触发 Baostock 全量重拉（实测卡死 Phase 1 十分钟以上）。
            新：数据截止日判据 —— cache_max_date >= 最近已完结交易日 即 HIT；
                覆盖校验增加"次新股历史完整"豁免标记（_mark_history_complete）。
            cache_ttl_hours 参数保留但仅作兜底硬上限（默认值×7，且≥7天），
            防止 date 判据异常时缓存被无限期使用。

        Args:
            use_cache: 是否使用 Parquet 缓存（默认 True）
            cache_ttl_hours: 兜底硬TTL基数（小时）。默认日线24h，30min线6h；实际硬上限为其7倍
        """
        # 默认TTL基数：日线24小时，30分钟线6小时（v5.1 起仅作兜底硬TTL）
        if cache_ttl_hours is None:
            cache_ttl_hours = 24 if level == 'daily' else 6

        # v5.1: 记录重拉原因与旧缓存起点，供保存段决定是否写历史完整标记
        _backfill_reason = None      # None | 'stale' | 'coverage'
        _old_cache_min = None

        cache_path = self._get_cache_path(symbol, level)

        # ===== 检查缓存 =====
        if use_cache and os.path.exists(cache_path):
            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(cache_path))
                age_hours = (datetime.now() - file_mtime).total_seconds() / 3600
                _hard_ttl = max(cache_ttl_hours * 7, 168)   # 兜底硬TTL ≥7天
                if age_hours >= _hard_ttl:
                    print(f"[DataManager] Cache hard-expired ({age_hours:.0f}h > {_hard_ttl}h), re-fetching...")
                    _backfill_reason = 'stale'
                else:
                    df = pd.read_parquet(cache_path)
                    if not df.empty and 'date' in df.columns:
                        # ── v5.1 新鲜度：数据截止日判据（替代 mtime TTL）──
                        _cache_max_date = str(df['date'].max())[:10]
                        _expected = self._expected_latest_trade_date(level, end_date)
                        if _cache_max_date < _expected:
                            print(f"[DataManager] Cache stale: max={_cache_max_date} < expected={_expected} ({level})")
                            # ── v5.2: 先增量补拉 [cache_max, today]，避免全量重拉 ──
                            # 增量只拉 1~3 天数据（秒级）；拉回空说明缓存已最新
                            # （假期/停牌）；异常或疑似除权则回退全量。
                            # v5.3.1(P1-3): 长假期间日期判据恒 stale, 每股每轮
                            # 重复网探。4小时内已探测"无新数据"的直接视为最新。
                            if self._recently_probed_empty(symbol, level):
                                print(f"[DataManager] [节流] 4h内已探测无新数据({symbol} {level}), 视为最新")
                            else:
                                _old_close = None
                                try:
                                    _tail = df[df['date'].astype(str).str[:10] == _cache_max_date]
                                    if not _tail.empty:
                                        _old_close = float(_tail.iloc[0]['close'])
                                except Exception:
                                    _old_close = None
                                _inc_df = self._try_incremental_fetch(symbol, level, _cache_max_date, _old_close)
                                if _inc_df is None:
                                    print(f"[DataManager] 增量补拉不可用，回退全量拉取")
                                    _backfill_reason = 'stale'
                                elif _inc_df.empty:
                                    print(f"[DataManager] 增量无新数据(假期/停牌/已最新)，缓存视为最新")
                                    # v5.3.1(P1-3): 记录探测时间戳——os.utime 对日期
                                    # 判据无效, 真正的节流锚点在这里
                                    self._mark_probe_empty(symbol, level)
                                else:
                                    df = pd.concat([df, _inc_df], ignore_index=True)
                                    # v5.3.1(P0-1修复): 去重键按级别区分——
                                    #   daily: 按日去重 keep last（同日仅1根，原逻辑安全）
                                    #   30min: 禁止按日期列去重（同日8根bar会全部被压成
                                    #   1根，摧毁日内结构并经 _save_to_sqlite 污染主库）！
                                    #   改为"按日替换"：移除与增量重叠日期的旧行后追加，
                                    #   行序即时序（增量行序=Baostock返回顺序=时间升序）
                                    if level == 'daily':
                                        df['_ds'] = df['date'].astype(str).str[:10]
                                        df = df.sort_values('_ds').drop_duplicates(subset='_ds', keep='last').drop(columns=['_ds'])
                                    else:
                                        _inc_days = set(_inc_df['date'].astype(str).str[:10])
                                        df = df[~df['date'].astype(str).str[:10].isin(_inc_days)]
                                        df = pd.concat([df, _inc_df], ignore_index=True)
                                    try:
                                        df.to_parquet(cache_path)
                                        print(f"[DataManager] Cache UPDATED: +{len(_inc_df)} rows → max={str(df['date'].max())[:10]}")
                                        try:
                                            self._save_to_sqlite(symbol, level, df)
                                        except Exception as e:
                                            print(f"[DataManager] SQLite write error: {e}")
                                    except Exception as e:
                                        print(f"[DataManager] Cache write error: {e}")
                                    _cache_max_date = str(df['date'].max())[:10]
                        if _backfill_reason is None:
                            # ── 覆盖校验（v4.2 起；v5.0.1 七天容差；v5.1 次新股豁免）──
                            _covered = True
                            if start_date:
                                _cache_min_date = str(df['date'].min())[:10]
                                _req_date = str(start_date)[:10]
                                if _cache_min_date > _req_date:
                                    try:
                                        _gap_days = abs((datetime.strptime(_req_date, "%Y-%m-%d")
                                                     - datetime.strptime(_cache_min_date, "%Y-%m-%d")).days)
                                    except Exception:
                                        _gap_days = 0
                                    if self._is_history_complete(symbol, level):
                                        print(f"[DataManager] Cache coverage gap tolerated (history-complete): min={_cache_min_date} vs req={start_date}")
                                    elif _gap_days <= 7:
                                        print(f"[DataManager] Cache coverage OK (gap={_gap_days}d <= 7d): min={_cache_min_date} vs req={start_date}")
                                    else:
                                        _covered = False
                                        _backfill_reason = 'coverage'
                                        _old_cache_min = _cache_min_date
                                        print(f"[DataManager] Cache coverage insufficient: min={_cache_min_date} > req={start_date} (gap={_gap_days}d), re-fetching... [若重拉无更早数据将标记历史完整]")
                            if _covered:
                                # 应用日期过滤
                                if start_date:
                                    df['_date_str'] = df['date'].astype(str).str[:10]
                                    df = df[df['_date_str'] >= str(start_date)]
                                    df = df.drop(columns=['_date_str'])
                                if end_date and 'date' in df.columns:
                                    df['_date_str'] = df['date'].astype(str).str[:10]
                                    df = df[df['_date_str'] <= str(end_date)]
                                    df = df.drop(columns=['_date_str'])
                                if not df.empty:
                                    try:
                                        os.utime(cache_path, None)  # touch mtime，供外部监控参考
                                    except Exception:
                                        pass
                                    print(f"[DataManager] Cache HIT: {symbol} {level} (max={_cache_max_date}, expected={_expected}, {len(df)} rows)")
                                    # v5.4(B-04): 缓存命中早退路径同样清理残留失败
                                    # 标记/陈旧fallback（与源链成功路径同口径）
                                    if _HAS_HELPER:
                                        with contextlib.suppress(Exception):
                                            clear_agent_fallback(symbol, level)
                                    return df
                                print(f"[DataManager] Cache empty after date filter, re-fetching...")
                        # stale / coverage 分支落到下方 API 拉取
                    else:
                        print(f"[DataManager] Cache empty/corrupt, re-fetching...")
                        _backfill_reason = _backfill_reason or 'stale'
            except Exception as e:
                print(f"[DataManager] Cache read error, re-fetching: {e}")
                _backfill_reason = _backfill_reason or 'stale'
        
        # ===== 获取数据 =====
        # 0. v5.3 本地主库优先（零网络）：每日 21:30 任务维护，通常已含最新已完结交易日。
        #    新鲜度校验：主库 max < expected（任务未跑/盘中窗口）时仍转网络源，保证不回退数据时效。
        #    v5.3.4(审计P0-6): 仅日线允许本地库命中——kline_30min 表的 date 被截断到"日"
        #    （见 _save_to_sqlite），读回即丢失日内时序，且会经下方保存块回写 parquet，
        #    永久损毁 30min 缓存。30min 一律走网络源。
        df = pd.DataFrame()
        if level == 'daily':
            df = self._local_db_fetch(symbol, level, start_date, end_date)
        if not df.empty:
            _ldb_max = str(df['date'].max())[:10]
            _exp = self._expected_latest_trade_date(level, end_date)
            if end_date or _ldb_max >= _exp:
                print(f"[DataManager] Local-DB HIT: {symbol} {level} (max={_ldb_max}, {len(df)} rows, 零网络)")
            else:
                print(f"[DataManager] 本地主库偏旧(max={_ldb_max} < expected={_exp}，每日同步未跑?)，转网络源")
                df = pd.DataFrame()

        # 1. 主数据源：Baostock
        if df.empty:
            df = self.fetch_baostock_data(symbol, level, start_date, end_date)
        
        # 2. 备选1：efinance
        if df.empty:
            df = self.fetch_efinance_data(symbol, level, start_date, end_date)
        
        # 3. 备选2：AkShare 新浪源（稳定绕过东方财富网络限流）
        if df.empty:
            df = self.fetch_akshare_sina_data(symbol, level, start_date, end_date)
        
        # 4. 备选3：AkShare 东方财富源
        if df.empty:
            df = self.fetch_akshare_data(symbol, level, start_date, end_date)

        # 4.2 备选3.5：东财 push2his 直连（v5.4 DS-01）——AKShare 封装失败时
        # 同秒可用的同源直连通道（模板/纪律见 akshare-quirks 第7章）
        if df.empty:
            df = self.fetch_push2_data(symbol, level, start_date, end_date)

        # 4.5 备选4：腾讯K线（Baostock/mootdx/AkShare 全挂时的备用源，零鉴权不封IP，前复权）
        if df.empty:
            df = self.fetch_tencent_data(symbol, level, start_date, end_date)
        
        # 4.8 v5.3 本地主库最终兜底（在线源全挂时的离线保障；仅日线，理由同上 P0-6）
        if df.empty and level == 'daily':
            df = self._local_db_fetch(symbol, level, start_date, end_date)

        # 5. Agent 层兜底：检查 investoday MCP 预置的 fallback 数据
        if df.empty and _HAS_HELPER:
            fallback_path = check_agent_fallback(symbol, level)
            if fallback_path:
                print(f"[DataManager] Investoday MCP fallback FOUND: {fallback_path}")
                try:
                    df = pd.read_parquet(fallback_path)
                    if not df.empty:
                        print(f"[DataManager] Investoday MCP fallback OK: {len(df)} rows")
                        # 清理标记文件
                        clear_agent_fallback(symbol, level)
                    else:
                        print(f"[DataManager] Investoday MCP fallback file empty")
                        df = pd.DataFrame()
                except Exception as e:
                    print(f"[DataManager] Investoday MCP fallback read error: {e}")
                    df = pd.DataFrame()

        # 6. 如果仍然失败，写入标记文件通知 Agent
        if df.empty and _HAS_HELPER:
            mark_python_sources_failed(symbol, level)
            print(f"[DataManager] 已写入 .source_failed_{symbol}_{level}.flag，等待 Agent 用 investoday MCP 兜底")

        # v5.4(B-04): 网络源恢复成功后清理历史失败标记与陈旧 fallback 文件——
        # 旧实现只在 Agent fallback 消费路径清(L723), Python 源自愈后 flag 残留,
        # cron 会永远重复做无用的 MCP 兜底; 且 check_agent_fallback 只看 parquet
        # 是否存在(不看flag), 陈旧 parquet 会在未来故障时被当作"新鲜兜底"误食。
        if not df.empty and _HAS_HELPER:
            with contextlib.suppress(Exception):
                clear_agent_fallback(symbol, level)

        if df.empty:
            print(f"[DataManager] 所有数据源均失败: {symbol} {level}")
            return pd.DataFrame()
        
        # ===== 保存缓存 =====
        if use_cache:
            # v5.3.1(P1-1): 覆盖保护——全量拉取可能由短历史源(tencent 1200上限/
            # sina 等)成功, 无脑覆盖会把缓存静默截断且 history-complete 标记
            # 把缩水固化。新数据显著短于旧缓存时改为 union 合并。
            try:
                if os.path.exists(cache_path):
                    try:
                        _old = pd.read_parquet(cache_path)
                    except Exception:
                        _old = None
                    if _old is not None and not _old.empty and not df.empty and 'date' in df.columns and 'date' in _old.columns:
                        _old_min = str(_old['date'].min())[:10]
                        _new_min = str(df['date'].min())[:10]
                        if _new_min > _old_min:
                            print(f"[DataManager] ⚠ 重拉起点({_new_min})晚于旧缓存({_old_min})，"
                                  f"重叠区间以新数据为准合并(防历史缩水)")
                            # v5.3.4(缓存清洗): 旧实现 concat 全量 + drop_duplicates(keep='last')
                            # 依赖"整行完全相等"判重——跨除权的前复权漂移使同日bar数值不同
                            # 而被全部保留，即冗余根源(2026-08-23盘点: 243只缓存1088万行,
                            # 74%为冗余, 单股最高97%)。改为: 仅保留旧缓存中早于新数据起点
                            # 的部分, 重叠区间一律采用新拉取数据。
                            _old_dates = _old['date'].astype(str).str.slice(0, 10)
                            df = pd.concat([_old[_old_dates < _new_min], df], ignore_index=True)
                            if level == 'daily':
                                df = df.drop_duplicates(subset=['date'], keep='last')
                            df = df.sort_values('date', kind='stable').reset_index(drop=True)
                df.to_parquet(cache_path)
                print(f"[DataManager] Cache SAVED: {cache_path} ({len(df)} rows)")
            except Exception as e:
                print(f"[DataManager] Cache write error: {e}")
            # 同步写入 SQLite（供 DBHub 自然语言查询）
            try:
                self._save_to_sqlite(symbol, level, df)
            except Exception as e:
                print(f"[DataManager] SQLite write error: {e}")
            # v5.1: 因覆盖不足触发的重拉，若未获得更早数据 → 标记历史完整，
            # 后续运行豁免覆盖校验（典型：次新股上市晚于请求起点，重拉也拉不到）
            if (_backfill_reason == 'coverage' and not df.empty and 'date' in df.columns):
                _new_min = str(df['date'].min())[:10]
                if _old_cache_min is not None and _new_min >= _old_cache_min:
                    self._mark_history_complete(symbol, level, _new_min)
                    print(f"[DataManager] 重拉未获更早数据(min={_new_min} >= 旧 {_old_cache_min})，已标记 {symbol} {level} 历史完整")

        return df

    def _save_to_sqlite(self, symbol, level, df):
        """同步 K 线数据到 SQLite（DBHub 查询层）"""
        db_path = os.path.join(self.cache_dir, "chanlun_klines.db")
        conn = sqlite3.connect(db_path)
        try:
            if level == 'daily':
                rows = [
                    (symbol, r['date'], r['open'], r['high'], r['low'], r['close'], int(r['volume']))
                    for _, r in df.iterrows()
                ]
                conn.executemany(
                    "INSERT OR REPLACE INTO kline_daily "
                    "(stock_code, date, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", rows
                )
            else:
                # v5.2: 先删后插保证幂等（30min 表无主键，增量更新时防重复行）
                # v5.3.1(P1-6修复): DELETE 加上界——带 end_date 的历史区间请求
                # 不得截断主库中该股更晚的数据；写入 date 统一截断到"日"
                # （主库 30min 定位为查询层，格式一致；完整行序保留在 parquet 缓存）
                _min_d = str(df['date'].min())[:10]
                _max_d = str(df['date'].max())[:10]
                conn.execute(
                    "DELETE FROM kline_30min WHERE stock_code=? AND date>=? AND date<=?",
                    (symbol, _min_d, _max_d)
                )
                rows = [
                    (symbol, str(r['date'])[:10], r['open'], r['high'], r['low'], r['close'], int(r['volume']))
                    for _, r in df.iterrows()
                ]
                conn.executemany(
                    "INSERT INTO kline_30min "
                    "(stock_code, date, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", rows
                )
            conn.commit()
        finally:
            conn.close()

    def to_json_list(self, df):
        return df.to_dict('records')

    # __del__ 已移除：Baostock session 由 baostock_utils 统一管理，
    # DataManager 不应在其生命周期结束时调用 bs.logout()，
    # 否则会影响其他共享同一 session 的模块。
