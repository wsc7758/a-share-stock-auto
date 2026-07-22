def build_trading_strategy(
    sentiment_score: int,
    sentiment_level: str,
    leader_rank: int,
    moat_info: dict,
    tech_data: dict
) -> dict:
    now_price = tech_data["now_price"]
    sup_short = tech_data["support_short"]
    sup_mid = tech_data["support_mid"]
    res_short = tech_data["resistance_short"]
    buy_signal = tech_data["buy_signal"]
    sell_signal = tech_data["sell_signal"]
    has_moat = moat_info["has_moat"]

    strategy = {
        "position_suggest": "",
        "entry_range": "",
        "stop_loss_price": 0.0,
        "take_profit_target": [],
        "hold_cycle": "",
        "risk_warn": "",
        "full_text": ""
    }

    total_pos_rate = 0.0
    if sentiment_score <= 30:
        if leader_rank == 1 and has_moat:
            total_pos_rate = 0.20
            strategy["position_suggest"] = "市场情绪冰点，仅小仓试探，单只最大占用总资金20%"
        elif leader_rank == 1:
            total_pos_rate = 0.15
            strategy["position_suggest"] = "市场情绪冰点，题材博弈风险高，单只资金控制15%以内"
        else:
            total_pos_rate = 0.0
            strategy["position_suggest"] = "情绪冰点，龙二/龙三后排标的不建议新开仓，保持观望"
    elif 31 <= sentiment_score <= 55:
        if leader_rank == 1:
            total_pos_rate = 0.30
            strategy["position_suggest"] = "震荡分化行情，单只仓位不超过总资金30%"
        elif leader_rank == 2:
            total_pos_rate = 0.20
            strategy["position_suggest"] = "震荡行情，龙二跟风标的，单只仓位控制20%"
        else:
            total_pos_rate = 0.10
            strategy["position_suggest"] = "震荡环境龙三风险偏高，仅极小仓博弈"
    elif 56 <= sentiment_score <= 80:
        if leader_rank == 1:
            total_pos_rate = 0.45
            strategy["position_suggest"] = "市场赚钱效应良好，单只最大仓位45%"
        elif leader_rank == 2:
            total_pos_rate = 0.35
            strategy["position_suggest"] = "温和行情，龙二标的单只仓位35%"
        else:
            total_pos_rate = 0.25
            strategy["position_suggest"] = "龙三后排，仓位控制25%以内"
    else:
        total_pos_rate = 0.0
        strategy["position_suggest"] = "市场情绪高潮，严禁追高新开仓；已有持仓逢压力分批止盈"

    if buy_signal and total_pos_rate > 0:
        entry_low = round(sup_short * 1.00, 2)
        entry_high = round(sup_short * 1.035, 2)
        strategy["entry_range"] = f"【数据推演参考区间，不构成入场指导】{entry_low} ~ {entry_high}，依托短期支撑低吸，禁止追高"
    else:
        strategy["entry_range"] = "暂无符合模型买点，等待回踩支撑或者形态修复，不要主动入场"

    if has_moat:
        stop_loss = round(sup_mid * 0.97, 2)
    else:
        stop_loss = round(sup_short * 0.96, 2)
    strategy["stop_loss_price"] = stop_loss

    tp1 = round(res_short * 0.98, 2)
    tp2 = round(res_short * 1.06, 2)
    strategy["take_profit_target"] = [tp1, tp2]

    if has_moat:
        strategy["hold_cycle"] = "可波段观察 10~25 个交易日，基本面优质标的允许适度格局"
    else:
        strategy["hold_cycle"] = "题材博弈为主，建议5~12个交易日内完成交易，不长期恋战"

    risk_lines = []
    if sentiment_score <= 30:
        risk_lines.append("市场处于情绪冰点，题材持续性较差，警惕一日游行情")
    if sentiment_score >= 81:
        risk_lines.append("情绪高位谨防集体兑现，禁止追加仓位")
    if leader_rank >= 3:
        risk_lines.append("板块后排标的，资金优先回流龙头，波动更大")
    if not has_moat:
        risk_lines.append("无识别护城河，纯题材驱动，基本面缺少长期支撑")
    if sell_signal:
        risk_lines.append("价格接近短期压力位置，存在短期回调风险")

    if risk_lines:
        strategy["risk_warn"] = "⚠️风险提示：" + "；".join(risk_lines)
    else:
        strategy["risk_warn"] = "当前结构相对健康，依旧严格执行风控纪律"

    full = []
    full.append(f"【仓位策略】{strategy['position_suggest']}")
    full.append(f"【入场参考】{strategy['entry_range']}")
    full.append(f"【模型测算风控参考价位】{strategy['stop_loss_price']}")
    full.append(f"【分批止盈参考】第一目标 {tp1}，第二目标 {tp2}")
    full.append(f"【预期持仓周期】{strategy['hold_cycle']}")
    full.append(f"{strategy['risk_warn']}")
    strategy["full_text"] = "\n".join(full)
    return strategy
