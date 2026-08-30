#!/usr/bin/env python
"""一键选股全流程：Phase1→Phase2+3→Alpha→五维报告覆盖→归档历史汇总
v5.4(C-07): 失败不再预先删报告；Phase1 超时可 --phase1-timeout=N 参数化（默认1200s）"""
import subprocess, sys, os, shutil, json, threading
from collections import deque
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

BASE = "D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core"
OUTPUT = "D:/常用文件/股票池推荐股"

# Windows 兼容（2026-08-22）：原写法在 shell=True(cmd.exe) 下用 bash 风格前缀
# `HOME=... PYTHONPATH=... python x.py` 会报"不是内部或外部命令"，导致 Alpha 步骤失败、
# 四维重算静默回退 alpha=50 中性分。改为主进程设置后由全部子进程继承。
os.environ.setdefault("HOME", "C:/Users/13120")
_zoo = "D:/常用文件/DeepSeek Harness项目/trading-skills/alpha-zoo"
if _zoo not in os.environ.get("PYTHONPATH", ""):
    os.environ["PYTHONPATH"] = _zoo + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")

def run(cmd, desc, timeout=1200):
    print(f"\n{'='*60}")
    print(f"▶ {desc}")
    print(f"{'='*60}")
    # v5.3.1(P1-5): ①去 shell=True——timeout 只能杀 cmd.exe, python 孙进程
    # 成为孤儿继续写共享文件(与后续步骤竞态); ②显式 utf-8——text=True 缺省用
    # locale(cp936), 子进程打印 '✓/✗/▶' 直接 UnicodeEncodeError 崩溃
    # ("手动跑正常、编排跑挂"的根源); ③注入 PYTHONIOENCODING 双保险。
    # v2026-08-28(A1 可观测性): 旧实现 capture_output 吞掉子进程全部 stdout,
    # 退出后才打印 tail[-1000:]; 叠加本脚本 stdout 块缓冲, 后台/管道跑时全程
    # 黑箱(2026-08-28 实测: Phase 2+3 的 ~15 分钟内可见输出=0, 只能靠文件
    # mtime 侧信道判断死活)。新实现: Popen 逐行实时转发(合并流) + 尾部环形
    # 缓冲供报错上下文 + Timer 严格 timeout 杀进程 + 子进程 unbuffered。
    parts = cmd.split() if isinstance(cmd, str) else list(cmd)
    if parts and parts[0] == "python":
        parts[0] = sys.executable
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    tail = deque(maxlen=40)
    proc = subprocess.Popen(parts, cwd=BASE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, env=env)
    _killed = {"flag": False}

    def _on_timeout():
        _killed["flag"] = True
        proc.kill()

    _timer = threading.Timer(timeout, _on_timeout)
    _timer.start()
    try:
        if proc.stdout is not None:
            for _line in proc.stdout:
                tail.append(_line.rstrip("\n"))
                sys.stdout.write(_line if _line.endswith("\n") else _line + "\n")
                sys.stdout.flush()
        _rc = proc.wait()
    finally:
        _timer.cancel()
    if _killed["flag"]:
        raise RuntimeError(f"{desc} 超时({timeout}s)，中止全流程") from None
    if _rc != 0:
        # v5.3.1(P0-2): 子步骤失败必须中止——继续执行会用昨日陈旧中间产物
        # (scanner_cache/phase2_results 均为跨天持久文件)生成"今日"报告,
        # 与历史 confidence_score 假100分事故同构。抛异常中止并保留现场。
        print("[tail] 失败前最后输出:")
        for _l in list(tail)[-15:]:
            print(f"  | {_l}")
        raise RuntimeError(f"{desc} 失败 (exit={_rc})，中止全流程防止陈旧数据流入报告")
    return SimpleNamespace(returncode=_rc, stdout="", stderr="")

def archive_old_summaries():
    """归档历史汇总表到 历史汇总_bak/（保留当日）。

    v5.4(C-07): 取代旧的先行 cleanup()——旧顺序在 Phase1 之前就删光昨日
    个股报告目录+移走历史汇总，一旦 Phase1 超时/失败，系统处于"昨日报告
    已毁、今日报告未生"的空窗。现行职责划分:
      - 个股目录的陈旧清理 → pool_screener.generate_reports 内置逻辑
        (v5.0.1: 生成前删除不在推荐列表中的旧文件夹)，天然只在新报告
        即将落盘时清理，无空窗风险;
      - 历史汇总表归档 → 本函数，仅在**全流程成功后**调用。
    终审A4(2026-08-23): 动态日期 glob 匹配保留。"""
    print(f"\n{'='*60}")
    print(f"▶ 归档历史汇总表")
    print(f"{'='*60}")

    backup = Path(OUTPUT) / "历史汇总_bak"
    backup.mkdir(parents=True, exist_ok=True)

    _today = datetime.now().strftime("%Y-%m-%d")
    _moved = 0
    for _pat in ("扫描汇总_*.md", "扫描汇总_*.xlsx", "扫描汇总.md", "扫描汇总.xlsx"):
        for _f in Path(OUTPUT).glob(_pat):
            if _today in _f.name:
                continue  # 当日产物保留在主目录
            shutil.move(str(_f), str(backup / _f.name))
            _moved += 1
            print(f"  归档: {_f.name}")
    if _moved == 0:
        print("  无历史汇总需归档")

def resocre_with_alpha():
    """从 phase2_results.json 加载含 alpha 的数据，重算四维，覆盖报告"""
    sys.path.insert(0, BASE)
    os.chdir(BASE)

    # v5.3.1(P0-2): 新鲜度校验——只接受本次运行 Phase2 成功后写入的标记,
    # 防止手动乱序调用时读到昨日陈旧 phase2_results.json
    marker = Path(BASE) / ".phase2_fresh.marker"
    if not marker.exists():
        raise RuntimeError("缺少 .phase2_fresh.marker — Phase 2 未在本次运行中成功完成, 拒绝重算")
    from composite_scorer import compute_3d_score, buy_level_from_type
    from config_loader import W_TECH, W_FUND, W_ALPHA, W_NEWS, MANUAL_BLACKLIST

    with open('.phase2_results.json', encoding='utf-8') as f:
        scored = json.load(f)
    if not scored:
        raise RuntimeError("phase2_results.json 为空")
    
    print(f"加载 {len(scored)} 只含 alpha 的评分")
    
    w_sum = W_TECH + W_FUND + W_ALPHA + W_NEWS
    print(f"四维权重: 技术={W_TECH/w_sum*100:.0f}% 基本面={W_FUND/w_sum*100:.0f}% alpha={W_ALPHA/w_sum*100:.0f}% 消息={W_NEWS/w_sum*100:.0f}%")
    
    # 重算四维综合分
    for s in scored:
        result = compute_3d_score(
            tech_score=s.get('tech_score', 50),
            fund_score=s.get('fund_score', 50),
            alpha_score=s.get('alpha_score', 50),
            news_score=s.get('news_score', 50),
            w_tech=W_TECH, w_fund=W_FUND, w_alpha=W_ALPHA, w_news=W_NEWS,
            code=s['code'], name=s['name'],
            news_detail=s.get('news_detail', ''),
            resonance_penalty=True,
            # v5.3.1(F1): 重算点必须传 buy_level——缺省 0 会被当反转买点降档,
            # 全体仓位系统性压低(phase2 大量 position='15%' 的根源)
            buy_level=buy_level_from_type(s.get('buy_type', '')),
            # v5.3.1(F2): 四维重算点接通 severe 链
            risk_reasons=(s.get('risk_reasons') or []) + (s.get('severe_reasons') or []),
            manual_blacklist=MANUAL_BLACKLIST,
            # v5.3.3(E-1/E-2): 买卖冲突仲裁与观察型标记跨阶段透传
            recent_top_sell=bool(s.get('sell_conflict') or s.get('suppressed_by_sell')),
            observational=bool(s.get('observational')),
        )
        s['composite'] = result.composite
        s['grade'] = result.grade
        s['can_buy'] = result.can_buy
        s['sell_conflict'] = result.components.get('sell_conflict', False)
        s['observational'] = result.components.get('observational', False)
        # v5.3.1(F1): position/position_pct 必须同源更新——报告渲染层读 pct,
        # 漏更会导致 grade 与报告仓位自相矛盾(A级印15%事故)
        # v5.4.1(AUD-B-02): 补齐 M-01/C-06 在此重算点的遗漏——
        # ①regime cap 与 pool_screener/ff_rescore 同源(惰性导入防循环)；
        # ②reason 与最终分数同源重建(旧文案残留 Phase3 的"Alpha因子50分")。
        try:
            from pool_screener import _get_cached_cap as _gcc
            _pos = min(result.position, _gcc())
        except Exception:
            _pos = result.position
        s['position'] = _pos
        s['position_pct'] = f"{_pos*100:.0f}%"
        from composite_scorer import position_reason as _pos_reason
        s['reason'] = _pos_reason(result)
    
    scored.sort(key=lambda s: -s['composite'])
    
    # Top 10
    print(f"\n{'='*40}")
    print("四维综合评分 Top 10")
    print(f"{'='*40}")
    print(f"{'#':>2} {'代码':>6} {'名称':<8} {'综合':>4} {'级':>2} {'技术':>4} {'基本':>4} {'Alpha':>5} {'消息':>4}")
    for i, s in enumerate(scored[:10]):
        print(f"{i+1:>2}. {s['code']:>6} {s['name']:<8} {s['composite']:>4.0f} {s['grade']:>2s} {s.get('tech_score',50):>4.0f} {s.get('fund_score',50):>4.0f} {s.get('alpha_score',50):>5.1f} {s.get('news_score',50):>4.0f}")
    print(f"\n阈值以下(<70): {sum(1 for s in scored if s['composite'] < 70)} 只")
    
    # v2026-08-28(B 报告合并): 不再生成四维报告——四维中间产物马上被五维
    # (fund_factor_rescore --report)覆盖, 提前生成只是"汇总表×1 + 个股MD×N"
    # 的冗余 IO(2026-08-28 实测三遍生成浪费 ~5-8 分钟)。此处只写 JSON,
    # 汇总表由五维终版一次性输出。
    print("[四维重算] 完成(报告由五维终版统一生成)")
    
    # 保存更新后的 JSON（v5.3.1/F14: 原子写防中途崩溃留下半个 JSON）
    # v5.3.3(G-3): fund_data 保留可序列化子集(与 pool_screener._save_phase2_results
    # 同口径)——原整体剥离导致 ff_rescore 链报告的季报点评表退化为仅ROE一行
    _FD_KEYS = ('profitability', 'growth', 'health', 'valuation',
                'quarterly_profits', 'multi_year_data', 'data_date', 'industry')
    clean = []
    for s in scored:
        d = {k: v for k, v in s.items() if k != 'analyzer'}
        fd = s.get('fund_data')
        if isinstance(fd, dict):
            d['fund_data'] = {k: fd.get(k) for k in _FD_KEYS if k in fd}
        clean.append(d)
    _tmp = Path(BASE) / '.phase2_results.json.tmp'
    with open(_tmp, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    os.replace(str(_tmp), str(Path(BASE) / '.phase2_results.json'))
    print(f"\n已保存 4D phase2_results.json ({len(clean)} 只)")

# ====== 主流程 ======
if __name__ == "__main__":
    from datetime import datetime
    t0 = __import__('time').time()

    # v2026-08-28(A1): 本脚本 stdout/stderr 行缓冲 + 固化 utf-8——后台/管道跑时
    # 外部观察者不再等块填充才看到输出; 且不再依赖调用方设置 PYTHONIOENCODING
    # (2026-08-28 实锤: 未设该变量的管道环境下主进程走 GBK locale, print('▶')
    # 直接 UnicodeEncodeError——v5.3.1(P1-5) 只修了子进程编码, 主进程一直裸奔)
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
        sys.stderr.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass

    try:
        # 0. v5.4(C-07): 不再先行清理——昨日报告在今日产出确认前必须保全。
        #    仅清掉上轮残留的新鲜度标记——否则本轮 Phase2 失败时,
        #    resocre 的存在性校验会被旧 marker 骗过(P0-2 的 abort 在前,
        #    实际风险低, 但校验语义必须严格: "本次运行成功完成")
        _stale_marker = Path(BASE, ".phase2_fresh.marker")
        if _stale_marker.exists():
            _stale_marker.unlink()

        # 1. Phase 1
        # v5.4(C-07): timeout 默认600→1200 并参数化(--phase1-timeout=N)——
        # 缓存过期时全池重拉实测 ~231s，但网络劣化/子进程环境差异可翻倍
        _phase1_timeout = 1200
        for _a in sys.argv[1:]:
            if _a.startswith("--phase1-timeout="):
                try:
                    _phase1_timeout = int(_a.split("=", 1)[1])
                except ValueError:
                    print(f"⚠ 无效 --phase1-timeout 值: {_a}，用默认 {_phase1_timeout}s")
        run("python pool_scanner.py", "Phase 1: 缠论扫描", timeout=_phase1_timeout)

        # 2. Phase 2+3（生成 HTML + 个股 MD，analyzers 在内存）
        # v2026-08-28(B): --skip-summary——三维口径汇总表(α=50中性)不再生成,
        # 由 Step 4.5 五维重算 --report 终版一次性输出(消除三遍生成劳动)
        run("python pool_screener.py --from-cache --skip-summary",
            "Phase 2+3: 评分 + 个股报告(跳过汇总表)", timeout=1800)

        # v5.3.1(P0-2): Phase2 成功后写新鲜度标记, resocre_with_alpha 只接受本次运行的产物
        Path(BASE, ".phase2_fresh.marker").write_text(datetime.now().isoformat(), encoding="utf-8")

        # 3. Alpha 因子过滤
        run(
            "python alpha_factor_filter.py",
            "Alpha 因子过滤",
            timeout=180
        )

        # 4. 四维重算 + 覆盖报告
        resocre_with_alpha()

        # 4.5 资金面因子补扫（v1.1）：Top 30 补扫筹码/两融/资金流，五维权重重算 composite
        # tech×0.35 + fund×0.25 + alpha×0.20 + news×0.10 + ff×0.10（fund拆0.05 + alpha拆0.05）
        # --report 让五维 composite 反映到 MD/Excel 报告；约 2-3 分钟
        run("python fund_factor_rescore.py --top 30 --report", "资金面因子补扫(Top 30)", timeout=600)

        # 5. v5.4(C-07): 全流程成功后才归档历史汇总（失败路径不再毁报告）
        archive_old_summaries()
    except Exception as e:
        elapsed = __import__('time').time() - t0
        print(f"\n{'='*60}")
        print(f"❌ 流程中止: {e}")
        print(f"已运行 {elapsed:.0f}s。")
        print(f"v5.4(C-07)后失败不再预先删报告：昨日产物应仍在输出目录，")
        print(f"排查失败阶段后重跑: python run_full_4d_pipeline.py [--phase1-timeout=N]")
        print(f"{'='*60}")
        sys.exit(1)
    
    elapsed = __import__('time').time() - t0
    print(f"\n{'='*60}")
    print(f"✅ 全流程完成! 总耗时 {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"{'='*60}")
    print(f"输出目录: {OUTPUT}/")
    # 终审A4: 动态日期（与 pool_screener.generate_reports 输出名一致）
    _today = datetime.now().strftime("%Y-%m-%d")
    print(f"MD汇总表: {OUTPUT}/扫描汇总_{_today}.md")
    print(f"Excel汇总: {OUTPUT}/扫描汇总_{_today}.xlsx")
