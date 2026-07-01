import requests
import concurrent.futures
import re
from collections import defaultdict
import time
from urllib.parse import urlparse

# 配置
SOURCE_FILE = "sources.txt"
WHITE_LIST_FILE = "channel_whitelist.txt"
OUTPUT_TXT = "tv.txt"
TEST_TIMEOUT = 2.5   # 缩短超时，慢源直接丢弃
MAX_BEST_PER_CHANNEL = 3
# 内网/局域网黑名单，直接过滤
BLACK_HOST = {"127.", "192.", "10.", "172.", "localhost", ":8801", ":808"}

# 有序读取白名单
def load_white_list_ordered():
    white_channels_order = []
    with open(WHITE_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            white_channels_order.append(line)
    return white_channels_order

# 读取源列表
def load_source_urls():
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        lines = [i.strip() for i in f.readlines() if i.strip()]
    return lines

# 过滤内网/端口垃圾源
def filter_private_url(url):
    for black in BLACK_HOST:
        if black in url:
            return False
    return True

# 拉取txt/m3u源
def fetch_iptv(url):
    channels = []
    try:
        headers = {"User-Agent":"Mozilla/5.0 Android TV"}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = resp.apparent_encoding
        text = resp.text
        # txt格式
        txt_pattern = re.compile(r"([^,]+),(http[^\n]+)")
        txt_matches = txt_pattern.findall(text)
        for name, link in txt_matches:
            name = name.strip().replace("#genre#","").strip()
            link = link.strip()
            if name and link and filter_private_url(link):
                channels.append((name, link))
        # m3u格式
        m3u_pattern = re.compile(r"#EXTINF:-1,([^\n]+)\n(http[^\n]+)")
        m3u_matches = m3u_pattern.findall(text)
        for name, link in m3u_matches:
            name = name.strip()
            link = link.strip()
            if name and link and filter_private_url(link):
                channels.append((name, link))
    except Exception:
        pass
    return channels

# 真实分片测速（替代HEAD，准确度大幅提升）
def test_real_speed(url):
    start = time.time()
    try:
        headers = {"User-Agent":"Mozilla/5.0 Android TV"}
        # 只请求前1KB分片，模拟播放加载
        r = requests.get(url, headers=headers, timeout=TEST_TIMEOUT, stream=True, allow_redirects=True)
        r.raw.read(1024)
        cost = round(time.time() - start, 3)
        return cost
    except Exception:
        return 9999

def main():
    white_order_list = load_white_list_ordered()
    source_urls = load_source_urls()
    all_channels = []

    # 拉取所有源
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        res_list = executor.map(fetch_iptv, source_urls)
        for res in res_list:
            all_channels.extend(res)

    # 频道分组 + 同域名去重
    group = defaultdict(list)
    domain_set = defaultdict(set)
    for name,url in all_channels:
        dom = urlparse(url).netloc
        if dom not in domain_set[name]:
            domain_set[name].add(dom)
            group[name].append(url)

    final_output = []
    final_output.append("央视频道,#genre#")

    # 按白名单顺序输出CCTV
    for ch_name in white_order_list:
        if ch_name.startswith("CCTV") and ch_name in group:
            urls = group[ch_name]
            test_map = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                speed_res = executor.map(test_real_speed, urls)
                for u,s in zip(urls, speed_res):
                    test_map[u] = s
            # 只保留超时内线路，按延迟升序
            valid = sorted([u for u in test_map if test_map[u] < TEST_TIMEOUT], key=lambda x:test_map[x])
            top = valid[:MAX_BEST_PER_CHANNEL]
            for u in top:
                final_output.append(f"{ch_name},{u}")

    final_output.append("\n卫视频道,#genre#")
    # 按白名单顺序输出卫视
    for ch_name in white_order_list:
        if not ch_name.startswith("CCTV") and ch_name in group:
            urls = group[ch_name]
            test_map = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                speed_res = executor.map(test_real_speed, urls)
                for u,s in zip(urls, speed_res):
                    test_map[u] = s
            valid = sorted([u for u in test_map if test_map[u] < TEST_TIMEOUT], key=lambda x:test_map[x])
            top = valid[:MAX_BEST_PER_CHANNEL]
            for u in top:
                final_output.append(f"{ch_name},{u}")

    # 写入文件
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(final_output))
    print(f"优化完成，有效优质线路：{len(final_output)}")

if __name__ == "__main__":
    main()
