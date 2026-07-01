import requests
import concurrent.futures
import re
from collections import defaultdict
import time

# 配置文件路径
SOURCE_FILE = "sources.txt"
WHITE_LIST_FILE = "channel_whitelist.txt"
OUTPUT_TXT = "tv.txt"
TEST_TIMEOUT = 3
MAX_BEST_PER_CHANNEL = 3

# 读取白名单频道【有序列表，保留写入顺序，不打乱】
def load_white_list_ordered():
    white_channels_order = []
    with open(WHITE_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f.readlines():
            line = line.strip()
            # 跳过注释 #开头、空行
            if not line or line.startswith("#"):
                continue
            white_channels_order.append(line)
    return white_channels_order

# 读取所有直播源链接
def load_source_urls():
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        lines = [i.strip() for i in f.readlines() if i.strip()]
    return lines

# 拉取txt/m3u源，提取频道+链接
def fetch_iptv(url):
    channels = []
    try:
        headers = {"User-Agent":"Mozilla/5.0 Android TV"}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = resp.apparent_encoding
        text = resp.text
        # 标准txt匹配：频道名,url
        txt_pattern = re.compile(r"([^,]+),(http[^\n]+)")
        txt_matches = txt_pattern.findall(text)
        for name, link in txt_matches:
            name = name.strip().replace("#genre#","").strip()
            if name and link:
                channels.append((name, link.strip()))
        # m3u格式匹配
        m3u_pattern = re.compile(r"#EXTINF:-1,([^\n]+)\n(http[^\n]+)")
        m3u_matches = m3u_pattern.findall(text)
        for name, link in m3u_matches:
            name = name.strip()
            if name and link:
                channels.append((name, link.strip()))
    except Exception as e:
        pass
    return channels

# 测速单条播放链接
def test_speed(url):
    start = time.time()
    try:
        headers = {"User-Agent":"Mozilla/5.0 Android TV"}
        r = requests.head(url, headers=headers, timeout=TEST_TIMEOUT, allow_redirects=True)
        cost = time.time() - start
        return round(cost,3)
    except:
        return 9999

def main():
    # 读取【有序】白名单（关键改动：不用set，用list保存原始顺序）
    white_order_list = load_white_list_ordered()
    all_channels = []
    source_urls = load_source_urls()

    # 多线程拉取全部外部直播源
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        res_list = executor.map(fetch_iptv, source_urls)
        for res in res_list:
            all_channels.extend(res)

    # 按频道名分组存储所有线路
    group = defaultdict(list)
    for name,url in all_channels:
        group[name].append(url)

    final_output = []
    # 固定分组标题
    final_output.append("央视频道,#genre#")

    # 1. 先遍历白名单里所有CCTV频道，严格按白名单先后输出
    for ch_name in white_order_list:
        if ch_name.startswith("CCTV"):
            if ch_name not in group:
                continue
            urls = group[ch_name]
            test_map = {}
            # 多线程测速该频道全部线路
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                speed_res = executor.map(test_speed, urls)
                for u,s in zip(urls, speed_res):
                    test_map[u] = s
            # 过滤超时失效源，按延迟从小到大排序
            valid_urls = sorted([u for u in test_map if test_map[u] < TEST_TIMEOUT], key=lambda x:test_map[x])
            top_urls = valid_urls[:MAX_BEST_PER_CHANNEL]
            # 写入输出列表
            for u in top_urls:
                final_output.append(f"{ch_name},{u}")

    # 分割卫视分组
    final_output.append("\n卫视频道,#genre#")

    # 2. 再遍历白名单里所有卫视频道，严格按白名单先后输出
    for ch_name in white_order_list:
        if not ch_name.startswith("CCTV"):
            if ch_name not in group:
                continue
            urls = group[ch_name]
            test_map = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                speed_res = executor.map(test_speed, urls)
                for u,s in zip(urls, speed_res):
                    test_map[u] = s
            valid_urls = sorted([u for u in test_map if test_map[u] < TEST_TIMEOUT], key=lambda x:test_map[x])
            top_urls = valid_urls[:MAX_BEST_PER_CHANNEL]
            for u in top_urls:
                final_output.append(f"{ch_name},{u}")

    # 写入tv.txt，完全保持白名单原始顺序
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(final_output))
    print(f"过滤完成，仅保留央视+卫视，有效线路总数：{len(final_output)}")

if __name__ == "__main__":
    main()
