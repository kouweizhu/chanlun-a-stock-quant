# 线段中枢完整版实现报告 (2026-05-03)

## 文件清单

| 文件 | 操作 | 说明 |
|------|:--:|------|
| `/home/zjj1990/work/chanlun_core/segment_analyzer.py` | **新建** | 完整版线段中枢模块，~1200行 |
| `/home/zjj1990/work/chanlun_core/generate_analysis.py` | 修改 | HTMLVisualizer 新增 `segment_result` 参数 |
| `/home/zjj1990/work/chanlun_core/quick_html.py` | 修改 | 自动集成 SegmentChanLunAnalyzer |
| `/home/zjj1990/work/chanlun_core/test_segment_zhongshu.py` | 已有 | 简化版测试（3笔成段，用于对比） |

## 核心架构

```
笔中枢（generate_analysis.py，不改动）       线段中枢（segment_analyzer.py，新增）
ChanLunAnalyzer                         SegmentChanLunAnalyzer
  ├ _find_zhongshus (笔中枢)              ├ find_segments (特征序列+包含+分型)
  ├ _find_buy_sell_points (B1/B2/B3)      ├ find_segment_zhongshus (含延伸保护)
  └ .analyze() → analyzer                 ├ apply_expansion (含三段保护)
                                           └ .analyze(bi_analyzer) → segment_result

                          ▼
              HTMLVisualizer(segment_result=...)
                          ▼
              HTML 双视角：🔀 线段中枢 切换按钮
```

## 线段划分算法

```
find_segments(bis):
1. 从第一笔开始，逐笔扩展线段
2. 提取特征序列（向上线段→向下笔，向下线段→向上笔）
3. 特征序列包含处理：
   - 向上线段（特征=向下笔）→ 处理方向"向下" → 取低低（取 min high, min low）
   - 向下线段（特征=向上笔）→ 处理方向"向上" → 取高高（取 max high, max low）
   - **单次遍历，不递归**（避免级联合并吞掉所有元素）
4. 寻找顶/底分型
5. 分型确认 → 线段结束（统一分割，不区分第一/第二种破坏）
   - seg_end = right_feature.bi_index - 1（确保首尾同向）
6. 后处理：合并同向线段

关键参数：
- min_bi_klines = 5（笔最少K线数，继承自 ChanLunAnalyzer）
- 线段最少3笔
- 特征序列最少3个元素才可能形成分型
```

## 中枢延伸保护（三段保护）

**这是今日最重要的修复**。原实现中枢延伸无限制，导致一个中枢"吞掉"所有后续线段。

```python
# 在 find_segment_zhongshus() 的 while 延伸循环中：
MAX_ZHONGSHU_BI = 27      # 9段×3笔/段，超过则强制切分
MAX_ZHONGSHU_DAYS = 120   # 最大存活交易日
MIN_FLUCTUATION_PCT = 0.05 # 无效横盘阈值

保护1：段数超限 → (end_idx - i) >= 9 → break
保护2：时间超限 → 距离中枢起始 > 120日 → break
保护3：无效横盘 → 中枢宽度 < 5% 且已持续 > 60日 → break
```

## 中枢扩张（未触发但逻辑完整）

扩张前提：需要≥2个中枢 → 目前只有少数票产生2个中枢。

```
扩张条件：
1. 同级别校验（段数比 ≤ 3）
2. 同方向校验（方向一致）
3. 条件1（区间重叠）OR 条件2（波动触及）
4. 三段保护检查（同上）
合并：新的 ZG/ZD = max/min，GG/DD 记录波动边界，原中枢消失
```

## 买卖点识别

- **SB1/SS1**：线段趋势背驰（需≥2个中枢对比 MACD 面积）
- **SB2/SS2**：一类买卖点后第一次反向线段不创新低/高
- **SB3/SS3**：线段突破中枢后回踩/反弹不进入

## 踩坑记录

| # | 坑 | 现象 | 根因 | 修复 |
|---|-----|------|------|------|
| 1 | 段笔数为偶数 | 4笔的段（首下尾上） | `seg_end` 取分型右侧特征的 bi_index，该笔方向与段相反 | `bi_index - 1` |
| 2 | 特征序列级联合并 | 6个元素递归合并→1个，分型无法形成 | 递归包含取最宽范围，产生"黑洞"效应 | 取低低/取高高 + 单次遍历 |
| 3 | 第二种破坏无限等待 | 段永远不分割 | `seg_start` 不变，`direction` 永久相同，pending 永远等待反向段 | 统一分割，不区分第一/第二种 |
| 4 | **中枢延伸无保护** | 1个中枢吃光所有后续段 | 延伸条件只有 "next_high>=zd and next_low<=zg" 无上限 | 27笔/120天/5% 三段保护 |
| 5 | 数据窗口太短 | 500天=329K线，段太少 | 线段比笔长8-12倍，需更多历史 | 改用1200天 |

## 18只自选股实测（1200天数据，修复后）

```
代码       名称       段   段ZS  段BS  信号
600346   恒力石化      6     2    1   ⭐ SB3
000830   鲁西化工      8     2    0
002415   海康威视      6     2    0
600309   万华化学      6     1    0
600298   安琪酵母      6     1    0
601888   中国中免      5     1    0
601155   新城控股      7     1    0
000002   万科A        5     1    0
002271   东方雨虹      5     1    0
601318   中国平安      7     1    0
300059   东方财富      5     1    0
000001   平安银行      8     1    0
601601   中国太保      5     0    0
600486   扬农化工      5     0    0
300783   三只松鼠      4     0    0
002714   牧原股份      6     0    0
601615   明阳智能      5     0    0
300772   运达股份      5     0    0

有段中枢: 13/18 (72%)，修复前 5/18 (28%)
有段BS: 1/18 — 恒力石化 SB3
```

## 核心结论

1. **段BS稀有是系统特性而非缺陷**——线段级别天然产生极少信号
2. 线段中枢的价值在于**确认/否认**笔级别的信号
3. 笔中枢给B3而线段中枢无信号 → "多一分谨慎"
4. 笔中枢+线段中枢同时给信号 → "高确定性，可拿更久"
5. 1200天数据窗口是实用折中——够长产生足够段数，不至於数据源超限

## HTML 双视角用法

```bash
cd /home/zjj1990/work/chanlun_core
python3 quick_html.py 002415
# 输出：~/.hermes/profiles/commander/analysis_reports/reports_html/002415_chanlun.html
# 打开后点击 🔀 线段中枢 切换视角
```

## 后续方向

- [ ] 组合回测对比（笔中枢 vs 线段中枢，慢牛2016-2017）
- [ ] 段BS触发条件调优（当前一买需2中枢，可考虑放宽）
- [ ] 无效中枢过滤（宽度<3%的中枢是否应该丢弃）
- [ ] 多只票批量生成HTML的脚本化
