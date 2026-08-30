#!/usr/bin/env python
"""
single_stock_analysis.py — A股统一三维分析脚本 v1.0

将原本 5 个独立子进程（quick_chanlun + hithink_fundamental + news_detail_report
+ check_negative_news + quick_html）合并为 1 次 Python 执行。

用法:
    python single_stock_analysis.py --code 600872 --name "中炬高新"
    python single_stock_analysis.py --code 600872              # 无名称时用代码代替
    python single_stock_analysis.py --code 600872 --output result.json
    python single_stock_analysis.py --code 600872 --report   # 附带生成Markdown报告

输出: stdout JSON（合并 5 个模块的分析结果）
"""

import sys
import os
import json
import argparse
import threading
import tempfile
from io import StringIO
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, wait

# 确保能找到 chanlun_core 下的模块
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CHANLUN_CORE = r"D:/常用文件/DeepSeek Harness项目/trading-skills/chanlun_core"
if _CHANLUN_CORE not in sys.path:
    sys.path.insert(0, _CHANLUN_CORE)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# ============================================================
# 辅助：线程路由式 stdout 捕获（v5.4 B-15 根修）
# ============================================================

class _ThreadRoutedStdout:
    """进程级单例代理：按线程路由 stdout。

    v5.4(B-15): 旧实现把 _silence_stdout() 套在主线程 future.result() 外——
    子模块的 print 发生在工作线程、执行期在 result() 之前, 捕获窗口完全错位。
    第一版修复用"每线程换 sys.stdout"存在并发恢复链竞态(实测会把全局 stdout
    泄漏到死缓冲)。本代理只安装一次, 主线程/未注册线程写操作全部透传真实
    stdout, 工作线程经 start_capture/end_capture 获得隔离缓冲——无任何全局换装。
    """
    def __init__(self, real):
        self._real = real
        self._local = threading.local()

    def start_capture(self):
        self._local.buf = StringIO()

    def end_capture(self):
        buf = getattr(self._local, "buf", None)
        self._local.buf = None
        return buf.getvalue() if buf else ""

    # ---- print() 需要的最小文件接口 ----
    def write(self, s):
        buf = getattr(self._local, "buf", None)
        if buf is not None:
            return buf.write(s)
        return self._real.write(s)

    def flush(self):
        if getattr(self._local, "buf", None) is None:
            self._real.flush()

    def __getattr__(self, name):  # encoding/isatty/fileno 等透传
        return getattr(self._real, name)


if not isinstance(sys.stdout, _ThreadRoutedStdout):
    sys.stdout = _ThreadRoutedStdout(sys.stdout)


def _run_captured(fn, *args):
    """在工作线程内捕获该模块的 stdout 并整体转投 stderr。"""
    sys.stdout.start_capture()
    try:
        return fn(*args)
    finally:
        logs = sys.stdout.end_capture()
        if logs.strip():
            try:
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
            tag = getattr(fn, "__name__", "module")
            sys.stderr.write(f"\n----- [{tag}] module logs -----\n{logs}")
            if not logs.endswith("\n"):
                sys.stderr.write("\n")


def _import_quick_chanlun():
    """延迟导入 quick_chanlun（v5.4 A-4 后其 stdout 治理在 __main__ 内,
    库方式调用时由本脚本的 _run_captured 负责日志捕获）"""
    import quick_chanlun  # noqa: F401
    return quick_chanlun

def _import_hithink():
    import hithink_fundamental as mod
    return mod

def _import_news():
    import news_detail_report as mod
    return mod

def _import_negative():
    import check_negative_news as mod
    return mod

def _import_html():
    import quick_html as mod
    return mod


# ============================================================
# 各模块执行函数（每个函数在其线程中独立运行）
# ============================================================

def _run_chanlun(code: str) -> dict:
    """缠论分析"""
    mod = _import_quick_chanlun()
    return mod.analyze_stock(code)


def _run_fundamental(code: str) -> dict:
    """基本面数据（同花顺API，含Q1季报+4年趋势+扣非+修正评分）"""
    mod = _import_hithink()
    return mod.get_fundamentals(code)


def _run_news(code: str, name: str) -> dict:
    """消息面评分"""
    mod = _import_news()
    return mod.analyze_single_stock(code, name)


def _run_negative(code: str, name: str) -> dict:
    """负面信号检查（v5.3.4-C1: 统一入口，iwencai→多源→skip_needs_review 降级链）"""
    mod = _import_negative()
    result = mod.search_negative(code, name, hours=24)
    # 简化输出：统计级别分布，不再输出原始文章列表
    results = result.get("results", [])
    l3 = [r for r in results if r.get("level") == "L3"]
    l2 = [r for r in results if r.get("level") == "L2"]
    l1 = [r for r in results if r.get("level") == "L1"]
    return {
        "symbol": code,
        "name": name,
        "source": result.get("source", ""),
        "error": result.get("error"),
        "total_negative": len(results),
        "l3_count": len(l3),
        "l2_count": len(l2),
        "l1_count": len(l1),
        "l3_details": [{"title": r["title"], "neg_hits": r.get("neg_hits", [])}
                       for r in l3],
        "l2_details": [{"title": r["title"], "neg_hits": r.get("neg_hits", [])}
                       for r in l2[:5]],  # L2只保留前5条
    }


def _run_html(code: str, name: str) -> dict:
    """HTML可视化报告
    v5.4.1(AUD-A-05): 优先复用 chanlun 线程已完成的分析对象——避免同一股票
    并行重跑完整技术分析(双倍CPU)与 parquet 缓存并发写竞态"""
    from quick_chanlun import _LAST_ANALYSES
    mod = _import_html()
    return mod.generate_html(code, name, shared_recsys=_LAST_ANALYSES.get(code))


# ============================================================
# 主流程
# ============================================================

def analyze(code: str, name: str = None, timeout_per_module: int = 180) -> dict:
    """并行执行所有分析，返回合并结果
    
    Args:
        code: 股票代码（如 600872）
        name: 股票名称（可选，默认用 code 代替）
        timeout_per_module: v5.4(B-01)语义变更为【总预算秒数】——全部模块共享,
            超预算后再给 max(30, 预算/2) 宽限窗, 仍未完成的模块标记
            timeout_abandoned 并继续回收已完成模块（旧"每模块超时"从未生效）
    
    Returns:
        合并后的结果字典
    """
    if name is None:
        name = code

    # 定义全部任务（每个任务是 (函数, 参数...) 元组）
    tasks = {
        "chanlun": (_run_chanlun, code),
        "fundamental": (_run_fundamental, code),
        "news": (_run_news, code, name),
        "negative": (_run_negative, code, name),
        "html": (_run_html, code, name),
    }

    result = {
        "symbol": code,
        "name": name,
        "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "modules": {},
        "errors": [],
        "summary": {},
    }

    # ── 并行执行 ──
    # v5.4(B-01): 预算制超时——旧实现 as_completed()+future.result(timeout) 中
    # timeout 是死参数(as_completed 只在 future 完成后才 yield, 永远等不到
    # 超时), 任一模块挂死 = 整个编排永久阻塞。改为 wait(总预算)+宽限窗:
    # 到点未完成模块标记 timeout_abandoned, 不再阻塞其余模块结果回收;
    # shutdown(wait=False, cancel_futures=True) 防止退出被挂死线程拖住
    # (线程内网络调用自带超时, 最终会自行结束)。
    # v5.4.1(AUD-A-05): html 依赖 chanlun——等待其完成(异常则自行降级自跑)
    # 后渲染, 共享同一次技术分析结果。
    # v6.2(2026-08-29): Baostock 线程安全修复。data_manager.get_klines 已用
    # BS_SESSION_LOCK 包裹登录+抓取全程；本修复前 hithink_fundamental 的裸
    # Baostock 调用(Step3.5估值分位/Step5行业兜底)未持锁，与 DataManager 锁内
    # 调用并发导致 session 串包死锁(实测 13min+)。现已将 hithink 两段改为走
    # baostock_utils.BS_SESSION_LOCK，所有 Baostock 调用统一串行化，5线程并行
    # 框架可安全保留——news/negative 的 HTTP 请求自由并行，耗时恢复~90s。
    pool = ThreadPoolExecutor(max_workers=5)
    future_map = {}
    not_done = set()
    futures_by_name = {}

    def _run_with_deps(fn, deps, *args):
        for d in deps:
            fut = futures_by_name.get(d)
            if fut is not None:
                try:
                    fut.result()
                except Exception:
                    pass  # 依赖失败: 本任务仍尝试自跑(generate_html 会兜底重分析)
        # v5.4(B-15): 捕获仍由 _run_captured 负责(线程路由式 stdout 隔离)
        return _run_captured(fn, *args)

    try:
        tasks_submit_order = list(tasks.items())
        for module_name, (fn, *args) in tasks_submit_order:
            deps = ("chanlun",) if module_name == "html" else ()
            fut = pool.submit(_run_with_deps, fn, deps, *args)
            futures_by_name[module_name] = fut
            future_map[fut] = module_name

        done, not_done = wait(future_map.keys(), timeout=timeout_per_module)
        if not_done:
            # v5.4.1(AUD-A-10): 宽限窗限幅 [10,60]s——旧下限固定 30s 使小预算
            # 调用方即使任务已死也固定拖尾; 上限防预算很大时白等过久
            grace = max(10, min(60, timeout_per_module // 2))
            _done2, not_done = wait(not_done, timeout=grace)
            done |= _done2
        for fut in not_done:
            fut.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    for future, module_name in future_map.items():
        if future in not_done:
            result["errors"].append({
                "module": module_name,
                "error": f"timeout_abandoned: 超过预算{timeout_per_module}s(+宽限)仍未完成",
            })
            result["modules"][module_name] = {"error": "timeout_abandoned"}
            continue
        try:
            module_result = future.result()
            result["modules"][module_name] = module_result
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            result["errors"].append({
                "module": module_name,
                "error": str(e),
                "traceback": tb,
            })
            result["modules"][module_name] = {"error": str(e)}

    # ── 生成摘要 ──
    modules_status = {}
    for mod_name, mod_data in result["modules"].items():
        # 判断是否真有错误（error 字段为非空字符串 或 非 None）
        err_val = mod_data.get("error") if isinstance(mod_data, dict) else None
        has_real_error = isinstance(err_val, str) and len(err_val) > 0
        if has_real_error:
            modules_status[mod_name] = "error"
        elif mod_name == "negative":
            # v5.3.4(C3): source=skip / skip_needs_review 都视为 skip 状态，
            # 但报告层会区分措辞（skip_needs_review ≠ 无负面）
            _src = str(mod_data.get("source") or "")
            modules_status[mod_name] = "skip" if _src.startswith("skip") else "ok"
        else:
            modules_status[mod_name] = "ok"

    result["summary"] = {
        "modules_count": len(tasks),
        "success_count": sum(1 for s in modules_status.values() if s == "ok"),
        "error_count": len(result["errors"]),
        "modules_status": modules_status,
    }

    return result


def main():
    parser = argparse.ArgumentParser(
        description="A股统一三维分析（合并5个子进程为1次执行）"
    )
    parser.add_argument("--code", required=True, help="股票代码（如 600872）")
    parser.add_argument("--name", help="股票名称（如 中炬高新）")
    parser.add_argument("--output", help="保存结果到文件路径（默认输出到stdout）")
    parser.add_argument("--report", action="store_true",
                        help="生成Markdown分析报告（调用 generate_report.py）")

    args = parser.parse_args()

    # 执行分析
    result = analyze(args.code, args.name)

    # 如果有 --report，调用 generate_report.py 生成 md 报告
    # v5.3.4(审计P0-1)修复：先写盘再传参。旧逻辑把 /dev/stdin（Windows 必炸）
    # 或尚未写盘的 --output 路径传给子进程 → 报告必然失败，且失败仅打 stderr、
    # 不进 errors[]、整体仍 exit 0（"误导性成功"）。
    if args.report:
        import subprocess as _sp
        gr_path = os.path.join(_SCRIPT_DIR, "generate_report.py")
        json_for_report = os.path.abspath(args.output) if args.output else os.path.join(
            tempfile.gettempdir(), f"_ssa_temp_{args.code}.json")
        os.makedirs(os.path.dirname(json_for_report) or ".", exist_ok=True)
        with open(json_for_report, "w", encoding="utf-8") as _jf:
            _jf.write(json.dumps(result, ensure_ascii=False))
        try:
            # 终审A2(2026-08-23): 显式 utf-8 + errors=replace + PYTHONIOENCODING。
            # Windows GBK 环境下子进程输出含 ⚠️/emoji 时 print 会 UnicodeEncodeError
            # → returncode≠0 → 报告被误判失败（与 run_full_4d_pipeline P1-5 同构坑）。
            _env = dict(os.environ)
            _env["PYTHONIOENCODING"] = "utf-8"
            _r = _sp.run(
                ["python", gr_path, "--input", json_for_report,
                 "--code", args.code, "--name", args.name or args.code],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180, env=_env
            )
        except _sp.TimeoutExpired:
            _r = None
        if _r is not None and _r.returncode == 0:
            # v5.4.1(AUD-A-04): 报告子进程的成功摘要只投 stderr——旧实现 print
            # 到 stdout, "--report 且无 --output"时与下方主 JSON 拼接成两段文本,
            # 消费方 json.loads(stdout) 必失败(JSON 契约污染)
            sys.stderr.write(_r.stdout or "")
        else:
            _tail = (_r.stderr[-800:] if _r is not None else "子进程超时(180s)")
            print(f"⚠️ 报告生成失败: {_tail}", file=sys.stderr)
            result["errors"].append("report_generation_failed")

    # 输出（v5.3.4: 若报告失败已在上方计入 errors，这里同步 error_count 保持一致）
    result["summary"]["error_count"] = len(result["errors"])
    output_json = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        # stdout 只输出简短的确认信息
        print(json.dumps({
            "status": "ok",
            "output": os.path.abspath(args.output),
            "symbol": args.code,
            "errors": len(result["errors"]),
            "modules": list(result["modules"].keys()),
        }, ensure_ascii=False))
    else:
        # stdout 输出完整 JSON（供 Agent 消费）
        print(output_json)

    # 如果有错误，非零退出
    if result["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    # v5.4(A-4): 入口编码守卫——GBK 控制台下报告文本/错误信息含 emoji 或
    # 生僻符号时不再 UnicodeEncodeError 崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
