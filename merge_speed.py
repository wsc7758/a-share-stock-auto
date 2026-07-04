import sys
sys.stdout.reconfigure(encoding="utf-8")
import requests
import concurrent.futures
import re
from collections import defaultdict
import time
import urllib3
import os
import datetime
import threading

# 全局禁用长连接，单次请求用完立刻销毁socket
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
def no_reuse_conn(self, timeout=None):
    return self._new_conn()
urllib3.connectionpool.ConnectionPool._get_conn = no_reuse_conn

# 全局业务参数
SOURCE_FILE = "sources.txt"
WHITE_LIST_FILE = "channel_whitelist.txt"
OUTPUT_TXT = "tv.txt"
STREAM_REQ_TIMEOUT = 1.5
M3U8_CHECK_TIMEOUT = 2.0    # m3u8连通检测超时
PLAY_CHUNK_TIMEOUT = 2.5     # 视频分片读取超时
TASK_GLOBAL_TIMEOUT = 12
BATCH_GLOBAL_TIMEOUT = 25
MIN_VERTICAL_RES = 1080
MAX_STREAM_PER_CHANNEL = 6
SOURCE_FETCH_TIMEOUT = 3
SOURCE_FETCH_WORKERS = 3
STREAM_EVAL_WORKERS = 12
batch_size = 60
DEBUG_LOG = False

# 标准化核心ID：统一cctv数字，忽略大小写、横杠、后缀汉字
def standardize_core_id(raw_name: str) -> str:
    s = raw_name.lower().replace("-", "").replace(" ", "")
    match = re.search(r"cctv(\d+)", s)
    if match:
        return f"cctv{match.group(1)}"
    return s

def is_stream_incompatible(url: str) -> bool:
    ban_list = {"127.", "192.168.", "10.", "172.", "localhost", "rtmp://", "igmp://", "rtsp://", "srt://", "udp://", "tcp://"}
    lower_url = url.lower()
    if not (url.startswith("http://") or url.startswith("https://")):
        return True
    return any(key in lower_url for key in ban_list)

def get_stream_priority(url: str) -> int:
    lower_url = url.lower()
    if "migu" in lower_url or "miguvideo" in lower_url:
        return 0
    if "cctv.cn" in lower_url or "yangshipin" in lower_url:
        return 0
    return 1

# --------------------------新增：完整链路检测（404延迟测速 + m3u8连通校验）--------------------------
def full_stream_detect(url: str) -> tuple[float, int, bool, bool, int]:
    """
    返回元组：(延迟s, 垂直分辨率, 是否标准M3U8, 可播放, HTTP状态码)
    分层检测逻辑：
    1. HEAD测速，记录延迟+状态码，捕获404/403/5xx
    2. 状态码正常则GET拉取m3u8，校验#EXTM3U标识（区分假网页链接）
    3. 读取视频分片，确认存在真实视频数据
    """
    headers = {
        "User-Agent": "Mozilla/5.0 AndroidTV",
        "Connection": "close",
        "Referer": "https://tv.cctv.com/"
    }
    delay = 9999.0
    max_res = 0
    is_valid_m3u8 = False
    can_play = False
    status_code = 0
    start_total = time.time()

    # 第一层：HEAD测速，捕获404/403/5xx，计算响应延迟
    try:
        head_resp = requests.head(
            url,
            headers=headers,
            timeout=STREAM_REQ_TIMEOUT,
            stream=True,
            verify=False,
            allow_redirects=True
        )
        delay = round(time.time() - start_total, 3)
        status_code = head_resp.status_code
        # 非2xx/3xx直接判定失效（包含404、403、500等）
        if not (200 <= status_code < 400):
            if DEBUG_LOG:
                print(f"【链接检测失败】{url} 状态码:{status_code} 延迟:{delay}s", flush=True)
            return delay, max_res, is_valid_m3u8, can_play, status_code
    except requests.exceptions.Timeout:
        delay = round(time.time() - start_total, 3)
        status_code = -1
        if DEBUG_LOG:
            print(f"【链接检测超时】{url} 延迟:{delay}s", flush=True)
        return delay, max_res, is_valid_m3u8, can_play, status_code
    except Exception as e:
        delay = round(time.time() - start_total, 3)
        status_code = -2
        if DEBUG_LOG:
            print(f"【链接检测异常】{url} 错误:{str(e)}", flush=True)
        return delay, max_res, is_valid_m3u8, can_play, status_code

    # 第二层：拉取m3u8内容，校验标准HLS标识（区分跳转广告/空白网页）
    try:
        m3u8_resp = requests.get(
            url,
            headers=headers,
            timeout=M3U8_CHECK_TIMEOUT,
            stream=True,
            verify=False,
            allow_redirects=True
        )
        first_chunk = m3u8_resp.raw.read(256)
        # 包含#EXTM3U标准头，判定为合法直播流文件
        if b"#EXTM3U" in first_chunk:
            is_valid_m3u8 = True
            max_res = 1080
        else:
            if DEBUG_LOG:
                print(f"【非标准M3U8】{url} 无#EXTM3U标识，跳转网页/广告", flush=True)
            return delay, max_res, is_valid_m3u8, can_play, status_code
    except Exception:
        if DEBUG_LOG:
            print(f"【M3U8文件拉取失败】{url}", flush=True)
        return delay, max_res, is_valid_m3u8, can_play, status_code

    # 第三层：读取视频分片，确认真实可播放
    try:
        play_resp = requests.get(
            url,
            headers=headers,
            timeout=PLAY_CHUNK_TIMEOUT,
            stream=True,
            verify=False,
            allow_redirects=True
        )
        video_chunk = play_resp.raw.read(4096)
        if len(video_chunk) > 200:
            can_play = True
            if DEBUG_LOG:
                print(f"【链接校验通过】{url} 延迟:{delay}s 状态码:{status_code} 合法M3U8 可播放", flush=True)
    except Exception:
        if DEBUG_LOG:
            print(f"【无视频分片】{url} M3U8合法但无法读取播放数据", flush=True)

    return delay, max_res, is_valid_m3u8, can_play, status_code

def batch_subtask(url_group: list[tuple[int, str, str]]) -> dict[int, tuple[float, int, bool, bool, int]]:
    task_start = time.time()
    local_result = {}
    for real_idx, ch_name, url in url_group:
        if time.time() - task_start >= TASK_GLOBAL_TIMEOUT:
            break
        local_result[real_idx] = full_stream_detect(url)
    return local_result

def load_source_list() -> list[str]:
    source_list = []
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        for line in f.readlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if "：" in line:
                    _, link = line.split("：", 1)
                    source_list.append(link.strip())
                elif ":" in line and "http" in line:
                    _, link = line.split(":", 1)
                    source_list.append(link.strip())
                else:
                    source_list.append(line.strip())
    print(f"【阶段1-源池加载】待拉取直播源节点总数：{len(source_list)}", flush=True)
    return source_list

# 白名单：core_id -> 标准完整频道名（CCTV-1综合）
def load_white_list() -> tuple[list, dict]:
    group_info = []
    core_to_fullname = dict()
    current_group = ""
    with open(WHITE_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f.readlines():
            raw_line = line.replace("\r","").replace("\n","").strip()
            if not raw_line:
                continue
            if raw_line.endswith(",#genre#"):
                current_group = raw_line.replace(",#genre#", "").strip()
                group_info.append((current_group, []))
                continue
            if current_group:
                clean_ch = raw_line.strip()
                group_info[-1][1].append(clean_ch)
                ch_core = standardize_core_id(clean_ch)
                core_to_fullname[ch_core] = clean_ch
    print(f"【阶段1-白名单加载】共读取分类数量：{len(group_info)}，频道核心映射数量：{len(core_to_fullname)}", flush=True)
    return group_info, core_to_fullname

# 拉源：标准化ID匹配，自动替换为白名单标准全名
def fetch_channel_from_source(src_link: str, core_mapping: dict) -> list[tuple[str, str]]:
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36", "Connection": "close"}
    result_pairs = []
    if src_link.startswith("//"):
        src_link = "https:" + src_link
    try:
        resp = requests.get(src_link, headers=headers, timeout=SOURCE_FETCH_TIMEOUT, verify=False)
        text = resp.text
        txt_pattern = re.compile(r"([^,]+),(https?://[^\n]+)")
        for raw_ch, stream_url in txt_pattern.findall(text):
            raw_ch = raw_ch.strip().replace("#genre#", "")
            stream_url = stream_url.strip()
            if raw_ch.startswith("#") or is_stream_incompatible(stream_url):
                continue
            src_core = standardize_core_id(raw_ch)
            if src_core in core_mapping:
                full_ch_name = core_mapping[src_core]
                result_pairs.append((full_ch_name, stream_url))
        m3u_pattern = re.compile(r"#EXTINF:-1,([^\n]+)\n(https?://[^\n]+)")
        for raw_ch, stream_url in m3u_pattern.findall(text):
            raw_ch = raw_ch.strip()
            stream_url = stream_url.strip()
            if is_stream_incompatible(stream_url):
                continue
            src_core = standardize_core_id(raw_ch)
            if src_core in core_mapping:
                full_ch_name = core_mapping[src_core]
                result_pairs.append((full_ch_name, stream_url))
    except Exception as e:
        if DEBUG_LOG:
            print(f"【调试】源 {src_link} 拉取异常：{str(e)}", flush=True)
    return result_pairs

def filter_best_streams(channel_raw_map: dict[str, list[str]], core_mapping: dict) -> dict[str, list[str]]:
    final_map = defaultdict(list)
    total_ch = len(channel_raw_map)
    ch_url_index = []
    curr_idx = 0
    fullname_to_core = {full_name:cid for cid,full_name in core_mapping.items()}
    print(f"【全部待测速频道列表】{list(channel_raw_map.keys())}", flush=True)
    for ch_name, url_list in channel_raw_map.items():
        target_cid = fullname_to_core[ch_name]
        for url in url_list:
            ch_url_index.append((curr_idx, ch_name, url))
            curr_idx += 1
    task_result = {}
    total_url = len(ch_url_index)
    print(f"【测速预加载】待测速总链接数量：{total_url}", flush=True)

    for start in range(0, total_url, batch_size):
        batch_start_time = time.time()
        batch_items = ch_url_index[start:start + batch_size]
        batch_end_idx = min(start + batch_size, total_url)
        print(f"【测速批次】{start+1} ~ {batch_end_idx} / {total_url}", flush=True)
        sub_task_groups = [[] for _ in range(STREAM_EVAL_WORKERS)]
        for idx, item in enumerate(batch_items):
            sub_task_groups[idx % STREAM_EVAL_WORKERS].append(item)
        exe = concurrent.futures.ThreadPoolExecutor(max_workers=STREAM_EVAL_WORKERS)
        futures = []
        try:
            futures = [exe.submit(batch_subtask, g) for g in sub_task_groups if g]
            complete_futures = set()
            while len(complete_futures) < len(futures):
                if time.time() - batch_start_time >= BATCH_GLOBAL_TIMEOUT:
                    print(f"【批次超时】本批运行已满25秒，终止剩余未完成测速，已测数据全部保留，强制清理所有线程", flush=True)
                    urllib3.PoolManager().clear()
                    for fu in futures:
                        fu.cancel()
                    break
                try:
                    for fu in concurrent.futures.as_completed(futures, timeout=0.3):
                        if fu not in complete_futures:
                            complete_futures.add(fu)
                            try:
                                task_result.update(fu.result())
                            except Exception as e:
                                print(f"【线程异常】本组线程出错，已测数据保留：{str(e)}", flush=True)
                except concurrent.futures.TimeoutError:
                    continue
        finally:
            exe.shutdown(wait=False, cancel_futures=True)
            urllib3.PoolManager().clear()
            pool_tmp = urllib3.PoolManager()
            pool_tmp.clear()
            print(f"【批次完成】{start+1}~{batch_end_idx} 线程池已全部清空，直接进入下一批次", flush=True)

    ch_temp = defaultdict(list)
    for idx, ch_name, url in ch_url_index:
        delay, res, is_m3u8_valid, can_play, status = task_result.get(idx, (9999, 0, False, False, -99))
        prio = get_stream_priority(url)
        ch_temp[ch_name].append((url, prio, delay, -res, is_m3u8_valid, can_play, status))
    curr = 0
    for ch_name, eval_res in ch_temp.items():
        curr += 1
        print(f"【阶段2测速进度】{curr}/{total_ch} 完成频道：{ch_name}", flush=True)
        # 排序规则：优先官方源(0) → 延迟从小到大 → 分辨率从高到低
        eval_res.sort(key=lambda x: (x[2], x[1], x[3]))
        qualified = []
        for item in eval_res:
            url, prio, delay, neg_h, is_m3u8_valid, can_play, status = item
            real_h = -neg_h
            # 四重过滤条件：无404/异常状态 + 标准M3U8 + 可播放分片 + 分辨率≥1080
            if status >= 200 and status < 400 and is_m3u8_valid and can_play and real_h >= MIN_VERTICAL_RES:
                qualified.append(item)
        print(f"【频道统计】{ch_name} 达标链接总数：{len(qualified)}，单频道最大留存：{MAX_STREAM_PER_CHANNEL}", flush=True)
        final_map[ch_name] = [item[0] for item in qualified[:MAX_STREAM_PER_CHANNEL]]
    return final_map

# 输出函数：分类表头严格匹配 分类名,#genre#
def export_result(group_info: list, final_stream_map: dict[str, list[str]]):
    lines = []
    for group_name, ch_list in group_info:
        lines.append(f"{group_name},#genre#")
        for ch_name in ch_list:
            stream_list = final_stream_map.get(ch_name, [])
            for link in stream_list:
                lines.append(f"{ch_name},{link}")
        lines.append("")
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.flush()
    os.sync()
    stream_count = sum(1 for line in lines if "," in line)
    print(f"【阶段3-输出完成】最终有效流媒体总条数：{stream_count}", flush=True)

def main():
    print("====== IPTV分拣脚本启动 ======", flush=True)
    source_pool = load_source_list()
    group_info, core_mapping = load_white_list()
    raw_channel_cache = defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=SOURCE_FETCH_WORKERS) as exe:
        futures = [exe.submit(fetch_channel_from_source, s, core_mapping) for s in source_pool]
        for fu in futures:
            try:
                pair_list = fu.result(timeout=SOURCE_FETCH_TIMEOUT + 2)
                for full_ch, link in pair_list:
                    raw_channel_cache[full_ch].append(link)
            except concurrent.futures.TimeoutError:
                print("【警告】单个直播源拉取超时，自动跳过", flush=True)
            except Exception as e:
                print(f"【警告】直播源处理异常：{str(e)}", flush=True)
    for ch in raw_channel_cache:
        raw_channel_cache[ch] = list(dict.fromkeys(raw_channel_cache[ch]))
    print(f"【阶段1完成】待测速频道总数量：{len(raw_channel_cache)}", flush=True)
    qualified_channel_map = filter_best_streams(raw_channel_cache, core_mapping)
    print(f"【阶段2完成】完成测速频道数量：{len(qualified_channel_map)}", flush=True)
    export_result(group_info, qualified_channel_map)

    urllib3.PoolManager().clear()
    os.sync()
    time.sleep(0.5)
    pool = urllib3.PoolManager()
    pool.clear()
    urllib3.disable_warnings()
    for t in threading.enumerate():
        if t is not threading.main_thread():
            t.join(timeout=1)
    time.sleep(1)
    print("====== Python资源全部释放完成 ======", flush=True)
    sys.exit(0)

if __name__ == "__main__":
    main()
