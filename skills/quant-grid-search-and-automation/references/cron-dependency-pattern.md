# Cron 依赖管理模式

## 问题

多个 cron 任务之间存在隐式依赖（如 A500 月度回测依赖当月选股成功），
上游失败时下游静默崩溃。需要轻量级方案（不引入 Airflow）。

## 方案：Flag 文件信号

```python
from cron_utils import FlagSignals, CronLogger

# 上游脚本（pool_screener.py 末尾）
flag_path = FlagSignals.write("a500_scan_done", "2026-05-01",
                               extra={"candidates": 135, "scored": 30})

# 下游脚本（a500_backtest.py --cron 模式）
if not FlagSignals.check("a500_scan_done", "2026-05-01"):
    print("上游未完成，跳过")
    return
```

## 可用 API

| 方法 | 用途 |
|------|------|
| `FlagSignals.write(name, date, extra)` | 写入成功标记到 `signals/` 目录 |
| `FlagSignals.check(name, date)` | 检查标记是否存在 |
| `FlagSignals.read(name, date)` | 读取标记内容（JSON） |
| `FlagSignals.get_latest(name)` | 获取最新匹配标记 |
| `CronLogger("script_name")` | 统一日志到 `logs/YYYY-MM-DD/<name>.log` |

## 日志统一

所有 cron 脚本统一使用 CronLogger：
```python
logger = CronLogger("my_script")
logger.info("开始...")
logger.warn("异常")
logger.error("失败")
logger.success("完成")
```

输出同时到 stdout 和 `logs/YYYY-MM-DD/my_script.log`。

## 当前依赖链

```
pool_screener.py (手动)
  → signals/a500_scan_done_YYYY-MM-DD.flag
    → a500_backtest.py --cron 检查 → 执行或跳过
```

## 文件位置

- `cron_utils.py`: `/home/zjj1990/work/chanlun_core/cron_utils.py`
- 标记文件目录: `signals/`
- 日志目录: `logs/YYYY-MM-DD/`
