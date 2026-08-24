"""
file_utils.py — 跨模块文件读写安全层

提供原子写入 + filelock 双重保护，消除 JSON/Excel 文件 IPC 的
数据竞争和脏读风险。

用法:
    from file_utils import safe_read_json, safe_write_json
    data = safe_read_json("/path/to/file.json")
    safe_write_json("/path/to/file.json", data)
"""

import os
from date_utils import date_to_str, parse_date_to_datetime
import json
import tempfile
import pandas as pd
from filelock import FileLock, Timeout


def _get_lock_path(file_path: str) -> str:
    """获取锁文件路径"""
    return file_path + ".lock"


def safe_read_json(file_path: str, default=None, lock_timeout: int = 30) -> dict:
    """安全读取 JSON 文件（带 filelock）
    
    Args:
        file_path: JSON 文件路径
        default: 文件不存在时的默认返回值
        lock_timeout: 获取锁的超时秒数
    
    Returns:
        dict: 解析后的 JSON 数据
    """
    if not os.path.exists(file_path):
        return default if default is not None else {}
    
    lock_path = _get_lock_path(file_path)
    try:
        with FileLock(lock_path, timeout=lock_timeout):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Timeout:
        # 锁超时：回退到无保护读（至少比什么都不做强）
        print(f"[file_utils] WARNING: 锁超时 ({lock_path})，无保护读取")
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)


def safe_write_json(file_path: str, data: dict, lock_timeout: int = 60) -> None:
    """安全写入 JSON 文件（原子写 + filelock）
    
    先写临时文件，再 os.replace() 原子重命名。
    保证读者永远不会读到半成品。
    
    Args:
        file_path: JSON 文件路径
        data: 要写入的数据
        lock_timeout: 获取锁的超时秒数
    """
    lock_path = _get_lock_path(file_path)
    
    try:
        with FileLock(lock_path, timeout=lock_timeout):
            # 1. 写到临时文件（同目录，确保 os.replace 是原子操作）
            fd, tmp_path = tempfile.mkstemp(
                suffix='.json',
                prefix='.tmp_',
                dir=os.path.dirname(file_path) or '.'
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                # 2. 原子替换
                os.replace(tmp_path, file_path)
            except Exception:
                # 清理临时文件
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
    except Timeout:
        # 锁超时：回退到直接写入（至少完成写入）
        print(f"[file_utils] WARNING: 锁超时 ({lock_path})，无保护写入")
        fd, tmp_path = tempfile.mkstemp(
            suffix='.json',
            prefix='.tmp_',
            dir=os.path.dirname(file_path) or '.'
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, file_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


def safe_write_excel(file_path: str, df: pd.DataFrame, lock_timeout: int = 60,
                     **to_excel_kwargs) -> None:
    """安全写入 Excel 文件（原子写 + filelock）"""
    lock_path = _get_lock_path(file_path)
    
    try:
        with FileLock(lock_path, timeout=lock_timeout):
            fd, tmp_path = tempfile.mkstemp(
                suffix='.xlsx',
                prefix='.tmp_',
                dir=os.path.dirname(file_path) or '.'
            )
            try:
                with os.fdopen(fd, 'wb') as f:
                    df.to_excel(f, **to_excel_kwargs)
                os.replace(tmp_path, file_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
    except Timeout:
        print(f"[file_utils] WARNING: 锁超时 ({lock_path})，无保护写入")
        fd, tmp_path = tempfile.mkstemp(
            suffix='.xlsx',
            prefix='.tmp_',
            dir=os.path.dirname(file_path) or '.'
        )
        try:
            with os.fdopen(fd, 'wb') as f:
                df.to_excel(f, **to_excel_kwargs)
            os.replace(tmp_path, file_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
