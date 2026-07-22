import jieba
import pandas as pd
from data.datasource import get_cailian_news, get_sector_fund, safe_request
from config.settings import HOT_SCORE_THRESHOLD
from core.market_sentiment import calc_sentiment_score
import akshare as ak


def analyze_hot_sector():
    fund_df = get_sector_fund()
    # 容错逻辑：板块资金接口失效，自动切换备用板块列表
    if fund_df is None or fund_df.empty:
        print("⚠️板块资金接口获取失败，云端网络受限，切换备用板块列表，资金权重归零")
        # 备用接口：获取全部行业板块
        fund_df = safe_request(ak.stock_board_industry_name_em)
        if fund_df is None or fund_df.empty:
            print("❌备用板块数据源同样获取失败，无法继续分析")
            return pd.DataFrame(), {}
        # 补齐缺失字段，防止代码报错
        fund_df["主力净流入-亿"] = 0
        fund_df["涨跌幅"] = 0

    # 财联社新闻（接口已失效，保留兼容代码，不影响运行）
    news_df = get_cailian_news()
    word_freq = {}
    if news_df is not None and not news_df.empty:
        all_title = "".join(news_df["标题"].astype(str))
        words = jieba.lcut(all_title)
        for w in words:
            if len(w) >= 2:
                word_freq[w] = word_freq.get(w, 0) + 1

    sentiment_info = calc_sentiment_score()
    emotion_factor = sentiment_info["factor"]
    hot_result = []

    for _, row in fund_df.iterrows():
        sector_name = row["板块名称"]
        try:
            fund_flow = float(row["主力净流入-亿"])
        except:
            fund_flow = 0
        try:
            sector_chg = float(row["涨跌幅"])
        except:
            sector_chg = 0

        # 热度打分公式
        news_score = word_freq.get(sector_name, 0) * 0.3
        fund_score = max(fund_flow, 0) * 0.4
        change_score = max(sector_chg, 0) * 30 * 0.3
        total_score = news_score + fund_score + change_score
        total_score = round(total_score * emotion_factor, 2)

        if total_score >= HOT_SCORE_THRESHOLD:
            hot_result.append({
                "sector_name": sector_name,
                "total_score": total_score,
                "main_fund_flow": fund_flow,
                "sector_change": sector_chg
            })

    hot_sector_df = pd.DataFrame(hot_result)
    # 按照热度分数从高到低排序
    if not hot_sector_df.empty:
        hot_sector_df = hot_sector_df.sort_values("total_score", ascending=False)

    return hot_sector_df, sentiment_info
