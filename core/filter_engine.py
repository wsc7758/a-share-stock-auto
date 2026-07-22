from data.datasource import get_stock_announcement
from datetime import datetime, timedelta
from config.settings import NEWS_CHECK_DAY
import pandas as pd

BAD_KEYWORD = [
    "减持", "清仓", "问询函", "监管函", "立案调查", "业绩亏损",
    "风险提示", "终止重组", "商誉减值", "担保逾期"
]

def has_bad_news(code) -> bool:
    df = get_stock_announcement(code)
    if df is None or df.empty:
        return False
    cutoff_day = datetime.now() - timedelta(days=NEWS_CHECK_DAY)
    df["公告时间"] = pd.to_datetime(df["公告时间"])
    df = df[df["公告时间"] >= cutoff_day]
    titles = df["公告标题"].str.cat(sep=" ")
    for word in BAD_KEYWORD:
        if word in titles:
            print(f"⚠️ {code} 检测到利空公告：{word}")
            return True
    return False
