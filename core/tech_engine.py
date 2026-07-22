import pandas as pd
from config.settings import SHORT_MA, MID_MA, N_AMPLITUDE_THRESHOLD, BOX_WINDOW
from config.switch import ENABLE_FORM_ENGINE
from core.form_engine import get_local_high_low, detect_n_pattern, detect_box_breakout, calc_simple_chip_distribution

def custom_trade_model(df):
    df = df.copy()
    df["ma20"] = df["close"].rolling(SHORT_MA).mean()
    df["ma60"] = df["close"].rolling(MID_MA).mean()
    last = df.iloc[-1]

    buy = False
    sell = False
    vol_5 = df["volume"].tail(5).mean()
    base_buy_condition = (last["close"] > last["ma20"]) and (last["volume"] > vol_5)

    extra_form_msg = {
        "n_pattern": {"valid": False, "desc": ""},
        "box": {"valid": False, "desc": ""},
        "chip": {}
    }

    support_short = 0.0
    support_mid = 0.0
    resist_short = 0.0

    if ENABLE_FORM_ENGINE:
        # 形态识别
        n_flag, n_desc = detect_n_pattern(df, amplitude_threshold=N_AMPLITUDE_THRESHOLD)
        box_flag, _, _, box_desc = detect_box_breakout(df, box_window=BOX_WINDOW)
        chip_data = calc_simple_chip_distribution(df)
        hl_data = get_local_high_low(df, window=60)

        extra_form_msg["n_pattern"]["valid"] = n_flag
        extra_form_msg["n_pattern"]["desc"] = n_desc
        extra_form_msg["box"]["valid"] = box_flag
        extra_form_msg["box"]["desc"] = box_desc
        extra_form_msg["chip"] = chip_data

        support_short = round(max(hl_data["recent_effect_low"], last["ma20"]), 2)
        support_mid = round(last["ma60"], 2)
        resist_short = hl_data["recent_effect_high"]

        if base_buy_condition and (n_flag or box_flag):
            buy = True
        if last["close"] >= resist_short * 0.96:
            sell = True
    else:
        # 原始简易算法兜底
        df_60 = df.tail(60)
        recent_high = df_60["high"].max()
        recent_low = df_60["low"].min()
        support_short = round(max(recent_low, last["ma20"]), 2)
        support_mid = round(last["ma60"], 2)
        resist_short = round(recent_high, 2)
        if base_buy_condition:
            buy = True
        if last["close"] >= resist_short * 0.96:
            sell = True

    tech_result = {
        "now_price": round(last["close"],2),
        "support_short": support_short,
        "support_mid": support_mid,
        "resistance_short": resist_short,
        "buy_signal": buy,
        "sell_signal": sell,
        "extra_form_msg": extra_form_msg
    }
    return tech_result
