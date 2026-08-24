# 定时自验证故障诊断记录

## 问题1: akshare 模块级导入导致启动失败

### 现象
```
Traceback (most recent call last):
  File "/home/zjj1990/work/chanlun_core/validate_tech_score.py", line 28, in <module>
    from data_manager import DataManager
  File "/home/zjj1990/work/chanlun_core/data_manager.py", line 4, in <module>
    import akshare as ak
ModuleNotFoundError: No module named 'akshare'
```

### 根因
`data_manager.py` 在模块级别导入 `akshare`，即使 Baostock 可用且是首选数据源，脚本也无法启动。

### 修复
将 `import akshare as ak` 从模块级移到两个方法内部，改为惰性导入。

---

## 问题2: validate_tech_score.py 缺失 Counter 导入

### 现象
```
NameError: name 'Counter' is not defined
```

### 修复
`from collections import defaultdict, Counter`

---

## 问题3: parquet 缓存引擎缺失导致超时 (2026-05-15)

### 现象
`auto_validate.py` 运行超过 300 秒超时，退出码 124。检查日志发现每只股票都在 `[DataManager] Trying Baostock...` 处卡住，无 `Cache HIT` 消息。

### 根因
Hermes venv 默认未安装 pyarrow/fastparquet，`pd.read_parquet()` 抛 "Unable to find a usable engine"，退化到 Baostock 逐只拉取。

### 诊断命令
```bash
cd ~/work/chanlun_core
python3 -c "import pandas as pd; df = pd.read_parquet('data_cache/600309_daily.parquet'); print('OK:', len(df), 'rows')"
# 预期输出: OK: 568 rows
# 失败输出: ImportError: Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'
```

### 修复
```bash
/home/zjj1990/.hermes/venv/bin/pip install pyarrow
```
验证：
```bash
python3 -c "import pandas as pd; df = pd.read_parquet('data_cache/600309_daily.parquet'); print('OK: cache is readable,', len(df), 'rows')"
```

### 备选：缓存 TTL 绕过
当缓存文件存在但 TTL 过期时，touch 时间戳：
```bash
touch -t $(date +%Y%m%d%H%M) ~/work/chanlun_core/data_cache/*.parquet
```
⚠️ 仅适用于缓存数据本身未过时的情况。

---

## 调试流程总结

当 `auto_validate.py` 失败时：

```bash
# 1. 查看详细错误报告
cat "/mnt/d/常用文件/回测报告/定时自验证报告/2026-05-01_validation.md"

# 2. 定位问题类型
# - ImportError → 检查导入语句
# - NameError → 检查变量定义和导入
# - AttributeError → 检查对象属性
# - 超时 (exit 124) → 检查 parquet 引擎 + 缓存状态

# 3. 修复后重新运行
cd ~/work/chanlun_core && python3 auto_validate.py
```

## 验证结果快照

### 2026-05-01（修复后）
| 指标 | 数值 |
|------|------|
| 验证股票 | 18只 |
| 买点信号 | 70个 |
| A+/A级占比 | 91.4% |
| 20日相关系数 r | 0.329 |
| 结论 | 评分模型有效 |

### 2026-05-15（修复 pyarrow 后）
| 指标 | 数值 |
|------|------|
| 验证股票 | 20只（3只索引越界❌） |
| 买点信号 | 70个 |
| 技术评分均值 | 82.7 |
| A+/A级占比 | 84.3% |
| 20日相关系数 r | 0.236 |
| 60日相关系数 r | 0.332 |
| 结论 | 分级仍然有效，60日 r>0.3 |

---

## 问题4: auto_validate.py 子进程 python 路径错误（2026-06-03）

### 现象
`auto_validate.py` 输出正常，但 `validate_tech_score.py` 返回非零。报告中看到 `ModuleNotFoundError: No module named 'pandas'`。metrics 显示 tech_score_mean=45.0, grade_A_rate=0%（历史正常值 ~83, ~86%）。

### 根因
`auto_validate.py` 第 289 行使用 `subprocess.run(f"cd {WORK_DIR} && python3 {VALIDATE_SCRIPT}", ...)`，其中 `python3` 解析到 Hermes venv 的 `/home/zjj1990/.hermes/hermes-agent/venv/bin/python3`（3.11.15），该环境没有 pandas/baostock/akshare。

项目使用的系统 python3.12（`/usr/bin/python3.12`）才有全部依赖。AGENTS.md 明确写"Python 3.12，无 venv（直接使用系统 Python）"。

### 诊断步骤
```bash
cd ~/work/chanlun_core

# 1. 确认 python3 指向 Hermes venv
which python3   # → /home/zjj1990/.hermes/hermes-agent/venv/bin/python3
python3 -c "import pandas; print('OK')"  # → ModuleNotFoundError

# 2. 确认 python3.12 可用
which python3.12  # → /usr/bin/python3.12
python3.12 -c "import pandas; print(pandas.__version__)"  # → OK

# 3. 检查 auto_validate.py 的 subprocess 调用
grep "python3" auto_validate.py  # 看第 289 行
```

### 修复
```bash
# 将 auto_validate.py 第 289 行的 python3 改为 python3.12
# 用 patch 工具做精确替换
patch --old_string="python3 {VALIDATE_SCRIPT}" --new_string="python3.12 {VALIDATE_SCRIPT}" auto_validate.py
```

### 验证
```bash
cd ~/work/chanlun_core && python3.12 auto_validate.py 2>&1
# 技术评分均值应为 ~46+（依赖评分模型质量），A+/A级占比应有实际值（非0%）
# 不再出现 ⚠️ validate_tech_score.py 返回非零: 1
```

### 影响
2026-06-01 和 2026-06-03 两次 cron 执行均受此影响，指标报警为假阳性。修复后真实指标为 tech_score_mean=46.8, grade_A_rate=4.2%——评分模型确实已崩（从118中枢扩展P0修复后开始偏离），但非数据管道问题。

### 残留问题
metrics_history.json 包含 2026-06-03 两条重复：
- 第一条: tech_score_mean=45.0, grade_A_rate=0%, signal_count=64（失败子进程写入）
- 第二条: tech_score_mean=46.8, grade_A_rate=4.2%, signal_count=72（修复后正确写入）
不影响漂移检测逻辑（同一天两条在数组中相邻），但历史统计时多占一天权重。
