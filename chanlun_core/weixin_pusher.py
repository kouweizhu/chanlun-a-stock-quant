"""
weixin_pusher.py - 微信实时信号推送模块

封装 Hermes 微信发送能力，为交易系统提供独立的推送接口。
无需启动 gateway，直接使用 iLink API 发送消息。

用法：
    from weixin_pusher import WeixinPusher, wx_send, wx_signal
    
    pusher = WeixinPusher()
    pusher.send("买点信号：贵州茅台 600519，一类买点确认，建议仓位 50%")

环境变量（已配置在 Hermes 中）：
    WEIXIN_TOKEN        - iLink Bot Token
    WEIXIN_ACCOUNT_ID   - 微信账号ID
    WEIXIN_CHAT_ID      - 目标聊天ID（默认 filehelper）
"""

import os
from date_utils import date_to_str, parse_date_to_datetime
import sys
import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Hermes 路径注入
HERMES_ROOT = Path.home() / ".hermes" / "hermes-agent"
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))


class WeixinPusher:
    """
    微信推送器 - 基于 Hermes iLink Bot API
    
    特性：
    - 无需启动 gateway，独立运行
    - 自动分片长消息（微信限制约2000字）
    - 支持 Markdown 简化格式
    - 失败重试 3 次
    """
    
    # 微信单条消息长度限制（保守值）
    MAX_LENGTH = 1800
    
    def __init__(
        self,
        token: Optional[str] = None,
        account_id: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        """
        初始化推送器
        
        Args:
            token: iLink Bot Token，默认从 WEIXIN_TOKEN 环境变量读取
            account_id: 微信账号ID，默认从 WEIXIN_ACCOUNT_ID 环境变量读取
            chat_id: 目标聊天ID，默认从 WEIXIN_CHAT_ID 读取，否则用 filehelper
        """
        # 优先使用 Hermes 的 get_env_value（支持 ~/.hermes/.env 文件）
        try:
            from hermes_cli.config import get_env_value
            self.token = token or get_env_value("WEIXIN_TOKEN") or ""
            self.account_id = account_id or get_env_value("WEIXIN_ACCOUNT_ID") or ""
            # 默认使用 HOME_CHANNEL（这是微信配置中的目标聊天ID）
            # filehelper 仅作为最后 fallback，通常需要正确的 chat_id
            self.chat_id = chat_id or get_env_value("WEIXIN_CHAT_ID") or get_env_value("WEIXIN_HOME_CHANNEL") or "filehelper"
        except ImportError:
            self.token = token or os.getenv("WEIXIN_TOKEN", "")
            self.account_id = account_id or os.getenv("WEIXIN_ACCOUNT_ID", "")
            self.chat_id = chat_id or os.getenv("WEIXIN_CHAT_ID", "filehelper")
        
        if not self.token:
            logger.warning("WEIXIN_TOKEN not set, push will fail")
        if not self.account_id:
            logger.warning("WEIXIN_ACCOUNT_ID not set, push will fail")
    
    def _check_config(self) -> bool:
        """检查配置是否完整"""
        if not self.token:
            logger.error("WEIXIN_TOKEN not configured")
            return False
        if not self.account_id:
            logger.error("WEIXIN_ACCOUNT_ID not configured")
            return False
        return True
    
    def _chunk_message(self, message: str) -> List[str]:
        """将长消息分片"""
        if len(message) <= self.MAX_LENGTH:
            return [message]
        
        chunks = []
        current = ""
        for line in message.split("\n"):
            if len(current) + len(line) + 1 > self.MAX_LENGTH:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        
        # 添加分片标记
        if len(chunks) > 1:
            for i, chunk in enumerate(chunks):
                chunks[i] = f"[{i+1}/{len(chunks)}]\n{chunk}"
        
        return chunks
    
    def _format_for_weixin(self, message: str) -> str:
        """将 Markdown 简化为微信友好格式"""
        import re
        
        # 简化标题
        message = re.sub(r'^#{1,6}\s+', '[', message, flags=re.MULTILINE)
        message = re.sub(r'\n#{1,6}\s+', '\n[', message)
        
        # 简化代码块
        message = re.sub(r'```[a-z]*\n?', '\n---\n', message)
        message = re.sub(r'```', '\n---\n', message)
        
        # 简化行内代码
        message = re.sub(r'`([^`]+)`', r'\1', message)
        
        # 简化链接 [text](url) -> text
        message = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', message)
        
        # 简化表格（保留内容）
        message = re.sub(r'\|?\s*[-:]+\s*\|', '', message)
        message = re.sub(r'\|', ' ', message)
        
        # 清理多余空行
        message = re.sub(r'\n{3,}', '\n\n', message)
        
        return message.strip()
    
    async def _send_single(self, message: str) -> Dict[str, Any]:
        """发送单条消息（异步核心）"""
        if not self._check_config():
            return {"error": "配置不完整"}
        
        try:
            from gateway.platforms.weixin import check_weixin_requirements, send_weixin_direct
            
            if not check_weixin_requirements():
                return {"error": "微信依赖未满足（需要 aiohttp + cryptography）"}
            
            result = await send_weixin_direct(
                extra={"account_id": self.account_id},
                token=self.token,
                chat_id=self.chat_id,
                message=message,
                media_files=None,
            )
            return result
            
        except Exception as e:
            logger.error(f"微信发送失败: {e}")
            return {"error": str(e)}
    
    def send(self, message: str, format_md: bool = True) -> bool:
        """
        发送消息到微信（同步接口）
        
        Args:
            message: 消息内容
            format_md: 是否将 Markdown 简化为微信格式
        
        Returns:
            bool: 是否全部发送成功
        """
        if format_md:
            message = self._format_for_weixin(message)
        
        chunks = self._chunk_message(message)
        all_success = True
        
        for i, chunk in enumerate(chunks):
            # 重试 3 次
            for attempt in range(3):
                try:
                    result = asyncio.run(self._send_single(chunk))
                    if result and not result.get("error"):
                        logger.info(f"微信发送成功 [{i+1}/{len(chunks)}]")
                        break
                    else:
                        error = result.get("error", "未知错误") if result else "无返回"
                        logger.warning(f"发送失败 (尝试 {attempt+1}/3): {error}")
                        if attempt == 2:
                            all_success = False
                except Exception as e:
                    logger.warning(f"发送异常 (尝试 {attempt+1}/3): {e}")
                    if attempt == 2:
                        all_success = False
        
        return all_success
    
    def send_signal_alert(
        self,
        stock_name: str,
        stock_code: str,
        signal_type: str,
        price: float,
        tech_score: int,
        fund_score: int,
        news_score: int,
        composite_score: float,
        grade: str,
        position_pct: float,
        reason: str = "",
    ) -> bool:
        """
        发送交易信号告警（结构化格式）
        """
        emoji_map = {
            "买点": "BUY",
            "卖点": "SELL",
            "止损": "STOP",
            "止盈": "PROFIT",
            "持仓监控": "WATCH",
            "数据源告警": "ALERT",
        }
        emoji = emoji_map.get(signal_type, "SIGNAL")
        
        grade_label = {"A": "A", "B": "B", "C": "C", "D": "D"}.get(grade, "?")
        
        message = f"""[{emoji}] {signal_type}信号

{stock_name} ({stock_code})
现价: Y{price:.2f}

[三维评分]
技术面: {tech_score}/100
基本面: {fund_score}/100
消息面: {news_score}/100
综合: {composite_score:.1f} [{grade_label}]级

[仓位建议] {position_pct:.0f}%
"""
        if reason:
            message += f"\n[说明] {reason}"
        
        message += f"\n\n{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        return self.send(message)
    
    def send_position_summary(self, positions: List[Dict]) -> bool:
        """
        发送持仓汇总
        """
        if not positions:
            return self.send("[EMPTY] 当前无持仓")
        
        total_pnl = sum(p.get("pnl", 0) for p in positions)
        pnl_sign = "+" if total_pnl >= 0 else ""
        
        lines = [
            f"[WATCH] 持仓监控汇总 ({len(positions)}只)",
            "",
            f"总盈亏: {pnl_sign}Y{total_pnl:,.2f}",
            "",
            "[明细]",
        ]
        
        for p in positions:
            code = p.get("code", "")
            name = p.get("name", "")
            price = p.get("price", 0)
            cost = p.get("cost", 0)
            pnl = p.get("pnl", 0)
            pnl_pct = p.get("pnl_pct", 0)
            stop = p.get("stop_loss", 0)
            
            status = "UP" if pnl >= 0 else "DOWN"
            alert = ""
            if stop > 0 and price <= stop:
                alert = " [STOP TRIGGERED]"
            
            lines.append(
                f"[{status}] {name}({code}) 现Y{price:.2f} 成Y{cost:.2f} "
                f"盈亏{pnl_pct:+.2f}%{alert}"
            )
        
        lines.append(f"\n{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        return self.send("\n".join(lines))
    
    def send_data_health_alert(self, failures: List[Tuple[str, str]]) -> bool:
        """
        发送数据源健康告警
        """
        if not failures:
            return True
        
        message = "[ALERT] 数据源故障告警\n\n以下数据源异常："
        for name, detail in failures:
            message += f"\n- {name}: {detail}"
        
        message += f"\n\n{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        return self.send(message)


# ============================================================
# 快捷函数（无需实例化）
# ============================================================

_default_pusher: Optional[WeixinPusher] = None


def _get_default_pusher() -> WeixinPusher:
    global _default_pusher
    if _default_pusher is None:
        _default_pusher = WeixinPusher()
    return _default_pusher


def wx_send(message: str, format_md: bool = True) -> bool:
    """快捷发送消息"""
    return _get_default_pusher().send(message, format_md=format_md)


def wx_signal(
    stock_name: str,
    stock_code: str,
    signal_type: str,
    price: float,
    tech_score: int,
    fund_score: int,
    news_score: int,
    composite_score: float,
    grade: str,
    position_pct: float,
    reason: str = "",
) -> bool:
    """快捷发送交易信号"""
    return _get_default_pusher().send_signal_alert(
        stock_name, stock_code, signal_type, price,
        tech_score, fund_score, news_score,
        composite_score, grade, position_pct, reason,
    )


def wx_positions(positions: List[Dict]) -> bool:
    """快捷发送持仓汇总"""
    return _get_default_pusher().send_position_summary(positions)


def wx_alert(failures: List[Tuple[str, str]]) -> bool:
    """快捷发送数据源告警"""
    return _get_default_pusher().send_data_health_alert(failures)


# ============================================================
# CLI 测试
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="微信推送测试")
    parser.add_argument("message", nargs="?", default="[TEST] 微信推送测试消息", help="测试消息内容")
    parser.add_argument("--token", help="iLink Bot Token")
    parser.add_argument("--account", help="微信账号ID")
    parser.add_argument("--chat", default=None, help="目标聊天ID（默认从 WEIXIN_HOME_CHANNEL 读取）")
    args = parser.parse_args()
    
    pusher = WeixinPusher(
        token=args.token,
        account_id=args.account,
        chat_id=args.chat,
    )
    
    print(f"Token: {'set' if pusher.token else 'not set'}")
    print(f"Account: {'set' if pusher.account_id else 'not set'}")
    print(f"Chat ID: {pusher.chat_id}")
    print(f"Message: {args.message[:50]}...")
    
    success = pusher.send(args.message)
    print(f"Result: {'OK' if success else 'FAILED'}")