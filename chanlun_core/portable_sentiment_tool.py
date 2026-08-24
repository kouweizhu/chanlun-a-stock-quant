# portable_sentiment_tool.py
# 移植自 FinGenius src/tool/sentiment.py
# 适配师傅的 chanlun_core 系统，纯数据计算，无 LLM 依赖
# 市场情绪通过板块涨跌、资金流向、涨跌家数等量化指标表征
# 数据源：AKShare

import datetime
import sys
from typing import Dict, Any, List

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    print("警告: 需要安装 akshare，运行: pip install akshare")


class PortableSentimentTool:
    """
    A股市场情绪分析工具 (移植版)
    功能：板块热度、市场资金流向、涨跌分布、情绪评分
    不依赖 LLM，纯量化指标
    数据源：AKShare
    """

    def __init__(self):
        self.name = "portable_sentiment"
        self.description = "获取市场情绪数据：板块热度、资金流向、涨跌分布"

    def execute(self, index_code: str = "000001", sector_types: str = "all",
                max_retry: int = 3) -> Dict[str, Any]:
        """
        执行市场情绪分析
        Args:
            index_code: 指数代码（000001上证/399001深证/000300沪深300/000905中证500）
            sector_types: 板块类型 all/hot/concept/regional/industry
            max_retry: 最大重试次数
        Returns:
            包含市场情绪各维度的字典
        """
        if not AKSHARE_AVAILABLE:
            return {"error": "AKShare未安装", "status": "dependency_missing"}

        result = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "index_code": index_code,
            "data_quality": {}
        }

        # 4大情绪维度
        result["hot_sectors"] = self._get_hot_sectors(sector_types, max_retry)
        result["market_fund_flow"] = self._get_market_fund_flow(max_retry)
        result["zen_ratio"] = self._get_zen_ratio(max_retry)
        result["market_breadth"] = self._get_market_breadth(max_retry)

        # 情绪评分 (0-100)
        result["sentiment_score"] = self._calculate_sentiment_score(result)

        return result

    def _get_hot_sectors(self, sector_types: str, max_retry: int) -> Dict:
        """获取热门板块（概念+行业+地域）"""
        result = {"sector_types": sector_types, "data": {}}
        for attempt in range(1, max_retry + 1):
            try:
                if sector_types in ("all", "concept", "hot"):
                    print("获取概念板块...")
                    df = ak.stock_board_concept_name_em()
                    if not df.empty:
                        top = df.nlargest(10, "涨跌幅")
                        bottom = df.nsmallest(10, "涨跌幅")
                        result["data"]["top_concepts"] = top.to_dict(orient="records")
                        result["data"]["bottom_concepts"] = bottom.to_dict(orient="records")

                if sector_types in ("all", "industry"):
                    print("获取行业板块...")
                    df = ak.stock_board_industry_name_em()
                    if not df.empty:
                        top = df.nlargest(10, "涨跌幅")
                        result["data"]["top_industries"] = top.to_dict(orient="records")

                result["data_source"] = "akshare_boards"
                result["status"] = "success"
                print(f"✅ 热门板块数据获取成功")
                break
            except Exception as e:
                print(f"⚠️ 热门板块失败 (attempt {attempt}): {e}")
                if attempt >= max_retry:
                    result["data_source"] = "failed"
                    result["status"] = "fallback"
                    result["error"] = str(e)
        return result

    def _get_market_fund_flow(self, max_retry: int) -> Dict:
        """获取市场资金流向"""
        for attempt in range(1, max_retry + 1):
            try:
                print("获取市场资金流向...")
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

    def _get_zen_ratio(self, max_retry: int) -> Dict:
        """获取市场涨跌比（涨跌家数）"""
        for attempt in range(1, max_retry + 1):
            try:
                print("获取市场涨跌分布...")
                # 使用 stock_zh_a_spot_em 获取全市场涨跌统计
                df = ak.stock_zh_a_spot_em()
                if not df.empty:
                    total = len(df)
                    up_stocks = len(df[df["涨跌幅"] > 0])
                    down_stocks = len(df[df["涨跌幅"] < 0])
                    flat_stocks = total - up_stocks - down_stocks

                    # 涨跌停统计
                    limit_up = len(df[df["涨跌幅"] >= 9.9])
                    limit_down = len(df[df["涨跌幅"] <= -9.9])

                    # 平均涨跌幅
                    avg_change = float(df["涨跌幅"].mean())

                    print(f"✅ 涨跌分布: 涨{up_stocks} 跌{down_stocks} 平{flat_stocks}")
                    return {
                        "total": total,
                        "up_stocks": up_stocks,
                        "down_stocks": down_stocks,
                        "flat_stocks": flat_stocks,
                        "zen_ratio": round(up_stocks / max(down_stocks, 1), 2),
                        "limit_up": limit_up,
                        "limit_down": limit_down,
                        "avg_change": round(avg_change, 2),
                        "data_source": "stock_zh_a_spot_em",
                        "status": "success"
                    }
            except Exception as e:
                print(f"⚠️ 涨跌分布失败 (attempt {attempt}): {e}")
                if attempt >= max_retry:
                    return {"total": 0, "data_source": "failed",
                            "status": "fallback", "error": str(e)}
        return {"total": 0, "data_source": "failed", "status": "fallback"}

    def _get_market_breadth(self, max_retry: int) -> Dict:
        """获取市场广度（行业板块涨跌比）"""
        for attempt in range(1, max_retry + 1):
            try:
                print("获取市场广度...")
                df = ak.stock_board_industry_name_em()
                if not df.empty:
                    total_ind = len(df)
                    up_ind = len(df[df["涨跌幅"] > 0])
                    down_ind = len(df[df["涨跌幅"] < 0])
                    print(f"✅ 市场广度: {up_ind}/{down_ind} 行业上涨")
                    return {
                        "total_industries": total_ind,
                        "up_industries": up_ind,
                        "down_industries": down_ind,
                        "breadth_ratio": round(up_ind / max(down_ind, 1), 2),
                        "data_source": "stock_board_industry_name_em",
                        "status": "success"
                    }
            except Exception as e:
                print(f"⚠️ 市场广度失败 (attempt {attempt}): {e}")
                if attempt >= max_retry:
                    return {"data_source": "failed", "status": "fallback", "error": str(e)}
        return {"data_source": "failed", "status": "fallback"}

    def _calculate_sentiment_score(self, data: Dict) -> Dict:
        """
        计算情绪评分 (0-100)
        50 = 中性，>50 = 偏暖，<50 = 偏冷
        参考维度：
        - 涨跌比 (权重 40%)
        - 市场广度 (权重 30%)
        - 资金流向 (权重 20%)
        - 涨停数量 (权重 10%)
        """
        scores = {}
        details = {}

        # 1. 涨跌比评分
        zen = data.get("zen_ratio", {})
        if zen.get("data_source") != "failed":
            zen_ratio = zen.get("zen_ratio", 1.0)
            # 映射: 0.5→30分, 1.0→50分, 2.0→70分, 3.0+→90分
            zen_score = min(30 + zen_ratio * 20, 100)
            scores["zen_ratio"] = zen_score * 0.40
            details["涨跌比"] = f"{zen_ratio}x → {round(zen_score, 1)}分"

        # 2. 市场广度评分
        breadth = data.get("market_breadth", {})
        if breadth.get("data_source") != "failed":
            br = breadth.get("breadth_ratio", 1.0)
            breadth_score = min(30 + br * 20, 100)
            scores["breadth"] = breadth_score * 0.30
            details["市场广度"] = f"{br}x → {round(breadth_score, 1)}分"

        # 3. 资金流向评分
        mkt_flow = data.get("market_fund_flow", {})
        if mkt_flow.get("data_source") != "failed":
            recent_data = mkt_flow.get("data", [])
            if recent_data:
                try:
                    latest_flow = float(recent_data[-1].get("主力净流入-净额", 0))
                    flow_score = 50 + min(latest_flow / 10000 * 10, 40)  # 每100亿净流入+10分, 上限40
                    flow_score = max(10, min(flow_score, 90))
                except:
                    flow_score = 50
                scores["fund_flow"] = flow_score * 0.20
                details["资金流向"] = f"{flow_score:.1f}分"

        # 4. 涨停数量评分
        if zen.get("data_source") != "failed":
            limit_up = zen.get("limit_up", 0)
            # 涨停<30→30分, 30-60→50分, 60-100→70分, >100→90分
            if limit_up < 30:
                limit_score = 30
            elif limit_up < 60:
                limit_score = 50
            elif limit_up < 100:
                limit_score = 70
            else:
                limit_score = 90
            scores["limit_up"] = limit_score * 0.10
            details["涨停数量"] = f"{limit_up}只 → {limit_score}分"

        total_score = round(sum(scores.values()), 1)

        # 分级
        if total_score >= 70:
            level = "偏暖"
        elif total_score >= 45:
            level = "中性"
        else:
            level = "偏冷"

        return {
            "total_score": total_score,
            "level": level,
            "details": details,
            "breakdown": {k: round(v, 1) for k, v in scores.items()}
        }


def test_sentiment_tool(index_code: str = "000001"):
    """测试市场情绪工具"""
    print(f"\n{'='*60}")
    print(f"测试 PortableSentimentTool: 指数 {index_code}")
    print(f"{'='*60}\n")

    tool = PortableSentimentTool()
    result = tool.execute(index_code)

    if result.get("status") == "dependency_missing":
        print(f"❌ 错误: {result.get('error')}")
        return result

    # 涨跌比
    zen = result.get("zen_ratio", {})
    print(f"📊 市场涨跌分布:")
    print(f"   上涨: {zen.get('up_stocks', 0)} 只")
    print(f"   下跌: {zen.get('down_stocks', 0)} 只")
    print(f"   涨停: {zen.get('limit_up', 0)} 只 / 跌停: {zen.get('limit_down', 0)} 只")
    print(f"   涨跌比: {zen.get('zen_ratio', 'N/A')}x")

    # 市场广度
    breadth = result.get("market_breadth", {})
    print(f"\n📈 市场广度:")
    print(f"   上涨行业: {breadth.get('up_industries', 0)}")
    print(f"   下跌行业: {breadth.get('down_industries', 0)}")
    print(f"   广度比: {breadth.get('breadth_ratio', 'N/A')}x")

    # 热门板块
    hot = result.get("hot_sectors", {}).get("data", {})
    print(f"\n🔥 热门板块 Top5:")
    top_conc = hot.get("top_concepts", [])[:5]
    for c in top_conc:
        print(f"   {c.get('板块名称', 'N/A')}: {c.get('涨跌幅', 'N/A')}%")

    # 情绪评分
    score = result.get("sentiment_score", {})
    print(f"\n🎯 市场情绪评分:")
    print(f"   总分: {score.get('total_score', 'N/A')}")
    print(f"   等级: {score.get('level', 'N/A')}")
    print(f"   明细:")
    for k, v in score.get("details", {}).items():
        print(f"     {k}: {v}")

    print(f"\n{'='*60}")
    print("测试完成！")
    print(f"{'='*60}\n")
    return result


if __name__ == "__main__":
    index_code = sys.argv[1] if len(sys.argv) > 1 else "000001"
    test_sentiment_tool(index_code)
