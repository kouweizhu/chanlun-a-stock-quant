"""
data_health_monitor.py — 数据源心跳检测
用法：python data_health_monitor.py [--push]

每日开盘前 ping 四大数据源：
  - Baostock (登录+简单查询)
  - AKShare (stock_info_a_code_name, 带 signal 超时保护)
  - Tavily (API key + test search)
  - Metaso (API key + test search)

输出：YYY-MM-DD_数据源健康报告.md
"""
import sys
import os
import time
import json
import signal
from datetime import datetime
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPORT_DIR = "D:/常用文件/Hermes系统运行状态/数据源健康"
os.makedirs(REPORT_DIR, exist_ok=True)


# ============================================================
# Signal 超时辅助（可靠中断阻塞的 C 扩展调用）
# ============================================================

class TimeoutError(Exception):
    pass


def timeout_handler(signum, frame):
    raise TimeoutError("操作超时")


def with_timeout(func, timeout_sec, *args, **kwargs):
    """使用 SIGALRM 实现超时，只适用于主线程"""
    old = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout_sec)
    try:
        result = func(*args, **kwargs)
        return result
    except TimeoutError:
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


# ============================================================
# 检测函数
# ============================================================

def check_baostock() -> Dict:
    """检测 Baostock 连接"""
    import baostock as bs
    start = time.time()
    try:
        result = bs.login()
        if result.error_code == '0':
            # 验证：简单查询
            rs = bs.query_stock_basic(code="sh.600519")
            if rs.error_code == '0' and rs.next():
                bs.logout()
                return {
                    'status': 'OK',
                    'latency_ms': round((time.time() - start) * 1000),
                    'detail': '登录+查询成功',
                }
        bs.logout()
        return {'status': 'DOWN', 'latency_ms': 0, 'detail': f'登录失败: {result.error_msg}'}
    except Exception as e:
        try:
            bs.logout()
        except:
            pass
        return {'status': 'DOWN', 'latency_ms': 0, 'detail': str(e)[:80]}


def check_akshare() -> Dict:
    """检测 AKShare 可用性（多接口降级检测，带 signal 超时保护）"""
    import akshare as ak

    # 主接口：stock_info_a_code_name（东方财富源，偶尔不稳定/超时）
    start = time.time()
    try:
        df = with_timeout(ak.stock_info_a_code_name, 30)
        if df is not None and len(df) > 1000:
            return {
                'status': 'OK',
                'latency_ms': round((time.time() - start) * 1000),
                'detail': f'stock_info_a_code_name 返回 {len(df)} 只股票',
            }
        return {'status': 'DEGRADED', 'latency_ms': 0, 'detail': f'数据异常: {len(df) if df is not None else 0} 行'}
    except Exception as e:
        pass  # 主接口失败（含超时），降级到备用接口

    # 备用接口1：上证指数日线（新浪源）
    try:
        start = time.time()
        df = with_timeout(lambda: ak.stock_zh_index_daily(symbol='sh000001'), 15)
        if df is not None and len(df) > 100:
            return {
                'status': 'DEGRADED',
                'latency_ms': round((time.time() - start) * 1000),
                'detail': f'主接口异常，备用接口正常（{len(df)} 行）',
            }
    except Exception:
        pass

    # 备用接口2：北向资金（新浪源）
    try:
        start = time.time()
        df = with_timeout(lambda: ak.stock_hsgt_north_net_flow_in_em(symbol='北上'), 15)
        if df is not None and len(df) > 0:
            return {
                'status': 'DEGRADED',
                'latency_ms': round((time.time() - start) * 1000),
                'detail': f'主接口异常，备用接口2正常（{len(df)} 行）',
            }
    except Exception:
        pass

    return {'status': 'DOWN', 'latency_ms': 0, 'detail': '所有接口均失败（含超时保护）'}


def check_tavily() -> Dict:
    """检测 Tavily API"""
    api_key = os.environ.get('TAVILY_API_KEY', '')
    if not api_key:
        # 尝试从 .env 加载
        try:
            from dotenv import load_dotenv
            hermes_home = os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes'))
            env_path = os.path.join(os.path.dirname(hermes_home), '.env') if 'profiles' in hermes_home else os.path.join(hermes_home, '.env')
            load_dotenv(env_path)
            api_key = os.environ.get('TAVILY_API_KEY', '')
        except:
            pass

    if not api_key:
        return {'status': 'NO_KEY', 'latency_ms': 0, 'detail': 'TAVILY_API_KEY 未配置'}

    start = time.time()
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            'https://api.tavily.com/search',
            data=json.dumps({'query': 'test', 'max_results': 1, 'search_depth': 'basic'}).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if 'results' in data:
                return {
                    'status': 'OK',
                    'latency_ms': round((time.time() - start) * 1000),
                    'detail': f'返回 {len(data["results"])} 条结果',
                }
        return {'status': 'DOWN', 'latency_ms': 0, 'detail': '响应格式异常'}
    except urllib.error.HTTPError as e:
        return {'status': 'DOWN', 'latency_ms': 0, 'detail': f'HTTP {e.code}'}
    except Exception as e:
        return {'status': 'DOWN', 'latency_ms': 0, 'detail': str(e)[:80]}


def check_metaso() -> Dict:
    """检测 Metaso API"""
    api_key = os.environ.get('METASO_API_KEY', '')
    if not api_key:
        try:
            from dotenv import load_dotenv
            hermes_home = os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes'))
            env_path = os.path.join(os.path.dirname(hermes_home), '.env') if 'profiles' in hermes_home else os.path.join(hermes_home, '.env')
            load_dotenv(env_path)
            api_key = os.environ.get('METASO_API_KEY', '')
        except:
            pass

    if not api_key:
        return {'status': 'NO_KEY', 'latency_ms': 0, 'detail': 'METASO_API_KEY 未配置'}

    start = time.time()
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            'https://metaso.cn/api/v1/search',
            data=json.dumps({'q': '测试', 'scope': 'webpage', 'size': 1}).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if 'webpages' in data or 'data' in data:
                return {
                    'status': 'OK',
                    'latency_ms': round((time.time() - start) * 1000),
                    'detail': '搜索成功',
                }
        return {'status': 'DOWN', 'latency_ms': 0, 'detail': '响应格式异常'}
    except urllib.error.HTTPError as e:
        return {'status': 'DOWN', 'latency_ms': 0, 'detail': f'HTTP {e.code}'}
    except Exception as e:
        return {'status': 'DOWN', 'latency_ms': 0, 'detail': str(e)[:80]}


# ============================================================
# 报告生成
# ============================================================

def run_all_checks() -> Dict[str, Dict]:
    """依次检测所有数据源"""
    checks = {}

    print("检测中...")
    for name, fn in [
        ('Baostock', check_baostock),
        ('AKShare', check_akshare),
        ('Tavily', check_tavily),
        ('Metaso', check_metaso),
    ]:
        print(f"  {name}...", end=' ', flush=True)
        result = fn()
        icon = {'OK': '✓', 'DEGRADED': '⚠', 'DOWN': '✗', 'NO_KEY': '○'}.get(result['status'], '?')
        print(f"{icon} {result['status']} ({result.get('latency_ms', 0)}ms)")
        checks[name] = result

    return checks


def generate_report(checks: Dict) -> str:
    """生成健康报告"""
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    today_file = datetime.now().strftime('%Y-%m-%d')

    ok_count = sum(1 for c in checks.values() if c['status'] == 'OK')
    down_count = sum(1 for c in checks.values() if c['status'] == 'DOWN')
    total = len(checks)

    lines = []
    lines.append(f"# 数据源健康报告 — {today_file}")
    lines.append("")
    lines.append(f"**检测时间**: {today}")
    lines.append(f"**整体状态**: {ok_count}/{total} 正常" + (" ✅" if down_count == 0 else f" ⚠️ {down_count} 个故障"))
    lines.append("")

    # 汇总表
    lines.append("| 数据源 | 状态 | 延迟 | 详情 |")
    lines.append("|--------|:----:|:----:|------|")

    for name, c in checks.items():
        icon = {'OK': '🟢', 'DEGRADED': '🟡', 'DOWN': '🔴', 'NO_KEY': '⚪'}.get(c['status'], '❓')
        latency = f"{c['latency_ms']}ms" if c['latency_ms'] > 0 else '-'
        lines.append(f"| {name} | {icon} {c['status']} | {latency} | {c['detail']} |")

    lines.append("")

    # 故障详情
    failures = [n for n, c in checks.items() if c['status'] in ('DOWN', 'DEGRADED')]
    if failures:
        lines.append("## 故障告警")
        lines.append("")
        for name in failures:
            c = checks[name]
            lines.append(f"- 🔴 **{name}**: {c['detail']}")
        lines.append("")

        # 影响分析
        lines.append("## 影响分析")
        lines.append("")
        if 'Baostock' in failures:
            lines.append("- **K线数据获取将失败** → 选股/回测全部不可用 → 依赖 investoday MCP 兜底")
        if 'AKShare' in failures:
            lines.append("- **基本面数据将降级** → pool_screener 自动切换到 Baostock fallback")
        if 'Tavily' in failures:
            lines.append("- **消息面评分将降级** → 自动切换到 Metaso")
        if 'Metaso' in failures:
            lines.append("- **消息面第二备源失效** → Tavily 失败时无法兜底")
        if 'Tavily' in failures and 'Metaso' in failures:
            lines.append("- ⚠️ **消息面评分完全不可用** → 所有 news_score = 50")
        lines.append("")

    lines.append("---")
    lines.append("*自动生成 | Hermes 数据源监控*")

    return '\n'.join(lines)


# ============================================================
# 主入口
# ============================================================
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='数据源健康检测')
    parser.add_argument('--push', action='store_true', help='推送告警')
    parser.add_argument('--output', type=str, help='输出路径（默认 数据源健康/日期_数据源健康报告.md）')
    args = parser.parse_args()

    print("=" * 50)
    print("  数据源心跳检测")
    print("=" * 50)
    print()

    # 检测
    checks = run_all_checks()

    # 生成报告
    report = generate_report(checks)

    output_path = args.output or os.path.join(
        REPORT_DIR, f"{datetime.now().strftime('%Y-%m-%d')}_数据源健康报告.md"
    )
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n[报告] {output_path}")

    # 简洁汇总
    ok = sum(1 for c in checks.values() if c['status'] == 'OK')
    down = sum(1 for c in checks.values() if c['status'] == 'DOWN')

    if down > 0:
        failures = [n for n, c in checks.items() if c['status'] == 'DOWN']
        print(f"\n⚠️  {down}/{len(checks)} 故障: {', '.join(failures)}")
    else:
        print(f"\n✅ 全部 {ok}/{len(checks)} 正常")

    print("\n完毕。")