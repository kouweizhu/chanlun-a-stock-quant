# A500 选股 Session Debug Transcripts (2026-05-29)

## 故障1: AKShare 无限挂死

### 症状
```
pool_screener.py 进程运行中(PID存在)，uptime超20分钟，但stdout卡在：
  [13] 002008 大族激光...
  0%|          | 0/4 [00:00<?, ?it/s]
  25%|██▌       | 1/4 [00:00<00:00,  4.55it/s]
  ...
```
进度条存在、进程活，但20分钟后仍无新行输出。

### 根因
AKShare 底层 `urllib` 请求无 timeout 参数。`f.result(timeout=300)` 在 concurrent.futures 层面设置超时，但无法中断 socket 级别的 `recv()`。

### 修复验证
```python
import socket
socket.setdefaulttimeout(60)  # 全局 socket 超时
```
加到 pool_screener.py 的 import 段后，API 不稳时单次调用最多等60s就抛异常退出。

### 修复后耗时对比
| 配置 | 结果 |
|------|------|
| 8线程, 无socket timeout | 22分钟卡死, 0只完成 |
| 8线程, 无socket timeout (重试) | 9分钟卡死, 0只完成 |
| 4线程, 无socket timeout | 6分钟到Batch1末尾挂起 |
| 4线程, socket timeout=60 | 11.9分钟完成(115只+30份报告) |

## 故障2: Path.home() 被Hermes profile截获

### 症状
```bash
$ python3 alpha_factor_filter.py
ModuleNotFoundError: No module named 'dbhub_panel'

$ python3 -c "from pathlib import Path; print(Path.home())"
/home/zjj1990/.hermes/profiles/commander/home/
```
Hermes background 进程改写 HOME 为 profile 目录。

### 波及文件
| 文件 | 行号 | 代码 |
|------|------|------|
| alpha_factor_filter.py | 33 | `ALPHA_ZOO_DIR = Path.home() / "work" / "alpha-zoo"` |
| dbhub_panel.py | 24 | `DBHUB_PATH = Path.home() / "work" / "chanlun_core" / "data_cache" / "chanlun_klines.db"` |

### 修复
```bash
# 必须在执行时显式覆盖 HOME 和 PYTHONPATH
HOME=/home/zjj1990 PYTHONPATH=/home/zjj1990/work/alpha-zoo python3 alpha_factor_filter.py
```

## 故障3: 缺alpha_factor_filter步骤 (第4维默认未激活)

### 症状
所有股票 `alpha_score=50`（中性），`W_ALPHA=0.25` 权重作废。

### 根因
```python
# pool_screener.py line 761
alpha_score = c.get("alpha_score", 50.0)  # 默认50
```
pool_screener.py 从候选字典读 alpha_score，但 alpha_factor_filter.py 从未运行。

### 验证
补跑 alpha_factor_filter.py 后 Top 10 排名变动：
| 股票 | 3D排名 | 4D排名 | alpha_score | 变化原因 |
|------|:------:|:------:|:----------:|---------|
| 恒瑞医药 | 14→ | 5 | 93.3 | 全榜最高alpha |
| 泸州老窖 | 10外 | 10 | 89.8 | 消费龙头因子强 |
| 上海机场 | 20 | 9 | 86.2 | 高基本面+高alpha共振 |
| 柏楚电子 | 3 | 22 | 26.0 | 技术面好但截面因子弱 |
| 华工科技 | 18 | 115(末) | 9.8 | 因子全榜倒数 |

## 故障4: 四维重算(Step 4)后 HTML 缠论报告丢失

### 症状
`run_full_4d_pipeline.py` 或手动执行 Step 4 后，输出日志显示全部30只 `HTML=✗ MD=✓`。

### 根因
Step 4 用内联 Python 片段从 `.phase2_results.json` 加载数据后调用 `generate_reports(scored)`。该函数生成 MD 报告时只需 JSON 数据字段，但生成 HTML 缠论报告时需要 `ChanLunAnalyzer` 对象（已序列化的买卖点、中枢、笔信息）。因为 Step 4 是独立的 Python 进程，无法使用 Phase 2+3 运行时创建的内存 analyzer 对象。

### 影响范围
只有**同时**出现在 Phase 2+3 首次 Top 30 和 四维重算 Top 30 的股票才有 HTML。新入围的股票丢失 HTML。

### 全流程实际结果（2026-05-29）
| 项目 | 数量 |
|------|:----:|
| 首次Phase2+3 Top30 (含Alpha, 旧权重) | 30只，全部 HTML=✓ |
| 四维重算后新 Top30 (含Alpha, 新权重 35/30/25/10) | 30只，22只有HTML (重叠)，8只 HTML=✗ |
| quick_html.py 修补 | 8只全部修复 |

**8只缺失HTML的股票**：中国平安(601318)、九安医疗(002432)、兆易创新(603986)、南方航空(600029)、国电电力(600795)、平安银行(000001)、泸州老窖(000568)、赛轮轮胎(601058)

### 修复命令
```bash
cd /home/zjj1990/work/chanlun_core
# 1) 批量生成8只HTML
python3 quick_html.py 601318 002432 603986 600029 600795 000001 000568 601058

# 2) 复制到输出目录
for code in 601318 002432 603986 600029 600795 000001 000568 601058; do
  src="reports_html/${code}_chanlun.html"
  dst_dir=$(ls -d /mnt/d/常用文件/股票池推荐股/*${code}*/ 2>/dev/null)
  if [ -n "$dst_dir" ] && [ -f "$src" ]; then
    cp "$src" "${dst_dir}/${code}_chanlun.html"
  fi
done
```

### 注意
`quick_html.py` 内嵌的股票名称映射表（第17-26行）较陈旧，少量股票（如 002432）显示为代码而非名称。不影响 HTML 内容的缠论分析质量（笔、中枢、买卖点标记正确）。
