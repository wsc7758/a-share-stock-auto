import akshare as ak
import pandas as pd
from data.datasource import safe_request

def get_market_sentiment_data():
    result = {}
    limit_df = safe_request(ak.stock_zt_pool_em)
    if limit_df is not None and not limit_df.empty:
        today_zt = len(limit_df[limit_df["涨跌幅"] >= 9.8])
        today_dt = len(limit_df[limit_df["涨跌幅"] <= -9.8])
        result["zt_count"] = today_zt
        result["dt_count"] = today_dt
    else:
        result["zt_count"] = 0
        result["dt_count"] = 0

    stock_change_df = safe_request(ak.stock_zh_a_spot_em)
    if stock_change_df is not None and not stock_change_df.empty:
        rise_cnt = len(stock_change_df[stock_change_df["涨跌幅"] > 0])
        fall_cnt = len(stock_change_df[stock_change_df["涨跌幅"] < 0])
        result["rise_num"] = rise_cnt
        result["fall_num"] = fall_cnt
    else:
        result["rise_num"] = 2000
        result["fall_num"] = 2000

    north_df = safe_request(ak.stock_hsgt_fund_flow_summary_em)
    north_money = 0
    if north_df is not None and not north_df.empty:
        try:
            north_money = float(north_df[north_df["名称"]=="北向资金"]["净流入-亿"].values[0])
        except:
            pass
    result["north_money"] = north_money

    market_total_df = safe_request(ak.stock_market_activity_legu)
    total_amount = 0
    if market_total_df is not None:
        try:
            total_amount = float(market_total_df.iloc[-1]["成交额"])
        except:
            pass
    result["total_amount"] = total_amount
    return result

def calc_sentiment_score() -> dict:
    data = get_market_sentiment_data()
    score = 0
    zt = data["zt_count"]
    dt = data["dt_count"]
    if zt > dt * 3:
        score += 35
    elif zt > dt:
        score += 20
    elif dt > zt * 2:
        score += 0
    else:
        score += 10

    rise = data["rise_num"]
    fall = data["fall_num"]
    if rise > fall * 1.8:
        score +=25
    elif rise > fall:
        score +=15
    elif fall > rise*1.8:
        score +=0
    else:
        score +=8

    north = data["north_money"]
    if north > 30:
        score +=20
    elif north >0:
        score +=10
    elif north < -30:
        score +=0
    else:
        score +=5

    amount = data["total_amount"]
    if amount > 8500:
        score +=20
    elif amount >6000:
        score +=12
    elif amount <4500:
        score +=0
    else:
        score +=6

    score = min(max(score,0),100)

    if score <=30:
        level = "情绪冰点"
        factor = 0.65
        tip = "【冰点行情】题材溢价低，尽量只参与板块绝对龙头，规避后排跟风，严控仓位，谨慎开新仓"
    elif score <=55:
        level = "震荡修复"
        factor = 0.85
        tip = "【震荡修复】分化行情，优选多重热点叠加、有护城河的核心标的，不追高"
    elif score <=80:
        level = "温和活跃"
        factor = 1.05
        tip = "【温和活跃】市场赚钱效应较好，主线题材持续性较强，可关注龙一龙二机会"
    else:
        level = "情绪高潮"
        factor = 1.15
        tip = "【情绪高潮】警惕短期兑现风险，不新开高位追涨，持仓标的逢压力位分批减仓"

    return {
        "sentiment_score": score,
        "sentiment_level": level,
        "factor": factor,
        "tips": tip
    }
