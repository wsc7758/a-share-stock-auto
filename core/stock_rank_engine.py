import pandas as pd
from data.datasource import get_stock_kline, get_stock_north_money, get_stock_limit_up
from core.filter_engine import has_bad_news
from core.moat_engine import detect_moat
from core.tech_engine import custom_trade_model
from core.trade_strategy_engine import build_trading_strategy
from config.settings import (
    LEADER_NUM, FILTER_ST, MAX_TURN_RATE, MIN_TURN_RATE,
    WEIGHT_INCREASE, WEIGHT_VOLUME, WEIGHT_LIMIT_UP,
    WEIGHT_NORTH_MONEY, WEIGHT_TURNOVER, RECENT_DAY
)

def calc_stock_score(code, name):
    df = get_stock_kline(code)
    if df is None or len(df) < 20:
        return 0
    df_period = df.tail(RECENT_DAY).copy()
    start_price = df_period.iloc[0]["close"]
    end_price = df_period.iloc[-1]["close"]
    increase_rate = (end_price - start_price) / start_price * 100
    vol_sum = df_period["volume"].sum()
    score_inc = max(increase_rate, 0) * (WEIGHT_INCREASE / 100)
    vol_score = min(vol_sum / 800000000, 10) * (WEIGHT_VOLUME / 100)
    zt_count = get_stock_limit_up(code)
    score_zt = min(zt_count,5) * (WEIGHT_LIMIT_UP / 100)
    north_flow = get_stock_north_money(code)
    score_north = min(max(north_flow/10000, 0), 8) * (WEIGHT_NORTH_MONEY / 100)
    avg_turn = df_period["turnover"].mean() if "turnover" in df.columns else 5
    if MIN_TURN_RATE <= avg_turn <= MAX_TURN_RATE:
        turn_score = 10 * (WEIGHT_TURNOVER / 100)
    else:
        turn_score = 0
    total_score = round(score_inc + vol_score + score_zt + score_north + turn_score, 2)
    return total_score

def get_sector_leaders(sector_stock_df, sentiment_info):
    stock_list = []
    for _,row in sector_stock_df.iterrows():
        code = str(row["股票代码"])
        s_name = row["股票名称"]
        if FILTER_ST and "ST" in s_name:
            continue
        if has_bad_news(code):
            continue
        s_score = calc_stock_score(code, s_name)
        if s_score <= 0:
            continue
        moat_info = detect_moat(code, s_name)
        k_data = get_stock_kline(code)
        if k_data is None or len(k_data) < 30:
            continue
        tech_info = custom_trade_model(k_data)
        stock_list.append({
            "code":code,
            "name":s_name,
            "rank_score":s_score,
            "moat_info": moat_info,
            "tech_info": tech_info
        })
    stock_df = pd.DataFrame(stock_list)
    if stock_df.empty:
        return []
    stock_df = stock_df.sort_values("rank_score",ascending=False)
    leaders_raw = stock_df.head(LEADER_NUM).to_dict("records")
    final_leaders = []
    for idx, item in enumerate(leaders_raw):
        trade_strategy = build_trading_strategy(
            sentiment_score=sentiment_info["sentiment_score"],
            sentiment_level=sentiment_info["sentiment_level"],
            leader_rank=idx+1,
            moat_info=item["moat_info"],
            tech_data=item["tech_info"]
        )
        item["trade_strategy"] = trade_strategy
        final_leaders.append(item)
    return final_leaders
