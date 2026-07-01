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

# 读取白名单频道（只保留模板里的央视/卫视）
def load_white_list():
    white_channels = set()
    with open(WHITE_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f.readlines():
            line = line.strip()
            # 跳过注释行 #开头
            if not line or line.startswith("#"):
                continue
            white_channels.add(line)
    return white_channels

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
        # 标准txt匹配
        txt_pattern = re.compile(r"([^,]+),(http[^\n]+)")
        txt_matches = txt_pattern.findall(text)
        for name, link in txt_matches:
            name = name.strip().replace("#genre#","").strip()
            if name and link:
                channels.append((name, link.strip()))
        # m3u匹配
        m3u_pattern = re.compile(r"#EXTINF:-1,([^\n]+)\n(http[^\n]+)")
        m3u_matches = m3u_pattern.findall(text)
        for name, link in m3u_matches:
            name = name.strip()
            if name and link:
                channels.append((name, link.strip()))
    except Exception as e:
        pass
    return channels

# 测速链接
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
    # 加载白名单
    white_set = load_white_list()
    all_channels = []
    source_urls = load_source_urls()

    # 多线程拉取全部源
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        res_list = executor.map(fetch_iptv, source_urls)
        for res in res_list:
            all_channels.extend(res)

    # 按频道分组
    group = defaultdict(list)
    for name,url in all_channels:
        group[name].append(url)

    # 最终输出列表
    final_output = []
    # 固定分组标题模板
    final_output.append("央视频道,#genre#")
    # 先输出所有白名单内央视
    for ch_name in white_set:
        if ch_name.startswith("CCTV"):
            if ch_name not in group:
                continue
            urls = group[ch_name]
            test_map = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                speed_res = executor.map(test_speed, urls)
                for u,s in zip(urls, speed_res):
                    test_map[u] = s
            # 过滤超时，按速度排序
            valid_urls = sorted([u for u in test_map if test_map[u] < TEST_TIMEOUT], key=lambda x:test_map[x])
            top_urls = valid_urls[:MAX_BEST_PER_CHANNEL]
            for u in top_urls:
                final_output.append(f"{ch_name},{u}")

    final_output.append("\n卫视频道,#genre#")
    # 输出卫视（非CCTV白名单频道）
    for ch_name in white_set:
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

    # 写入最终tv.txt（严格按模板分组）
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(final_output))
    print(f"过滤完成，仅保留央视+卫视，有效线路总数：{len(final_output)}")

if __name__ == "__main__":
    main()
