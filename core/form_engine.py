"""
移植算法来源：myhhub/stock InStock
模块：K线形态识别、滑动窗口高低点、简易筹码测算
纯计算函数，无数据库、无web依赖
入参DataFrame固定字段：date, open, high, low, close, volume
"""
import pandas as pd
import numpy as np

def get_local_high_low(df: pd.DataFrame, window: int = 60):
    df = df.copy().reset_index(drop=True)
    high_arr = df["high"].values
    low_arr = df["low"].values
    high_points = []
    low_points = []

    half_win = window // 2
    for i in range(half_win, len(df) - half_win):
        left = i - half_win
        right = i + half_win + 1
        local_max = np.max(high_arr[left:right])
        local_min = np.min(low_arr[left:right])
        if high_arr[i] == local_max:
            high_points.append(float(high_arr[i]))
        if low_arr[i] == local_min:
            low_points.append(float(low_arr[i]))

    valid_highs = sorted(list(set(high_points)), reverse=True)
    valid_lows = sorted(list(set(low_points)))
    effect_high = valid_highs[0] if len(valid_highs) else round(df["high"].max(), 2)
    effect_low = valid_lows[0] if len(valid_lows) else round(df["low"].min(), 2)

    return {
        "high_list": valid_highs,
        "low_list": valid_lows,
        "recent_effect_high": effect_high,
        "recent_effect_low": effect_low
    }

def detect_n_pattern(df: pd.DataFrame, amplitude_threshold=0.12):
    df = df.copy().tail(65).reset_index(drop=True)
    price = df["close"].values
    if len(price) < 30:
        return False, "❌K线数量不足，无法识别N字结构"

    segment_peak1 = np.argmax(price[:int(len(price)*0.55)])
    segment_trough = segment_peak1 + np.argmin(price[segment_peak1:])
    peak2 = segment_trough + np.argmax(price[segment_trough:])

    p1 = price[segment_peak1]
    p_mid = price[segment_trough]
    p2 = price[peak2]

    drawdown = (p1 - p_mid) / p1
    rebound_ratio = p2 / p1

    if drawdown >= amplitude_threshold and 0.90 <= rebound_ratio <= 1.18:
        return True, f"✅识别N字结构，第一峰{p1:.2f}，回调低点{p_mid:.2f}，二次高点{p2:.2f}"
    return False, "❌未形成标准N字形态"

def detect_box_breakout(df: pd.DataFrame, box_window=45):
    df = df.copy().reset_index(drop=True)
    box_df = df.tail(box_window)
    box_top = box_df["high"].quantile(0.85)
    box_bottom = box_df["low"].quantile(0.15)
    latest_close = df.iloc[-1]["close"]

    if latest_close > box_top * 1.003:
        return True, round(box_top,2), round(box_bottom,2), f"✅放量向上突破箱体，箱体上沿{box_top:.2f}，箱底{box_bottom:.2f}"
    elif latest_close < box_bottom * 0.997:
        return False, round(box_top,2), round(box_bottom,2), f"❌向下跌破箱体支撑，箱体区间【{box_bottom:.2f} - {box_top:.2f}】"
    else:
        return False, round(box_top,2), round(box_bottom,2), f"⏸处于箱体震荡内部，区间【{box_bottom:.2f} - {box_top:.2f}】"

def calc_simple_chip_distribution(df: pd.DataFrame):
    df = df.copy()
    vol = df["volume"].values
    close = df["close"].values
    total_vol = vol.sum()
    if total_vol <= 0:
        return {"chip_dense_low": 0.0, "chip_dense_high": 0.0, "cost_avg": 0.0}

    avg_cost = np.sum(close * vol) / total_vol
    return {
        "chip_dense_low": round(avg_cost * 0.94, 2),
        "chip_dense_high": round(avg_cost * 1.06, 2),
        "cost_avg": round(avg_cost, 2)
    }
