import jieba
import pandas as pd
import akshare as ak
from data.datasource import get_cailian_news, get_sector_fund, safe_request
from config.settings import HOT_SCORE_THRESHOLD
from core.market_sentiment import calc_sentiment_score


def analyze_hot_sector():
    fund_df = get_sector_fund()
    source_type = "东方财富资金接口"

    # 第一层容错：东方财富资金接口失效 → 切换申万一级行业指数（仅涨跌幅，无资金）
    if fund_df is None or fund_df.empty:
        print("⚠️东方财富板块资金接口访问失败，切换【申万一级行业】备用数据源，资金权重归零")
        # 修复正确接口名称 ak.sw_spot_index()
        fund_df = safe_request(ak.sw_spot_index)
        source_type = "申万行业备用接口"
        if fund_df is None or fund_df.empty:
            print("❌所有板块数据源全部获取失败")
            return pd.DataFrame(), {}
        # 统一字段名称，适配后续代码
        fund_df.rename(columns={"指数名称": "板块名称"}, inplace=True)
        fund_df["主力净流入-亿"] = 0
        # 申万接口涨跌幅字段
        if "涨跌幅" not in fund_df.columns:
            fund_df["涨跌幅"] = fund_df["涨跌"]

    # 财联社新闻（接口失效，保留兼容）
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
    if not hot_sector_df.empty:
        hot_sector_df = hot_sector_df.sort_values("total_score", ascending=False)
        print(f"✅ 数据源：{source_type}，筛选达标板块数量：{len(hot_sector_df)}")
    else:
        print(f"⚠️数据源：{source_type}，无板块达到热度阈值")

    return hot_sector_df, sentiment_info
