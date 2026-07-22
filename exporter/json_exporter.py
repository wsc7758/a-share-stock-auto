import json
import os
from datetime import datetime
from config.constants import OUTPUT_LATEST, OUTPUT_ARCHIVE, JSON_RESULT_NAME

def init_folder():
    os.makedirs(OUTPUT_LATEST, exist_ok=True)
    os.makedirs(OUTPUT_ARCHIVE, exist_ok=True)

def export_json(final_result, sentiment_info):
    init_folder()
    today = datetime.now().strftime("%Y-%m-%d")
    output_data = {
        "run_date": today,
        "market_sentiment": sentiment_info,
        "sector_list": final_result
    }
    # 最新结果
    json_path = os.path.join(OUTPUT_LATEST, JSON_RESULT_NAME)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    # 每日归档
    arch_path = os.path.join(OUTPUT_ARCHIVE, f"{today}_{JSON_RESULT_NAME}")
    with open(arch_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
