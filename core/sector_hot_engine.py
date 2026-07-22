import pandas as pd
import jieba
from data.datasource import get_cailian_news, get_sector_fund
from config.settings import HOT_SCORE_THRESHOLD
from core.market_sentiment import calc_sentiment_score

def analyze_hot_sector():
    fund_df = get_sector_fund()
    if fund_df is None or fund_df.empty:
        print("⚠️板块资金接口获取失败，当前云端网络环境受限，任务终止")
        # 返回空df，上层识别后结束流程，不再抛出崩溃异常
        import pandas as pd
        return pd.DataFrame(), {}
    news_df = get_cailian_news()
    word_freq = {}
    if news_df is not None and not news_df.empty:
        all_title = "".join(news_df["标题"].astype(str))
        words = jieba.lcut(all_title)
        for w in words:
            if len(w) >=2:
                word_freq[w] = word_freq.get(w,0)+1

    sent_info = calc_sentiment_score()
    emotion_factor = sent_info["factor"]
    hot_result = []
    for _,row in fund_df.iterrows():
        sector = row["板块名称"]
        fund_flow = float(row["主力净流入-亿"])
        chg = float(row["涨跌幅"])
        news_score = word_freq.get(sector, 0) * 0.3
        fund_score = max(fund_flow,0) * 0.4
        chg_score = max(chg,0)*30 * 0.3
        total_score = news_score + fund_score + chg_score
        total_score = round(total_score * emotion_factor, 2)
        if total_score >= HOT_SCORE_THRESHOLD:
            hot_result.append({
                "sector_name": sector,
                "total_score": total_score,
                "main_fund_flow": fund_flow,
                "sector_change": chg
            })
    hot_df = pd.DataFrame(hot_result)
    hot_df = hot_df.sort_values("total_score",ascending=False)
    return hot_df, sent_info
