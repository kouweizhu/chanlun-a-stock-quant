"""
cron_utils.py — 定时任务依赖管理与日志工具

Flag 文件信号系统：
  - 上游任务成功后写入 .flag 标记文件
  - 下游任务执行前检查标记存在性
  - 标记文件带日期，支持月度回测等周期性依赖

统一日志：
  - 所有 cron 脚本输出到 logs/YYYY-MM-DD/<script_name>.log
  - 自动创建目录，追加写入

用法:
    from cron_utils import FlagSignals, CronLogger

    # 上游：成功后写标记
    FlagSignals.write("a500_scan_done", date_str="2026-04")

    # 下游：检查标记
    if FlagSignals.check("a500_scan_done", date_str="2026-04"):
        run_backtest()
    else:
        print("上游未完成，跳过")

    # 日志
    logger = CronLogger("pool_screener")
    logger.log("开始扫描...")
"""

import os
from date_utils import date_to_str, parse_date_to_datetime
import sys
from datetime import datetime


SIGNALS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals")
LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


class FlagSignals:
    """轻量级 flag 文件信号系统"""

    @staticmethod
    def _ensure_dir():
        os.makedirs(SIGNALS_DIR, exist_ok=True)

    @staticmethod
    def write(task_name: str, date_str: str = None, extra: dict = None) -> str:
        """写入成功标记

        Args:
            task_name: 任务名，如 'a500_scan_done'
            date_str: 日期字符串，如 '2026-04-30' 或 '2026-04'
            extra: 额外元数据（写入 JSON）

        Returns:
            flag 文件路径
        """
        FlagSignals._ensure_dir()
        suffix = f"_{date_str}" if date_str else ""
        path = os.path.join(SIGNALS_DIR, f"{task_name}{suffix}.flag")

        import json
        content = {
            "task": task_name,
            "date": date_str,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if extra:
            content.update(extra)

        with open(path, 'w') as f:
            json.dump(content, f, ensure_ascii=False)

        return path

    @staticmethod
    def check(task_name: str, date_str: str = None) -> bool:
        """检查标记是否存在"""
        suffix = f"_{date_str}" if date_str else ""
        path = os.path.join(SIGNALS_DIR, f"{task_name}{suffix}.flag")
        return os.path.exists(path)

    @staticmethod
    def read(task_name: str, date_str: str = None) -> dict:
        """读取标记内容"""
        suffix = f"_{date_str}" if date_str else ""
        path = os.path.join(SIGNALS_DIR, f"{task_name}{suffix}.flag")
        if not os.path.exists(path):
            return {}
        import json
        with open(path, 'r') as f:
            return json.load(f)

    @staticmethod
    def get_latest(task_name: str, pattern: str = None) -> str:
        """获取最新的匹配标记日期"""
        FlagSignals._ensure_dir()
        import glob
        pattern = os.path.join(SIGNALS_DIR, f"{task_name}*.flag")
        files = sorted(glob.glob(pattern), reverse=True)
        return files[0] if files else None

    @staticmethod
    def list_all() -> list:
        """列出所有标记文件"""
        FlagSignals._ensure_dir()
        import glob
        files = glob.glob(os.path.join(SIGNALS_DIR, "*.flag"))
        result = []
        for f in sorted(files):
            flag = FlagSignals.read(os.path.basename(f).replace('.flag', ''))
            result.append({"path": f, "name": os.path.basename(f), "content": flag})
        return result


class CronLogger:
    """cron 任务统一日志器
    
    输出到 logs/YYYY-MM-DD/<script_name>.log
    同时可选地在终端回显。
    """

    def __init__(self, script_name: str, echo: bool = True):
        """
        Args:
            script_name: 脚本名（不含扩展名），如 'pool_screener'
            echo: 是否同时输出到 stdout
        """
        self.script_name = script_name
        self.echo = echo

        today_dir = datetime.now().strftime("%Y-%m-%d")
        self.log_dir = os.path.join(LOGS_DIR, today_dir)
        os.makedirs(self.log_dir, exist_ok=True)

        self.log_path = os.path.join(self.log_dir, f"{script_name}.log")

    def log(self, message: str, level: str = "INFO"):
        """写入日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}"

        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(line + "\n")

        if self.echo:
            print(line)

    def info(self, message: str):
        self.log(message, "INFO")

    def warn(self, message: str):
        self.log(message, "WARN")

    def error(self, message: str):
        self.log(message, "ERROR")

    def success(self, message: str):
        self.log(message, "OK")

    def separator(self):
        line = "-" * 60
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
        if self.echo:
            print(line)
