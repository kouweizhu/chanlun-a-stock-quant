# generate_report.py 渲染失败案例 — 万科A(000002, 530笔)

## 现象

`single_stock_analysis.py --code 000002 --name "万科A" --report` 执行结果：

- ✅ 退出码 0（脚本正常完成）
- ✅ `summary.modules_status` 全部为 "ok"（chanlun/fundamental/news/negative/html 均成功）
- ✅ JSON 输出包含完整的 modules 数据（缠论中枢/笔/买卖点/MACD、基本面多年度、消息面评分、负面信号、HTML路径）
- ❌ 但有 `⚠️ 报告生成失败: Traceback...` 输出到 stderr，内容是 `generate_report.py` 在 Jinja2 渲染阶段报 "服务器连接失败/接收数据异常"

## 根因分析

双重原因叠加：

1. **主因 — 超极端高笔数(530笔)**：Jinja2 模板在渲染530条笔/208个分型时，字符串拼接和 HTML 标记表生成的内存压力极大，触发 `generate_report.py` 内部超时/资源耗尽
2. **辅因 — hithink API 间歇性连接中断**：同花顺 hithink API 在某些时段返回 "服务器连接失败/接收数据异常" 写入 stderr，这些错误文本被 Jinja2 当作模板上下文的一部分加载，干扰了模板渲染流程

注：之前的 case（海康威视 54笔/208分型）同样超时但根因是 Canvas 绘制计算，与本次的 Jinja2 渲染层失败不同。

## 恢复流程

### 前提确认
```bash
cd D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core
python -c "
import json
with open('reports_json/000002_2026-06-17.json') as f:
    data = json.load(f)
m = data.get('summary', {}).get('modules_status', {})
print(f'chanlun: {m.get(\"chanlun\")}, fund: {m.get(\"fundamental\")}, news: {m.get(\"news\")}, html: {m.get(\"html\")}')
print(f'errors: {len(data.get(\"errors\", []))}')
"
```

### 数据提取与报告构建

1. **技术面数据**：从 JSON `modules.chanlun` 提取
   - `last_5_bis` — 最近5笔
   - `zhongshus` — 所有中枢（重点关注最后3个）
   - `macd_status` — MACD状态(dif/dea/macd/趋势/金叉死叉)
   - `buy_sell_points` — 最近3个买卖点

2. **基本面数据**：从 JSON `modules.fundamental` 提取
   - `multi_year_data` — 5年财务趋势
   - `profitability/growth/health/valuation` — 当前季报
   - `fundamental_score` — 评分明细

3. **消息面数据**：从 JSON `modules.news` 提取
   - `score` 和 `reason` — 综合评分
   - `detail` — 明细消息列表

4. **手动计算 MACD 背驰**（系统无自动一买输出时验证用）
   ```python
   import pandas as pd, numpy as np
   d = pd.read_parquet('data_cache/000002_daily.parquet')
   d['date'] = pd.to_datetime(d['date'])
   close = d['close'].astype(float).values
   ema_fast = pd.Series(close).ewm(span=12).mean()
   ema_slow = pd.Series(close).ewm(span=26).mean()
   dif = (ema_fast - ema_slow).values
   dea = pd.Series(dif).ewm(span=9).mean().values
   macd = 2 * (dif - dea)
   mask_enter = (d['date'] >= '进入段起点') & (d['date'] <= '进入段终点')
   mask_leave = (d['date'] >= '离开段起点') & (d['date'] <= '离开段终点')
   area_enter = abs(macd[mask_enter]).sum()
   area_leave = abs(macd[mask_leave]).sum()
   ratio = area_leave / area_enter * 100
   ```

5. **HTML文件复用**：`reports_html/{代码}_chanlun.html` 由 pipeline 早期线程生成（甚至在 generate_report.py 失败之前已执行完），直接复制到目标目录即可，**无需重跑 quick_html.py**

### 关键判断

| 判断项 | 万科A案例值 |
|:-------|:-----------|
| bi_count 估算 | 530（日线8366行，每~15行1笔） |
| quick_chanlun 是否可用 | ❌ 30s超时 |
| HTML是否存在 | ✅ (427KB，pipeline已生成) |
| generate_report 是否成功 | ❌（服务器连接失败） |
| JSON 数据是否完整 | ✅ 全部 5 个模块 |

## 教训

- 单笔笔数超200即进入极端区域，所有脚本耗时应翻倍预期
- 不要依赖 quick_chanlun.py 来确认笔数 — 500+笔时它本身也超时。改用 parquet 行数估算
- generate_report.py 失败 ≠ 数据获取失败 — 一定要检查 JSON 中 modules_status 的明细
- quick_html.py 不可重跑时检查已有文件 — pipeline 首次执行已生成 HTML