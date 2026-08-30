# 30分钟K线分析完整代码模板

**用途：** 当用户要求"看一下30分钟K线"或"30分钟是否有买点"时，使用此代码模板进行快速分析。所有代码可通过 `execute_code` 工具直接运行。

**前置条件：** `quick_chanlun.py` 已执行过（确保已缓存30分钟数据到 `data_cache/`）

---

## 🚀 一键分析模板

```python
# 30分钟K线分析 — 完整模板
# 引用自 references/30min-analysis-pattern.md
import sys
sys.path.insert(0, 'D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core')
from data_manager import DataManager
import numpy as np

dm = DataManager()
code = '600298'  # ← 替换为实际代码
bars_30 = dm.get_klines(code, level='30min')
df_30 = bars_30.sort_values('date')
print(f"30分钟数据: {len(df_30)} 行, {df_30['date'].min()} ~ {df_30['date'].max()}")

# 计算MACD
closes = df_30['close']
ema12 = closes.ewm(span=12).mean()
ema26 = closes.ewm(span=26).mean()
dif = ema12 - ema26
dea = dif.ewm(span=9).mean()
macd = 2 * (dif - dea)
df_30['dif'] = dif
df_30['dea'] = dea
df_30['macd'] = macd

# --- ① 最近10根K线 ---
print(f"\n--- 最近10根30分钟K线 ---")
for i, row in df_30.tail(10).iterrows():
    macd_arrow = "↑" if row['macd'] > 0 else "↓"
    print(f"  {row['date']} | O:{row['open']:.2f} H:{row['high']:.2f} "
          f"L:{row['low']:.2f} C:{row['close']:.2f} | "
          f"DIF:{row['dif']:.3f} DEA:{row['dea']:.3f} MACD:{macd_arrow}{abs(row['macd']):.3f}")

# --- ② MACD金叉/死叉检测 ---
latest = df_30.iloc[-1]
prev = df_30.iloc[-2]
print(f"\n--- 30分钟MACD状态 ---")
print(f"DIF={latest['dif']:.3f}, DEA={latest['dea']:.3f}")
print(f"DIF比上一根: {'↑' if latest['dif'] > prev['dif'] else '↓'} "
      f"({prev['dif']:.3f}→{latest['dif']:.3f})")
print(f"DEA比上一根: {'↑' if latest['dea'] > prev['dea'] else '↓'} "
      f"({prev['dea']:.3f}→{latest['dea']:.3f})")

# 金叉: DIF从下方上穿DEA
cross_series = df_30.tail(20)
golden_cross_detected = False
death_cross_detected = False
for i in range(1, len(cross_series)):
    if cross_series.iloc[i-1]['dif'] < cross_series.iloc[i-1]['dea'] and \
       cross_series.iloc[i]['dif'] >= cross_series.iloc[i]['dea']:
        golden_cross_detected = True
        gc_time = cross_series.iloc[i]['date']
    if cross_series.iloc[i-1]['dif'] > cross_series.iloc[i-1]['dea'] and \
       cross_series.iloc[i]['dif'] <= cross_series.iloc[i]['dea']:
        death_cross_detected = True
        dc_time = cross_series.iloc[i]['date']

if golden_cross_detected:
    print(f"✅ 30分钟MACD金叉！(最近20根内, @{gc_time})")
elif death_cross_detected:
    print(f"❌ 30分钟MACD死叉！(最近20根内, @{dc_time})")
else:
    state = "多头(DIF>DEA)" if latest['dif'] > latest['dea'] else "空头(DIF<DEA)"
    print(f"⚪ 无金叉/死叉, 当前{state}")

# DIF是否连续上升（动能在积累）
dif_vals = df_30.tail(8)['dif'].values
dif_rising = all(dif_vals[i] <= dif_vals[i+1] for i in range(len(dif_vals)-1))
if dif_rising:
    print(f"📈 DIF已连续{8}根上升, 多头动量在积累")

# --- ③ 底分型/顶分型 ---
print(f"\n--- 30分钟分型检查 ---")
idx = len(df_30) - 2
if idx >= 2:
    k1, k2, k3 = df_30.iloc[idx-1], df_30.iloc[idx], df_30.iloc[idx+1]
    if k1['low'] > k2['low'] and k3['low'] > k2['low'] and k1['close'] > k2['close']:
        print(f"🔵 底分型: {k2['date'][:16]} L={k2['low']:.2f}")
    elif k1['high'] < k2['high'] and k3['high'] < k2['high'] and k1['close'] < k2['close']:
        print(f"🔴 顶分型: {k2['date'][:16]} H={k2['high']:.2f}")
    else:
        print(f"⚪ 无明显分型")

# --- ④ 近期支撑/阻力（最近40根） ---
recent = df_30.tail(40)
min_low = recent['low'].min()
min_idx = recent['low'].idxmin()
max_high = recent['high'].max()
max_idx = recent['high'].idxmax()
latest_c = latest['close']
print(f"\n--- 30分钟支撑/阻力 ---")
print(f"40根K线最低: {min_low:.2f} @ {df_30.loc[min_idx]['date'][:16]}")
print(f"40根K线最高: {max_high:.2f} @ {df_30.loc[max_idx]['date'][:16]}")
print(f"当前价: {latest_c:.2f}")
print(f"距支撑: +{(latest_c-min_low)/min_low*100:.1f}%")
print(f"距阻力: -{(max_high-latest_c)/max_high*100:.1f}%")

# --- ⑤ 短期趋势（20根K线） ---
mid = df_30.tail(20).iloc[0]
change = (latest_c - mid['close']) / mid['close'] * 100
print(f"\n--- 短期趋势(20根K线+{change:+.2f}%) ---")
if change > 2:
    print(f"✅ 短期呈上升趋势(+{change:.1f}%)")
elif change < -2:
    print(f"❌ 短期呈下降趋势({change:.1f}%)")
else:
    print(f"⚪ 短期横盘震荡")

# --- ⑥ 日线vs30分钟矛盾判断 ---
# 此部分需要根据日线分析结果手动补充
```

## 🧠 典型多级别矛盾判断

根据大量案例分析，以下是常见的日线vs30分钟矛盾场景及报告措辞：

### 场景A：日线三卖 + 30分钟金叉（最常见）
```
日线状态: 三卖@XX.XX + 跌破中枢下沿 + MACD死叉
30分钟:   MACD金叉 + DIF连续上升 + 从低点反弹+2.9%

判断: ⚠️ 30分钟金叉为短期反弹信号, 但日线三卖未消耗,
      小级别反弹不构成大级别反转。反弹目标位通常到近期阻力
      或前向下笔起点附近, 突破需要日线级别的信号配合。
```

### 场景B：日线买点 + 30分钟死叉回调
```
日线状态: 一买@XX.XX + 底背驰确认
30分钟:   MACD死叉 + DIF下降 + 价格靠近支撑

判断: 🟡 正常回调。30分钟调整是日线买点后的健康回踩,
      可等待30分钟二次金叉作为加仓点。
```

### 场景C：日线三卖 + 30分钟死叉新低
```
日线状态: 三卖@XX.XX + 持续下跌
30分钟:   MACD死叉 + DIF创新低 + 价格持续走弱

判断: 🔴 大小级别共振下跌。30分钟和日线同步空头,
      任何做多尝试都是逆势操作, 建议回避。
```

## ⚠️ 关键注意事项

1. **30分钟数据时效性**：Baostock 30分钟数据截止到最近一个完整交易日（非实时行情），不能用于日内实时判断。当前价只是最近一个30分钟K线的收盘价。

2. **不要过度解读30分钟信号**：30分钟金叉在日线空头下的平均成功率约40%（反弹后继续下跌），远低于日线级别买点。报告中必须标注"小级别反弹不构成反转"的免责声明。

3. **30分钟不做评分**：30分钟信号仅作为报告中的附加分析区块呈现，不纳入技术面评分公式。评分只基于日线数据。

4. **缓存过期检测**：`DataManager` 缓存的30分钟数据超过6小时会过期并重新拉取。如果 `Cache EXPIRED` 提示出现，Baostock会重新下载（需3-5秒），耐心等待。
