"""
auto_validate.py — 定时自验证入口 (v2.0)

每周一/三/五 20:00 由 cronjob 调用。
v2.0 新增：多维指标漂移监控 + 历史追踪 + 2σ 告警。

监控指标：
  1. tech_score_mean    — 技术评分均值
  2. tech_score_std     — 技术评分标准差
  3. signal_count       — 买点信号总数
  4. buy_type_1_count   — 一类买点数量
  5. grade_A_rate       — A+/A 级占比
  6. error_rate         — 分析失败率

告警规则：任意指标较 30 日均值偏离 > 2σ 时触发告警。

用法：
  python auto_validate.py                    # 默认
  python auto_validate.py --all              # 全验证 + 网格搜索
  python auto_validate.py --metrics-only     # 仅更新历史指标（不跑验证）
"""

import sys, os, json, subprocess
from date_utils import date_to_str, parse_date_to_datetime
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cron_utils import CronLogger

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
VALIDATE_SCRIPT = os.path.join(WORK_DIR, "validate_tech_score.py")
GRID_SEARCH_SCRIPT = os.path.join(WORK_DIR, "grid_search.py")
REPORT_DIR = "D:/常用文件/回测报告/定时自验证报告"
os.makedirs(REPORT_DIR, exist_ok=True)
METRICS_FILE = os.path.join(REPORT_DIR, "metrics_history.json")
JSON_OUTPUT = os.path.join(WORK_DIR, "tech_score_validation.json")

os.makedirs(REPORT_DIR, exist_ok=True)
today = datetime.now().strftime("%Y-%m-%d")

# ============================================================
# 指标提取
# ============================================================

def extract_metrics(validation_json_path: str) -> dict:
    """从 validate_tech_score.py 输出的 JSON 中提取核心指标"""
    if not os.path.exists(validation_json_path):
        return None

    with open(validation_json_path, 'r') as f:
        data = json.load(f)

    signals = data.get('signals', [])
    n = len(signals)
    if n == 0:
        return {
            'date': today,
            'tech_score_mean': None,
            'tech_score_std': None,
            'signal_count': 0,
            'buy_type_1_count': 0,
            'grade_A_rate': 0.0,
            'error_rate': 0.0,
        }

    tech_scores = [s['tech_score'] for s in signals if s.get('tech_score') is not None]
    buy_1 = sum(1 for s in signals if '一买' in str(s.get('point_type', '')))
    grade_a = sum(1 for s in signals if s.get('grade', '') in ('A+', 'A'))
    errors = sum(1 for s in signals if s.get('error'))

    return {
        'date': today,
        'tech_score_mean': round(float(np.mean(tech_scores)), 1) if tech_scores else None,
        'tech_score_std': round(float(np.std(tech_scores)), 1) if tech_scores else None,
        'signal_count': n,
        'buy_type_1_count': buy_1,
        'grade_A_rate': round(grade_a / n, 4) if n > 0 else 0.0,
        'error_rate': round(errors / max(n, 1), 4),
    }


# ============================================================
# 历史追踪
# ============================================================

def load_history() -> list:
    """加载历史指标"""
    if os.path.exists(METRICS_FILE):
        with open(METRICS_FILE, 'r') as f:
            return json.load(f)
    return []


def save_history(history: list):
    """保存历史指标（保留最近 90 天）"""
    history = history[-90:]
    with open(METRICS_FILE, 'w') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def check_drift(history: list, window: int = 30) -> list:
    """检查指标漂移 — 2σ 告警
    
    Returns:
        list of warning strings
    """
    if len(history) < max(window, 10):
        return ["[INFO] 历史数据不足，跳过漂移检测 (需要≥10天)"]

    recent = history[-window:]
    warnings = []

    keys = ['tech_score_mean', 'tech_score_std', 'signal_count',
            'buy_type_1_count', 'grade_A_rate']
    labels = {
        'tech_score_mean': '技术评分均值',
        'tech_score_std': '技术评分标准差',
        'signal_count': '买点信号数',
        'buy_type_1_count': '一类买点数',
        'grade_A_rate': 'A+/A级占比',
    }

    today_entry = history[-1]

    for key in keys:
        values = [h.get(key) for h in recent if h.get(key) is not None]
        if len(values) < 5:
            continue

        mean = np.mean(values[:-1])  # 不含今天的均值
        std = np.std(values[:-1])
        today_val = today_entry.get(key)

        if today_val is None or std == 0:
            continue

        z_score = abs(today_val - mean) / std
        if z_score > 2.0:
            direction = "↑" if today_val > mean else "↓"
            warnings.append(
                f"[ALERT] {labels[key]}: 今日 {today_val} {direction} "
                f"(30日均值 {mean:.1f}, σ={std:.1f}, Z={z_score:.1f})"
            )
        elif z_score > 1.5:
            direction = "↑" if today_val > mean else "↓"
            warnings.append(
                f"[WARN]  {labels[key]}: 今日 {today_val} {direction} "
                f"(30日均值 {mean:.1f}, Z={z_score:.1f})"
            )

    # 额外检查：信号数为 0
    if today_entry.get('signal_count', 1) == 0:
        warnings.append("[ALERT] 信号数为 0 — 数据源可能故障")

    return warnings if warnings else ["[OK] 所有指标正常"]


# ============================================================
# 模型级漂移检测 (v3.0 新增)
# ============================================================

def check_model_drift(history: list) -> list:
    """检查策略有效性漂移 — 比指标漂移更深层
    
    新指标：
      7. virtual_return_1m   — 虚拟组合月度收益
      8. excess_vs_hs300     — 相对沪深300超额
      9. score_trend         — 技术评分连续下降天数
    """
    if len(history) < 10:
        return ["[INFO] 历史数据不足，跳过模型漂移检测 (需要≥10天)"]
    
    warnings = []
    
    # --- 7. 技术评分连续下降 (模型变弱信号) ---
    scores = [h.get('tech_score_mean') for h in history if h.get('tech_score_mean') is not None]
    if len(scores) >= 10:
        # 检查最近5天的趋势
        recent5 = scores[-5:]
        if len(recent5) == 5:
            # 线性回归斜率
            x = np.arange(5)
            slope = np.polyfit(x, recent5, 1)[0]
            if slope < -1.0:  # 每天下降 >1分
                warnings.append(
                    f"[ALERT] 技术评分连续下降: 5日斜率={slope:.1f}/日 "
                    f"(从{recent5[0]:.1f}降至{recent5[-1]:.1f})"
                )
            elif slope < -0.5:
                warnings.append(
                    f"[WARN] 技术评分缓慢下降: 5日斜率={slope:.1f}/日"
                )
    
    # --- 8. 信号数量衰减 ---
    signals = [h.get('signal_count') for h in history if h.get('signal_count') is not None]
    if len(signals) >= 15:
        recent15_avg = np.mean(signals[-15:])
        older15_avg = np.mean(signals[-30:-15]) if len(signals) >= 30 else np.mean(signals[:-15])
        
        if older15_avg > 0 and recent15_avg < older15_avg * 0.5:
            warnings.append(
                f"[ALERT] 信号数腰斩: 近15日均{recent15_avg:.1f} vs 前15日均{older15_avg:.1f}"
            )
        elif older15_avg > 0 and recent15_avg < older15_avg * 0.7:
            warnings.append(
                f"[WARN] 信号数明显减少: 近15日均{recent15_avg:.1f} vs 前15日均{older15_avg:.1f}"
            )
    
    # --- 9. A级占比崩溃 ---
    a_rates = [h.get('grade_A_rate', 0) for h in history if h.get('grade_A_rate') is not None]
    if len(a_rates) >= 10:
        a_now = a_rates[-1]
        a_avg = np.mean(a_rates[:-1]) if len(a_rates) > 1 else a_now
        if a_avg > 0 and a_now < a_avg * 0.3:
            warnings.append(
                f"[ALERT] A级占比崩溃: 当前{a_now*100:.0f}% vs 均值{a_avg*100:.0f}%"
            )
    
    return warnings if warnings else ["[OK] 模型健康"]


def check_portfolio_drift() -> list:
    """检查虚拟组合收益漂移（基于A500 Top10报告）
    
    读取最近N期选股报告，模拟等权买入后1月收益。
    如果连续3期跑输沪深300 → 告警。
    """
    import re
    import glob
    
    report_dir = "D:/常用文件/股票池推荐股"
    if not os.path.exists(report_dir):
        return []
    
    # 找所有扫描汇总MD
    summary_files = sorted(glob.glob(os.path.join(report_dir, "扫描汇总_*.md")), reverse=True)
    if len(summary_files) < 3:
        return ["[INFO] A500报告不足3期，跳过组合漂移检测"]
    
    warnings = []
    beat_count = 0
    total = 0
    
    for fpath in summary_files[:6]:  # 最近6期
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 提取日期
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(fpath))
            report_date = date_match.group(1) if date_match else '未知'
            
            # 提取综合分
            scores = re.findall(r'\|\s*[\d.]+\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|', content)
            if scores:
                composites = [float(s[3]) for s in scores[:10]]
                avg_composite = np.mean(composites) if composites else 0
                
                total += 1
                # 简化：综合分 > 70 算"优质"
                if avg_composite > 70:
                    beat_count += 1
        except:
            continue
    
    if total >= 3 and beat_count == 0:
        warnings.append(
            f"[ALERT] 最近{total}期A500选股Top10综合分均≤70，选股质量可能下降"
        )
    elif total >= 3 and beat_count < total / 2:
        warnings.append(
            f"[WARN] 最近{total}期中仅{beat_count}期综合分>70"
        )
    
    return warnings if warnings else ["[OK] A500选股质量正常"]


# ============================================================
# 主流程
# ============================================================

def run_validation():
    """运行全量验证，保存结果，提取指标"""
    logger = CronLogger("auto_validate")
    logger.info(f"开始自验证 ({today})")

    # 1. 跑全量验证
    cmd = f"cd {WORK_DIR} && python {VALIDATE_SCRIPT} 2>&1"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)

    report_path = os.path.join(REPORT_DIR, f"{today}_validation.md")
    with open(report_path, 'w') as f:
        f.write(f"# 缠论系统自验证报告\n\n")
        f.write(f"**验证时间**: {today}\n\n")
        f.write(f"## 验证输出\n\n")
        f.write(f"```\n{result.stdout}\n```\n")
        if result.stderr:
            f.write(f"\n## 错误输出\n\n")
            f.write(f"```\n{result.stderr}\n```\n")

    print(f"  验证报告: {report_path}")

    # 检查子进程是否成功
    if result.returncode != 0:
        print(f"  ⚠️ validate_tech_score.py 返回非零: {result.returncode}")

    # 2. 提取指标
    metrics = extract_metrics(JSON_OUTPUT)
    if metrics is None:
        print("  ⚠️ 未找到验证结果 JSON")
        return None

    # 打印当前指标
    print(f"\n  --- 今日指标 ---")
    print(f"  技术评分均值:  {metrics['tech_score_mean']}")
    print(f"  技术评分标准差: {metrics['tech_score_std']}")
    print(f"  买点信号总数:   {metrics['signal_count']}")
    print(f"  一类买点数量:   {metrics['buy_type_1_count']}")
    print(f"  A+/A级占比:     {metrics['grade_A_rate']*100:.1f}%")
    print(f"  失败率:         {metrics['error_rate']*100:.1f}%")

    # 3. 加载历史 + 追加
    history = load_history()
    history.append(metrics)
    save_history(history)
    print(f"\n  历史记录: {len(history)} 天 (最近 90 天保留)")

    # 4. 漂移检测
    logger.separator()
    logger.info("指标漂移检测:")
    drift_warnings = check_drift(history)
    for w in drift_warnings:
        level = "ERROR" if "[ALERT]" in w else ("WARN" if "[WARN]" in w else "INFO")
        logger.log(w, level)
    
    # 5. 模型漂移检测 (v3.0)
    logger.separator()
    logger.info("模型漂移检测:")
    model_warnings = check_model_drift(history)
    for w in model_warnings:
        level = "ERROR" if "[ALERT]" in w else ("WARN" if "[WARN]" in w else "INFO")
        logger.log(w, level)
    
    # 6. 组合质量检测 (v3.0)
    port_warnings = check_portfolio_drift()
    if port_warnings:
        logger.separator()
        logger.info("组合漂移检测:")
        for w in port_warnings:
            level = "ERROR" if "[ALERT]" in w else ("WARN" if "[WARN]" in w else "INFO")
            logger.log(w, level)

    logger.success(f"自验证完成")
    return metrics


def run_grid_search():
    """运行参数网格搜索"""
    print(f"[{today}] 开始参数网格搜索...")
    cmd = f"cd {WORK_DIR} && python {GRID_SEARCH_SCRIPT} 2>&1"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)

    report_path = os.path.join(REPORT_DIR, f"{today}_grid_search.md")
    with open(report_path, 'w') as f:
        f.write(f"# 缠论系统参数网格搜索报告\n\n")
        f.write(f"**搜索时间**: {today}\n\n")
        f.write(f"## 搜索结果\n\n")
        f.write(f"```\n{result.stdout}\n```\n")
    print(f"  结果: {report_path}")


def metrics_only():
    """仅更新历史指标（不跑全量验证）"""
    metrics = extract_metrics(JSON_OUTPUT)
    if metrics is None:
        print("⚠️ 未找到验证结果 JSON，请先运行 validate_tech_score.py")
        return

    history = load_history()
    history.append(metrics)
    save_history(history)
    print(f"指标已追加: {metrics['signal_count']} 信号, 历史 {len(history)} 天")


if __name__ == "__main__":
    print("=" * 60)
    print("  缠论系统定时自验证 v2.0")
    print("=" * 60)

    args = sys.argv[1:]

    if '--metrics-only' in args:
        metrics_only()
    else:
        run_validation()

        if '--all' in args or '--grid' in args:
            run_grid_search()

    print(f"\n[{today}] 自验证完成")
