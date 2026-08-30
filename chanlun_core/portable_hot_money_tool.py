# portable_hot_money_tool.py
# 移植自 FinGenius src/tool/hot_money.py
# 适配师傅的 chanlun_core 系统，纯数据计算，无 LLM 依赖
# 数据源：AKShare (东方财富)

import datetime
import sys
from typing import Dict, Any, List, Optional

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    print("警告: 需要安装 akshare，运行: pip install akshare")


def get_recent_trading_day() -> str:
    """获取最近交易日"""
    today = datetime.date.today()
    if today.weekday() >= 5:
        days_back = today.weekday() - 4
        today = today - datetime.timedelta(days=days_back)
    return today.strftime("%Y-%m-%d")


class PortableHotMoneyTool:
    """
    A股游资/资金流向分析工具 (移植版)
    功能：龙虎榜数据、个股资金流向、大盘资金流向、热门板块
    数据源：AKShare
    """

    def __init__(self):
        self.name = "portable_hot_money"
        self.description = "获取游资龙虎榜数据、资金流向、热门板块分析"

    def execute(self, stock_code: str, date: str = "", index_code: str = "",
                sector_types: str = "all", max_retry: int = 3) -> Dict[str, Any]:
        """
        执行游资/资金流向分析
        Args:
            stock_code: 6位股票代码
            date: 查询日期 YYYY-MM-DD，默认最近交易日
            index_code: 指数代码，默认与股票代码相同
            sector_types: 板块类型 all/hot/concept/regional/industry
            max_retry: 最大重试次数
        Returns:
            包含龙虎榜、资金流向、热门板块的字典
        """
        if not AKSHARE_AVAILABLE:
            return {"error": "AKShare未安装", "status": "dependency_missing"}

        clean_code = stock_code.replace('sh', '').replace('sz', '').replace('bj', '')
        query_date = date or get_recent_trading_day()
        actual_index = index_code or clean_code

        result = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stock_code": clean_code,
            "date": query_date,
            "data_quality": {}
        }

        # 5大数据维度（并行获取）
        result["stock_info"] = self._get_stock_info(clean_code)
        result["dragon_tiger"] = self._get_dragon_tiger(query_date, max_retry)
        result["stock_fund_flow"] = self._get_stock_fund_flow(clean_code, max_retry)
        result["market_fund_flow"] = self._get_market_fund_flow(max_retry)
        result["hot_sectors"] = self._get_hot_sectors(sector_types, max_retry)

        # 生成信号
        result["signals"] = self._generate_signals(result)

        return result

    def _get_stock_info(self, code: str) -> Dict:
        """获取股票实时行情"""
        try:
            print(f"获取 {code} 实时行情...")
            df = ak.stock_individual_info_em(symbol=code)
            if not df.empty:
                info = dict(zip(df['item'], df['value']))
                return {
                    "name": str(info.get("股票简称", "未知")),
                    "code": code,
                    "current_price": float(info.get("最新", 0)),
                    "total_shares": str(info.get("总股本", "")),
                    "market_cap": str(info.get("总市值", "")),
                    "pe_ratio": str(info.get("动态市盈率", "")),
                    "industry": str(info.get("行业", "")),
                    "data_source": "stock_individual_info_em",
                    "status": "success"
                }
        except Exception as e:
            print(f"⚠️ 实时行情获取失败: {e}")
        return {"name": "未知", "code": code, "current_price": 0.0,
                "data_source": "default", "status": "fallback"}

    def _get_dragon_tiger(self, date: str, max_retry: int) -> Dict:
        """获取龙虎榜数据（多源回退）"""
        # 尝试 efinance（FinGenius原版数据源）
        for attempt in range(1, max_retry + 1):
            try:
                print(f"获取 {date} 龙虎榜数据 (efinance)...")
                import efinance as ef
                df = ef.stock.get_daily_billboard(start_date=date, end_date=date)
                if not df.empty:
                    records = df.head(30).to_dict(orient="records")
                    print(f"✅ 龙虎榜(efinance): {len(records)} 条")
                    return self._summarize_dragon_tiger(date, records)
            except Exception as e:
                print(f"⚠️ efinance龙虎榜失败: {e}")
                break  # efinance 不可用，直接降级
        
        # 回退：尝试 AKShare 的其他龙虎榜接口
        for attempt in range(1, 2):
            try:
                print(f"获取 {date} 龙虎榜数据 (AKShare备选)...")
                # 尝试营业部排行（稳定接口）
                df = ak.stock_lhb_yybph_em()
                if not df.empty:
                    records = df.head(30).to_dict(orient="records")
                    print(f"✅ 龙虎榜(营业部排行): {len(records)} 条")
                    return self._summarize_dragon_tiger(date, records,
                                                         source="stock_lhb_yybph_em")
            except Exception as e:
                print(f"⚠️ AKShare龙虎榜备选失败: {e}")

        print("⚠️ 龙虎榜数据不可用（所有数据源失败），降级跳过")
        return {"data": {}, "data_source": "unavailable",
                "status": "fallback", "note": "龙虎榜接口暂不可用，资金流向和板块数据正常"}

    def _summarize_dragon_tiger(self, date, records, source="efinance"):
        """汇总龙虎榜数据"""
        total_buy = 0.0
        total_sell = 0.0
        for rec in records:
            try:
                total_buy += float(rec.get("买入额", rec.get("买入金额", 0)))
                total_sell += float(rec.get("卖出额", rec.get("卖出金额", 0)))
            except (ValueError, TypeError):
                pass
        return {
            "data": {
                "上榜股票数": len(records),
                "日期": date,
                "总买入额": round(total_buy, 2),
                "总卖出额": round(total_sell, 2),
                "净买入额": round(total_buy - total_sell, 2),
                "最新上榜": records[:10],
            },
            "data_source": source,
            "status": "success",
            "count": len(records)
        }

    def _get_stock_fund_flow(self, code: str, max_retry: int) -> Dict:
        """获取个股资金流向（主力/散户/超大单）"""
        for attempt in range(1, max_retry + 1):
            try:
                print(f"获取 {code} 个股资金流向...")
                # 尝试个股资金流向接口
                df = ak.stock_individual_fund_flow(stock=code, market="sh" if code.startswith("6") else "sz")
                if not df.empty:
                    recent = df.tail(5).to_dict(orient="records")
                    # 计算净流入趋势
                    net_flow_5d = 0.0
                    for rec in recent:
                        try:
                            net_flow_5d += float(rec.get("主力净流入-净额", 0))
                        except (ValueError, TypeError):
                            pass
                    print(f"✅ 个股资金流向: {len(recent)} 条, 5日主力净流入 {net_flow_5d:.2f}")
                    return {
                        "data": recent if len(recent) <= 10 else recent[-10:],
                        "net_flow_5d": round(net_flow_5d, 2),
                        "data_source": "stock_individual_fund_flow",
                        "status": "success"
                    }
            except Exception as e:
                print(f"⚠️ 个股资金流向失败 (attempt {attempt}): {e}")
                # 回退：尝试通过 stock_individual_info_em 获取部分信息
                if attempt == max_retry:
                    try:
                        df2 = ak.stock_individual_info_em(symbol=code)
                        return {"data": [], "data_source": "partial_fallback",
                                "status": "fallback", "error": str(e)}
                    except:
                        pass
        return {"data": [], "data_source": "failed", "status": "fallback"}

    def _get_market_fund_flow(self, max_retry: int) -> Dict:
        """获取全市场资金流向（北向资金、融资融券等）"""
        for attempt in range(1, max_retry + 1):
            try:
                print("获取市场整体资金流向...")
                df = ak.stock_market_fund_flow()
                if not df.empty:
                    recent = df.tail(5).to_dict(orient="records")
                    print(f"✅ 市场资金流向: {len(recent)} 条")
                    return {
                        "data": recent,
                        "data_source": "stock_market_fund_flow",
                        "status": "success"
                    }
            except Exception as e:
                print(f"⚠️ 市场资金流向失败 (attempt {attempt}): {e}")
                if attempt >= max_retry:
                    return {"data": [], "data_source": "failed",
                            "status": "fallback", "error": str(e)}
        return {"data": [], "data_source": "failed", "status": "fallback"}

    def _get_hot_sectors(self, sector_types: str, max_retry: int) -> Dict:
        """获取热门板块（概念/行业/地域）"""
        result = {"sector_types": sector_types, "data": {}}
        for attempt in range(1, max_retry + 1):
            try:
                # 获取概念板块
                if sector_types in ("all", "concept", "hot"):
                    print("获取概念板块数据...")
                    df_conc = ak.stock_board_concept_name_em()
                    if not df_conc.empty:
                        # 按涨跌幅排序取前10
                        top_conc = df_conc.nlargest(10, "涨跌幅").to_dict(orient="records")
                        result["data"]["top_concepts"] = top_conc
                        print(f"✅ 概念板块: {len(top_conc)} 个热门")

                # 获取行业板块
                if sector_types in ("all", "industry"):
                    print("获取行业板块数据...")
                    df_ind = ak.stock_board_industry_name_em()
                    if not df_ind.empty:
                        top_ind = df_ind.nlargest(10, "涨跌幅").to_dict(orient="records")
                        result["data"]["top_industries"] = top_ind
                        print(f"✅ 行业板块: {len(top_ind)} 个热门")

                result["data_source"] = "akshare_boards"
                result["status"] = "success"
                break
            except Exception as e:
                print(f"⚠️ 热门板块获取失败 (attempt {attempt}): {e}")
                if attempt >= max_retry:
                    result["data_source"] = "failed"
                    result["status"] = "fallback"
                    result["error"] = str(e)
        return result

    def _generate_signals(self, data: Dict) -> Dict:
        """生成交易信号"""
        signals = {"buy_signals": [], "sell_signals": [], "risk_warnings": []}

        # 1. 个股资金流向信号
        stock_flow = data.get("stock_fund_flow", {})
        net_flow_5d = stock_flow.get("net_flow_5d", 0)
        if net_flow_5d > 0:
            signals["buy_signals"].append(f"近5日主力净流入 {net_flow_5d:.2f} 万，资金面积极")
        elif net_flow_5d < -1000:
            signals["risk_warnings"].append(f"近5日主力净流出 {abs(net_flow_5d):.2f} 万，资金面谨慎")

        # 2. 龙虎榜信号
        dragon = data.get("dragon_tiger", {}).get("data", {})
        net_buy = dragon.get("净买入额", 0)
        if net_buy > 0:
            signals["buy_signals"].append(f"龙虎榜净买入 {net_buy:.2f} 万，机构参与度较高")
        elif net_buy < -500:
            signals["risk_warnings"].append(f"龙虎榜净卖出 {abs(net_buy):.2f} 万，机构可能减持")

        # 3. 板块热度信号
        hot_sectors = data.get("hot_sectors", {}).get("data", {})
        top_concepts = hot_sectors.get("top_concepts", [])
        if top_concepts:
            top_concept_names = [c.get("板块名称", "") for c in top_concepts[:3]]
            signals["buy_signals"].append(f"热门概念: {', '.join(top_concept_names)}")

        return signals


def test_hot_money_tool(stock_code: str = "688036"):
    """测试游资/资金流向工具"""
    print(f"\n{'='*60}")
    print(f"测试 PortableHotMoneyTool: {stock_code}")
    print(f"{'='*60}\n")

    tool = PortableHotMoneyTool()
    result = tool.execute(stock_code)

    if result.get("status") == "dependency_missing":
        print(f"❌ 错误: {result.get('error')}")
        return result

    # 股票信息
    info = result.get("stock_info", {})
    print(f"📈 股票: {info.get('name', 'N/A')} ({info.get('code', 'N/A')})")
    print(f"💰 当前价: {info.get('current_price', 'N/A')}")

    # 龙虎榜
    dragon = result.get("dragon_tiger", {})
    dragon_data = dragon.get("data", {})
    print(f"\n📊 龙虎榜 ({dragon.get('date', 'N/A')}):")
    print(f"   上榜数量: {dragon_data.get('上榜股票数', 0)}")
    print(f"   净买入额: {dragon_data.get('净买入额', 0):.2f} 万")

    # 个股资金流向
    stock_flow = result.get("stock_fund_flow", {})
    print(f"\n💸 个股资金流向:")
    print(f"   5日主力净流入: {stock_flow.get('net_flow_5d', 'N/A')}")

    # 热门板块
    hot = result.get("hot_sectors", {}).get("data", {})
    print(f"\n🔥 热门板块:")
    top_conc = hot.get("top_concepts", [])[:3]
    for c in top_conc:
        print(f"   {c.get('板块名称', 'N/A')}: {c.get('涨跌幅', 'N/A')}%")

    # 信号
    sigs = result.get("signals", {})
    if sigs.get("buy_signals"):
        print(f"\n✅ 买入信号:")
        for s in sigs["buy_signals"]:
            print(f"   - {s}")
    if sigs.get("risk_warnings"):
        print(f"\n⚠️ 风险预警:")
        for w in sigs["risk_warnings"]:
            print(f"   - {w}")

    print(f"\n{'='*60}")
    print("测试完成！")
    print(f"{'='*60}\n")
    return result


if __name__ == "__main__":
    stock_code = sys.argv[1] if len(sys.argv) > 1 else "688036"
    test_hot_money_tool(stock_code)
