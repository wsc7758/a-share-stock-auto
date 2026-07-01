import requests
import concurrent.futures
import re
from collections import defaultdict
import time
import m3u8

# ===================== 配置区 =====================
SOURCE_FILE = "sources.txt"
WHITE_LIST_FILE = "channel_whitelist.txt"
OUTPUT_TXT = "tv.txt"
TEST_TIMEOUT = 2.0
MIN_VIDEO_HEIGHT = 720  # 低于720p直接剔除
# 内网黑名单，过滤局域网不可外网播放地址
BLACK_HOST = {"127.", "192.", "10.", "172.", "localhost", ":8801", ":808"}
# 并发控制
SOURCE_CHECK_WORKER = 8
CHANNEL_TEST_WORKER = 12

# ===================== 工具函数 =====================
def is_private_url(url: str) -> bool:
    """判断是否内网地址"""
    for seg in BLACK_HOST:
        if seg in url:
            return True
    return False

def check_source_alive(url: str) -> tuple[bool, str]:
    """需求1：预检测源地址是否有效，失效剔除"""
    headers = {"User-Agent": "Mozilla/5.0 Android TV"}
    try:
        resp = requests.get(url, headers=headers, timeout=3)
        resp.raise_for_status()
        text = resp.text.strip()
        if len(text) < 20:
            return False, url
        return True, url
    except Exception:
        return False, url

def load_source_priority() -> list[str]:
    """读取sources，区分（快）优先源，返回源名称+url配对"""
    fast_group = []
    normal_group = []
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith("#")]
    for line in lines:
        if "：" not in line:
            normal_group.append(("", line))
            continue
        name, url = line.split("：", 1)
        name = name.strip()
        url = url.strip()
        if "（快）" in name:
            fast_group.append((name, url))
        else:
            normal_group.append((name, url))
    # 快源放前面
    all_source_pairs = fast_group + normal_group
    source_urls = [u for _, u in all_source_pairs]
    print(f"总计待检测源数量：{len(source_urls)}（高速源{len(fast_group)}个）")

    # 需求1：批量检测线路有效性，剔除失效源
    valid_urls = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SOURCE_CHECK_WORKER) as exe:
        results = exe.map(check_source_alive, source_urls)
    alive_count = 0
    dead_count = 0
    for ok, url in results:
        if ok:
            valid_urls.append(url)
            alive_count += 1
        else:
            dead_count += 1
    print(f"源检测完成：有效{alive_count}条，失效剔除{dead_count}条")
    return valid_urls

def fetch_all_channel_from_source(source_url: str) -> list[tuple[str, str]]:
    """拉取单个源内所有频道名称+链接"""
    channels = []
    headers = {"User-Agent": "Mozilla/5.0 Android TV"}
    try:
        resp = requests.get(source_url, headers=headers, timeout=4)
        resp.encoding = resp.apparent_encoding
        text = resp.text
        # 标准 txt 格式 名称,url
        txt_reg = re.compile(r"([^,]+),(http[s]?://[^\n]+)")
        for ch_name, link in txt_reg.findall(text):
            ch_name = ch_name.strip().replace("#genre#", "").strip()
            link = link.strip()
            if ch_name and link and not is_private_url(link):
                channels.append((ch_name, link))
        # m3u8 格式
        m3u8_reg = re.compile(r"#EXTINF:-1,([^\n]+)\n(http[s]?://[^\n]+)")
        for ch_name, link in m3u8_reg.findall(text):
            ch_name = ch_name.strip()
            link = link.strip()
            if ch_name and link and not is_private_url(link):
                channels.append((ch_name, link))
    except Exception:
        pass
    return channels

def load_white_list() -> set[str]:
    """加载白名单频道集合，只处理这些频道"""
    white_set = set()
    with open(WHITE_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f.readlines():
            line = line.strip()
            if line and not line.startswith("#"):
                white_set.add(line)
    print(f"白名单待处理频道总数：{len(white_set)}")
    return white_set

def get_stream_resolution(stream_url: str) -> int:
    """获取流最大垂直分辨率，失败返回0"""
    headers = {"User-Agent": "Mozilla/5.0 Android TV"}
    try:
        resp = requests.get(stream_url, headers=headers, timeout=2)
        pl = m3u8.loads(resp.text)
        max_h = 0
        if pl.is_variant:
            for sub_pl in pl.playlists:
                if hasattr(sub_pl.stream_info, "resolution") and sub_pl.stream_info.resolution:
                    _, h = sub_pl.stream_info.resolution.split("x")
                    h = int(h)
                    if h > max_h:
                        max_h = h
        else:
            # 无分片信息默认标记720
            max_h = 720
        return max_h
    except Exception:
        return 0

def test_stream_delay(url: str) -> float:
    """测速，返回延迟秒，超时返回极大值"""
    headers = {"User-Agent": "Mozilla/5.0 Android TV"}
    start = time.time()
    try:
        r = requests.get(url, headers=headers, timeout=TEST_TIMEOUT, stream=True)
        r.raw.read(512)
        delay = round(time.time() - start, 3)
        return delay
    except Exception:
        return 9999.0

def main():
    # 步骤1：加载并校验所有源，剔除失效线路
    valid_source_urls = load_source_priority()
    if not valid_source_urls:
        print("无有效直播源，程序退出")
        return

    # 步骤2：拉取所有源频道，仅保留白名单内节目
    white_channels = load_white_list()
    ch_link_map = defaultdict(list)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as exe:
        all_source_channels = exe.map(fetch_all_channel_from_source, valid_source_urls)

    for ch_list in all_source_channels:
        for ch_name, link in ch_list:
            if ch_name in white_channels:
                ch_link_map[ch_name].append(link)
    print(f"白名单频道匹配到线路总量：sum([len(v) for v in ch_link_map.values()])")

    # 步骤3：对匹配到的白名单节目统一测速、测分辨率，过滤<720P
    final_channel_data = []
    for ch_name, url_list in ch_link_map.items():
        temp_store = []
        # 并发测速+分辨率
        with concurrent.futures.ThreadPoolExecutor(max_workers=CHANNEL_TEST_WORKER) as exe:
            delay_res = list(exe.map(test_stream_delay, url_list))
            res_res = list(exe.map(get_stream_resolution, url_list))
        for link, delay, height in zip(url_list, delay_res, res_res):
            # 需求3：剔除低于720P源
            if height < MIN_VIDEO_HEIGHT:
                continue
            temp_store.append({
                "url": link,
                "delay": delay,
                "height": height
            })
        if not temp_store:
            continue

        # 先按延迟升序（速度从快到慢），再按分辨率降序（画质优优先）
        temp_store.sort(key=lambda x: (x["delay"], -x["height"]))
        # 需求4：全部保留，不限3条
        for item in temp_store:
            final_channel_data.append(f"{ch_name},{item['url']}")

    # 写入输出文件
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(final_channel_data))
    print(f"处理完成，输出有效线路总数：{len(final_channel_data)}")

if __name__ == "__main__":
    main()
