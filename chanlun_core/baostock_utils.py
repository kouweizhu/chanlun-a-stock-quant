"""baostock_utils.py — Baostock 共享工具模块

集中管理所有依赖 Baostock 的模块共同需要的功能：
1. print 重定向到 stderr（避免污染 stdout JSON 输出）
2. 股票代码转换（6位 → 9位）
3. 登录/登出 session 管理（幂等，防重复登录）

使用方式：
    import baostock_utils
    bs_code = baostock_utils.to_bs_code("000001")
    bs = baostock_utils.login()
    ...
    baostock_utils.logout()

注意事项：
- 模块加载时自动安装 print 重定向（幂等）
- login() 可重复调用，内部防重入
- logout() 直接调用 bs.logout()
"""

import sys
from date_utils import date_to_str, parse_date_to_datetime
import builtins

# ============================================================
# 1. Print 重定向 — 幂等安装
# ============================================================

_print_redirected = False

def _setup_print_redirect():
    """将 Baostock 的 print 输出重定向到 stderr，一次进程生命周期只安装一次"""
    global _print_redirected
    if _print_redirected:
        return
    _print_redirected = True

    _orig_print = builtins.print

    def _redirected_print(*a, **kw):
        # 判断是否为 Baostock 日志消息（以 '[' 开头或包含 login/logout）
        if 'file' not in kw and a:
            msg = str(a[0])
            if (msg.startswith('[') or
                msg == "you don't login." or
                'login' in msg.lower() or
                'logout' in msg.lower()):
                kw = {**kw, 'file': sys.stderr}
        _orig_print(*a, **kw)

    builtins.print = _redirected_print

# 模块加载时自动安装
_setup_print_redirect()

# ============================================================
# 2. 股票代码转换
# ============================================================

def to_bs_code(code: str) -> str:
    """6位股票代码 → 9位 Baostock 代码

    Args:
        code: 6-digit stock code, e.g. "000001", "600519"

    Returns:
        Baostock code, e.g. "sz.000001", "sh.600519"
    """
    code = str(code).strip()
    # v5.4(B-14): 补沪市基金/ETF('5') 分支，与 data_manager 的前缀映射同口径
    # ——旧实现 51xxxx/58xxxx 落到 sz.* 在本路径必然查询失败
    if code.startswith(('6', '9', '5')):
        return f'sh.{code}'
    return f'sz.{code}'

# ============================================================
# 3. Baostock Session 管理
# ============================================================

import threading

# v5.3.3(F-2): Baostock 连接级会话锁——全局唯一, 所有 Baostock query 必须
# 持锁执行。历史事故: pool_screener 自己的 fallback 加了 _BS_LOCK, 但
# akshare_fundamental._baostock_basic_and_valuation 内部调用未加锁,
# 4线程并发下 session 串包 → PE/PB/行业静默失败(川投能源 PE=0 事故)。
# 锁放底层模块避免 pool_screener→akshare_fundamental 循环依赖。
# v5.4.1(AUD主审M-1): 升级为 RLock 并在本模块四函数内部真正持锁——
# v5.4 只下沉了锁对象、执行纪律仍靠调用方自觉("半成品"), grid_search/
# quick_fundamental 曾存在裸调 ensure_login 的并发暴露面。RLock 允许
# 同线程重入: 外部 `with BS_SESSION_LOCK:` 块(data_manager/pool_screener/
# akshare_fundamental 三处既有持锁点)内部再进本模块函数不会自锁。
BS_SESSION_LOCK = threading.RLock()

_logged_in = False
_login_result = None

def login():
    """登录 Baostock（幂等，已登录则返回上次结果）

    Returns:
        (bs_module, login_result) — bs 模块和 bs.login() 的返回值
    """
    global _logged_in, _login_result
    import baostock as bs
    with BS_SESSION_LOCK:
        if not _logged_in:
            _login_result = bs.login()
            _logged_in = True
        return bs, _login_result

def logout():
    """登出 Baostock"""
    global _logged_in, _login_result
    import baostock as bs
    with BS_SESSION_LOCK:
        try:
            bs.logout()
        except Exception:
            pass
        _logged_in = False
        _login_result = None

def ensure_login():
    """确保已登录（自动检测 session 有效性）

    用于无法确定当前 session 状态的多模块场景。
    先尝试 query_stock_basic 验证，失败则重新登录。
    v5.4.1(M-1): 全程持 BS_SESSION_LOCK(RLock)——session 探活与重登
    必须原子, 否则两线程同时探活失败会交叉 logout/login。
    """
    global _logged_in, _login_result
    import baostock as bs
    with BS_SESSION_LOCK:
        try:
            # 用一个简单查询验证 session
            rs = bs.query_stock_basic(code="sh.600000")
            if rs.error_code == '0':
                _logged_in = True
                return bs, _login_result
        except Exception:
            pass

        # Session 无效，重新登录
        try:
            bs.logout()
        except Exception:
            pass
        _login_result = bs.login()
        _logged_in = True
        return bs, _login_result


def query_with_retry(bs, query_fn, max_retries=2):
    """执行 Baostock 查询，失败时自动重连并重试

    Args:
        bs: baostock 模块
        query_fn: 无参函数，返回 (error_code, error_msg) 或 ResultData
        max_retries: 最大重试次数

    Returns:
        query_fn 的返回值，或最后一次失败的结果

    v5.4.1(M-1): 全程持 BS_SESSION_LOCK(RLock)——查询与失败重登是同一
    会话上的复合操作, 由本模块保证原子性而非依赖调用方自觉。
    """
    global _logged_in, _login_result
    import baostock as bs_module

    with BS_SESSION_LOCK:
        for attempt in range(max_retries + 1):
            try:
                result = query_fn()
                # 检查是否是 Baostock ResultData 对象
                if hasattr(result, 'error_code'):
                    if result.error_code == '0':
                        return result
                    if attempt < max_retries:
                        # 重新登录后重试
                        try:
                            bs_module.logout()
                        except Exception:
                            pass
                        _login_result = bs_module.login()
                        _logged_in = True
                else:
                    return result
            except Exception as e:
                if attempt < max_retries:
                    try:
                        bs_module.logout()
                    except Exception:
                        pass
                    _login_result = bs_module.login()
                    _logged_in = True
                else:
                    raise

        return result
