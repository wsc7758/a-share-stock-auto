import os
from datetime import datetime
from config.constants import OUTPUT_LATEST, OUTPUT_ARCHIVE, MD_RESULT_NAME, DISCLAIMER_TEXT

def init_folder():
    os.makedirs(OUTPUT_LATEST, exist_ok=True)
    os.makedirs(OUTPUT_ARCHIVE, exist_ok=True)

def export_md(final_result, sentiment_info):
    init_folder()
    today = datetime.now().strftime("%Y-%m-%d")
    md_lines = [DISCLAIMER_TEXT]
    md_lines.extend([
        f"# A股热点板块与龙头分析 {today}",
        "## 📊 当前全市场情绪状态",
        f"- 情绪得分：{sentiment_info['sentiment_score']}/100",
        f"- 情绪等级：{sentiment_info['sentiment_level']}",
        f"- 操作提示：{sentiment_info['tips']}",
        "\n---"
    ])
    for sector_info in final_result:
        md_lines.append(f"\n## 热点板块：{sector_info['sector_name']} 综合得分：{sector_info['total_score']}")
        leaders = sector_info["leaders"]
        if not leaders:
            md_lines.append("> 板块内无满足全部筛选条件标的")
            continue
        for idx, stock in enumerate(leaders,1):
            moat = stock["moat_info"]
            tech = stock["tech_info"]
            trade_str = stock["trade_strategy"]
            form_msg = tech["extra_form_msg"]
            md_lines.append(f"\n### 龙{idx}：{stock['name']}({stock['code']})")
            md_lines.append(f"> 【护城河提示】{moat['moat_tip']}")
            md_lines.append(f"> 识别依据：{moat['evidence']}")
            md_lines.append(f"\n📊【技术形态分析】")
            md_lines.append(f"{form_msg['n_pattern']['desc']}")
            md_lines.append(f"{form_msg['box']['desc']}")
            chip = form_msg["chip"]
            if chip.get("cost_avg",0) > 0:
                md_lines.append(f"筹码密集区间：{chip['chip_dense_low']} ~ {chip['chip_dense_high']}，市场平均持仓成本：{chip['cost_avg']}")
            md_lines.append(f"\n现价：{tech['now_price']}")
            md_lines.append(f"短期支撑：{tech['support_short']} | 中期支撑：{tech['support_mid']}")
            md_lines.append(f"短期压力：{tech['resistance_short']}")
            md_lines.append(f"买点信号：{'✅ 存在潜在买点' if tech['buy_signal'] else '❌ 暂无买点'}")
            md_lines.append(f"卖点信号：{'⚠️ 接近压力位' if tech['sell_signal'] else '🟢 暂时安全区间'}")
            md_lines.append(f"\n📋【标准化交易策略】")
            md_lines.append(f"{trade_str['full_text']}")
    md_content = "\n".join(md_lines)
    out_path = os.path.join(OUTPUT_LATEST, MD_RESULT_NAME)
    with open(out_path,"w",encoding="utf-8") as f:
        f.write(md_content)
    archive_path = os.path.join(OUTPUT_ARCHIVE,f"{today}_{MD_RESULT_NAME}")
    with open(archive_path,"w",encoding="utf-8") as f:
        f.write(md_content)
