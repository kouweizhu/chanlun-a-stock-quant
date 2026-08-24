"""
parquet_cache_sync.py - 将 Baostock 原始K线写入 data_manager 兼容的 Parquet 缓存

缓存规范(必须与 data_manager.fetch_baostock_data 的产出完全一致):
    - 路径:   {cache_dir}/{code}_{level}.parquet
    - 列序:   date, open, high, low, close, volume
    - date:   Baostock 原始字符串(30min 含日内时间戳, 如 "2024-08-23 10:00:00")
    - 行序:   Baostock 返回顺序 = 时间升序
    - 过滤:   无(data_manager 写缓存不过滤零成交行; 停牌过滤仅作用于主库口径)

⚠ P0-6(v5.3.4) 红线: 主库 30min 的 date 已截断到"日", 从 SQLite 回读生成缓存
  会丢失日内时序、永久损毁缓存 —— 本模块只接受调用方从 Baostock 直接取得的
  原始行, 绝不接受任何来自数据库的行。
"""

import os
from datetime import datetime

import pandas as pd

COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def _df_from_raw(raw_rows):
    """raw_rows=[(date_full,o,h,l,c,vol)] -> 规范 schema 的 DataFrame"""
    df = pd.DataFrame(raw_rows, columns=COLUMNS)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(int)
    return df


GAP_TOL_DAYS = 10  # 增量合并连续性容差(A股最长假期约8天; 超过视为可疑缺口)


def write_full(code, level, raw_rows, cache_dir):
    """整文件覆写单股缓存(全量重算场景)。

    返回 (ok, n_rows, note)。任何异常都不向上抛——缓存是次级层,
    失败由调用方记录, 不允许影响主库写入结果。
    """
    if not raw_rows:
        return False, 0, "empty raw_rows"
    path = os.path.join(cache_dir, f"{code}_{level}.parquet")
    try:
        _df_from_raw(raw_rows).to_parquet(path)
        return True, len(raw_rows), path
    except Exception as e:  # noqa: BLE001 - 见 docstring: 缓存失败不外抛
        return False, 0, f"{type(e).__name__}: {e}"


def merge_incremental(code, level, raw_rows, cache_dir):
    """增量合并单股缓存(每日增量场景)。语义对齐 data_manager 的缓存维护逻辑:

    - 日线:   旧缓存早于新数据起点的部分保留 + 新数据; 按 date 去重 keep='last'; 稳定排序;
    - 30min:  删除旧缓存中与新数据重叠"日期"的行(保行序即 时序), 新数据追加在后;
    - 守卫1:  缓存文件不存在/不可读/为空 -> skip, 不生成残缺短缓存, 留给管线全量重建;
    - 守卫2:  新数据起点晚于旧缓存末端超过 GAP_TOL_DAYS -> skip(陈旧但连续好过有洞),
              留给管线从其自身 max 起增量修复。

    返回 (status, n_rows_final, note); status in {"ok", "skip", "error"}。
    异常不外抛——缓存是次级层, 失败由调用方记录。
    """
    if not raw_rows:
        return "skip", 0, "empty raw_rows"
    path = os.path.join(cache_dir, f"{code}_{level}.parquet")
    if not os.path.exists(path):
        return "skip", 0, "no-existing-cache"
    try:
        old = pd.read_parquet(path)
        if old.empty or "date" not in old.columns:
            return "skip", 0, "empty-or-bad-existing"
        new = _df_from_raw(raw_rows)

        new_min = str(new["date"].iloc[0])[:10]
        old_d10 = old["date"].astype(str).str.slice(0, 10)
        old_max = str(old_d10.iloc[-1])
        # 守卫2: 连续性(仅当新数据整体晚于旧末端时有意义)
        if new_min > old_max:
            gap = (datetime.strptime(new_min, "%Y-%m-%d")
                   - datetime.strptime(old_max, "%Y-%m-%d")).days
            if gap > GAP_TOL_DAYS:
                return "skip", len(old), f"gap {old_max}->{new_min}"

        if level == "daily":
            kept = old[old_d10 < new_min]
            df = pd.concat([kept, new], ignore_index=True)
            df = df.drop_duplicates(subset=["date"], keep="last")
            df = df.sort_values("date", kind="stable").reset_index(drop=True)
        else:
            new_dates = {str(d)[:10] for d in new["date"]}
            kept = old[~old_d10.isin(new_dates)]
            df = pd.concat([kept, new], ignore_index=True)
            # 行序即 时序: kept 升序在前, new 接续在后, 不做全局排序

        df.to_parquet(path)
        return "ok", len(df), path
    except Exception as e:  # noqa: BLE001
        return "error", 0, f"{type(e).__name__}: {e}"
