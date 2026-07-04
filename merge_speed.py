import sys
sys.stdout.reconfigure(encoding="utf-8")
import requests
import concurrent.futures
import re
from collections import defaultdict
import time
import urllib3
import os
import threading

# 全局关闭HTTPS未校验警告，消除日志刷屏
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 全局业务参数
SOURCE_FILE = "sources.txt"
WHITE_LIST_FILE = "channel_whitelist.txt"
OUTPUT_TXT = "tv.txt"
STREAM_REQ_TIMEOUT = 1.5
BATCH_GLOBAL_TIMEOUT = 25  # 按要求固定不修改
MIN_VERTICAL_RES = 1080
MAX_STREAM_PER_CHANNEL = 6
SOURCE_FETCH_TIMEOUT = 6
SOURCE_FETCH_WORKERS = 3
STREAM_EVAL_WORKERS = 12
batch_size = 60  # 按要求固定不修改
DEBUG_LOG = False

# 全局总运行超时控制
GLOBAL_FORCE_STOP_SEC = 28 * 60  # 28分钟主动停止，导出已有成果
GLOBAL_HARD_LIMIT_SEC = 30 * 60  # 30分钟系统硬上限
# 静默卡死3分钟无心跳强制终止
HEARTBEAT_STOP_SEC = 3 * 60

# 标准化函数：提取 cctv+数字 核心标识，忽略横杠、大小写、后缀文字
def standardize_core_id(raw_name: str) -> str:
    s = raw_name.lower().replace("-", "")
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

# m3u8解析独立1秒短超时，慢链接快速跳过
def get_real_video_res(m3u8_url: str, headers, timeout) -> tuple[int, str | None]:
    try:
        resp = requests.get(m3u8_url, headers=headers, timeout=1.0, verify=False, allow_redirects=True)
        content = resp.text
        real_height = 720
        stream_raw_id = None
        # 提取分辨率
        res_pattern = re.compile(r"RESOLUTION=(\d+)x(\d+)")
        match = res_pattern.search(content)
        if match:
            real_height = int(match.group(2))
        fragment_res = re.compile(r"#EXT-X-VIDEO-RANGE.*RESOLUTION=(\d+)x(\d+)")
        frag_match = fragment_res.search(content)
        if frag_match:
            real_height = int(frag_match.group(2))
        # 提取 tvg-id
        tvg_id_pat = re.compile(r'tvg-id="([^"]+)"')
        tvg_match = tvg_id_pat.search(content)
        if tvg_match:
            stream_raw_id = tvg_match.group(1)
        # 兜底提取 #EXTINF 内置频道名称
        extinf_name_pat = re.compile(r'#EXTINF:-1.*,(.+?)\n')
        name_match = extinf_name_pat.search(content)
        if not stream_raw_id and name_match:
            stream_raw_id = name_match.group(1)
        # 标准化流内置标识
        if stream_raw_id:
            stream_core = standardize_core_id(stream_raw_id)
            return (real_height, stream_core)
        return (real_height, None)
    except Exception:
        return (720, None)

# 新增入参 target_core_id：当前频道标准核心ID；返回(延迟, 真实高度, 是否频道匹配)
def stream_quality_detect(url: str, target_core_id: str) -> tuple[float, int, bool]:
    headers = {"User-Agent": "Mozilla/5.0 AndroidTV", "Connection": "close"}
    delay = 9999.0
    real_height = 0
    is_channel_match = False
    start = time.time()
    try:
        resp = requests.head(
            url,
            headers=headers,
            timeout=STREAM_REQ_TIMEOUT,
            stream=True,
            verify=False,
            allow_redirects=True
        )
        delay = round(time.time() - start, 3)
        if 200 <= resp.status_code < 400:
            real_height, stream_core = get_real_video_res(url, headers, STREAM_REQ_TIMEOUT)
            if stream_core == target_core_id:
                is_channel_match = True
    except Exception:
        pass
    return delay, real_height, is_channel_match

# 元组新增第4项 target_core_id
def batch_subtask(url_group: list[tuple[int, str, str, str]]) -> dict[int, tuple[float, int, bool]]:
    task_start = time.time()
    local_result = {}
    for real_idx, ch_name, url, target_core_id in url_group:
        if time.time() - task_start >= BATCH_GLOBAL_TIMEOUT:
            break
        local_result[real_idx] = stream_quality_detect(url, target_core_id)
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

# 白名单读取：构建 核心ID -> 完整标准频道名（CCTV-1综合）映射
def load_white_list() -> tuple[list, dict]:
    group_info = []
    core_to_fullname = dict()
    current_group = ""
    with open(WHITE_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f.readlines():
            raw_line = line.replace("\r", "").replace("\n", "").strip()
            if not raw_line:
                continue
            if raw_line.endswith(",#genre#"):
                current_group = raw_line.replace(",#genre#", "").strip()
                group_info.append((current_group, []))
                continue
            if current_group:
                full_ch_name = raw_line.strip()
                group_info[-1][1].append(full_ch_name)
                core_id = standardize_core_id(full_ch_name)
                core_to_fullname[core_id] = full_ch_name
    print(f"【阶段1-白名单加载】分类数：{len(group_info)}", flush=True)
    return group_info, core_to_fullname

def fetch_channel_from_source(src_link: str, core_mapping: dict) -> list[tuple[str, str]]:
    headers = {"User-Agent": "Mozilla/5.0 Chrome", "Connection": "close"}
    result_pairs = []
    if src_link.startswith("//"):
        src_link = "https:" + src_link
    try:
        resp = requests.get(src_link, headers=headers, timeout=SOURCE_FETCH_TIMEOUT, verify=False)
        text = resp.text
        txt_pattern = re.compile(r"([^,]+),(https?://[^\n]+)")
        for raw_ch, url in txt_pattern.findall(text):
            raw_ch = raw_ch.strip().replace("#genre#", "")
            url = url.strip()
            if raw_ch.startswith("#"):
                continue
            source_core = standardize_core_id(raw_ch)
            if source_core in core_mapping and not is_stream_incompatible(url):
                standard_full_name = core_mapping[source_core]
                result_pairs.append((standard_full_name, url))
        m3u_pattern = re.compile(r"#EXTINF:-1,(.+?)\n(https?://[^\n]+)")
        for raw_ch, url in m3u_pattern.findall(text):
            raw_ch = raw_ch.strip()
            url = url.strip()
            source_core = standardize_core_id(raw_ch)
            if source_core in core_mapping and not is_stream_incompatible(url):
                standard_full_name = core_mapping[source_core]
                result_pairs.append((standard_full_name, url))
    except Exception as e:
        print(f"【警告】源 {src_link} 拉取异常：{str(e)}", flush=True)
    return result_pairs

# 新增入参 global_start_ts：脚本全局启动时间戳，last_heartbeat_ts：上次打印批次日志时间戳
def filter_best_streams(channel_raw_map: dict[str, list[str]], fullname_to_core: dict, global_start_ts: float) -> dict[str, list[str]]:
    final_map = defaultdict(list)
    total_ch = len(channel_raw_map)
    ch_url_index = []
    curr_idx = 0
    print(f"【全部待测速频道总数】{total_ch}", flush=True)
    last_heartbeat_ts = time.time()  # 初始化心跳时间戳

    for ch_name, url_list in channel_raw_map.items():
        target_core = fullname_to_core[ch_name]
        for url in url_list:
            ch_url_index.append((curr_idx, ch_name, url, target_core))
            curr_idx += 1
    task_result = {}
    total_url = len(ch_url_index)
    print(f"【测速预加载】待检测总链接：{total_url}", flush=True)
    last_heartbeat_ts = time.time()  # 刷新心跳

    for start in range(0, total_url, batch_size):
        now_ts = time.time()
        # 1. 判断静默卡死3分钟无心跳
        if now_ts - last_heartbeat_ts >= HEARTBEAT_STOP_SEC:
            print(f"【静默卡死预警】已连续3分钟无任何批次输出，强制终止测速，导出当前已完成数据", flush=True)
            break
        # 2. 判断全局28分钟总时长
        run_cost = now_ts - global_start_ts
        if run_cost >= GLOBAL_FORCE_STOP_SEC:
            print(f"【全局主动停止】已运行28分钟，放弃剩余未检测链接，使用当前已完成数据导出tv.txt", flush=True)
            break

        batch_start_time = now_ts
        batch_items = ch_url_index[start:start + batch_size]
        batch_end_idx = min(start + batch_size, total_url)
        print(f"【测速批次】{start + 1} ~ {batch_end_idx} / {total_url}", flush=True)
        last_heartbeat_ts = time.time()  # 打印批次日志，刷新心跳
        sub_task_groups = [[] for _ in range(STREAM_EVAL_WORKERS)]
        for idx, item in enumerate(batch_items):
            sub_task_groups[idx % STREAM_EVAL_WORKERS].append(item)
        exe = concurrent.futures.ThreadPoolExecutor(max_workers=STREAM_EVAL_WORKERS)
        futures = []
        for g in sub_task_groups:
            futures.append(exe.submit(batch_subtask, g))
        complete_futures = set()
        while len(complete_futures) < len(futures):
            # 内层循环也持续检测心跳卡死
            inner_now = time.time()
            if inner_now - last_heartbeat_ts >= HEARTBEAT_STOP_SEC:
                print(f"【静默卡死预警】批次内连续3分钟无输出，强制终止", flush=True)
                break
            if inner_now - batch_start_time >= BATCH_GLOBAL_TIMEOUT:
                print(f"【批次超时】已满{BATCH_GLOBAL_TIMEOUT}秒，终止当前批次剩余测速", flush=True)
                break
            try:
                for fu in concurrent.futures.as_completed(futures, timeout=0.3):
                    if fu not in complete_futures:
                        complete_futures.add(fu)
                        try:
                            task_result.update(fu.result())
                        except Exception as e:
                            print(f"【线程异常】{str(e)}", flush=True)
            except concurrent.futures.TimeoutError:
                continue
        # 修复卡死：强制取消未完成任务，非阻塞关闭线程池，立刻进入下一批
        for fu in futures:
            fu.cancel()
        exe.shutdown(wait=False)
        print(f"【测速批次】{start+1}~{batch_end_idx} 处理完成", flush=True)
        last_heartbeat_ts = time.time()  # 批次结束刷新心跳

    ch_temp = defaultdict(list)
    for idx, ch_name, url, _ in ch_url_index:
        delay, real_h, is_match = task_result.get(idx, (9999, 0, False))
        # 存储结构 (url, 源优先级, 是否不匹配, 延迟, -分辨率)
        ch_temp[ch_name].append((url, get_stream_priority(url), not is_match, delay, -real_h))

    curr = 0
    for ch_name, eval_res in ch_temp.items():
        curr += 1
        # 已删除单频道进度、单频道统计打印，极简日志
        # 排序规则：频道匹配 > 延迟从小到大 > 分辨率从高到低，移除源优先级权重
        eval_res.sort(key=lambda x: (x[2], x[3], x[4]))
        # 双重过滤：频道匹配成功 + 垂直分辨率≥1080
        qualified = [item for item in eval_res if (item[2] is False) and (-item[4] >= MIN_VERTICAL_RES)]
        final_map[ch_name] = [item[0] for item in qualified[:MAX_STREAM_PER_CHANNEL]]
    return final_map

def export_result(group_info: list, final_stream_map: dict[str, list[str]]):
    lines = []
    for group_name, ch_list in group_info:
        lines.append(f"{group_name},#genre#")
        for ch_full_name in ch_list:
            stream_list = final_stream_map.get(ch_full_name, [])
            for link in stream_list:
                lines.append(f"{ch_full_name},{link}")
        lines.append("")
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.flush()
    os.sync()
    total = sum(1 for line in lines if "," in line)
    print(f"【输出完成】有效匹配1080P总链接数：{total}", flush=True)

def main():
    print("====== IPTV分拣启动 ======", flush=True)
    global_start_time = time.time()  # 记录脚本全局启动时间戳
    source_pool = load_source_list()
    group_info, core_mapping = load_white_list()
    # 构建反向映射：完整频道名 -> 标准化核心ID
    fullname_to_core = {full_name: core for core, full_name in core_mapping.items()}
    raw_channel_cache = defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=SOURCE_FETCH_WORKERS) as exe:
        futures = [exe.submit(fetch_channel_from_source, s, core_mapping) for s in source_pool]
        for fu in futures:
            retry = 0
            max_retry = 1
            while retry <= max_retry:
                try:
                    pair_list = fu.result(timeout=SOURCE_FETCH_TIMEOUT + 2)
                    for full_ch, link in pair_list:
                        raw_channel_cache[full_ch].append(link)
                    break
                except concurrent.futures.TimeoutError:
                    retry += 1
                    if retry > max_retry:
                        print("【警告】源拉取重试后依旧超时，自动跳过", flush=True)
                    else:
                        print("【提示】源拉取超时，正在重试一次", flush=True)
    # 单频道完全相同URL去重
    for ch in raw_channel_cache:
        raw_channel_cache[ch] = list(dict.fromkeys(raw_channel_cache[ch]))
    print(f"【源解析完成】待测速频道总数：{len(raw_channel_cache)}", flush=True)
    # 传入全局启动时间戳，开启28分钟超时判断+3分钟静默心跳监控
    qualified_map = filter_best_streams(raw_channel_cache, fullname_to_core, global_start_time)
    print(f"【测速筛选完成】有效高清匹配频道数量：{len(qualified_map)}", flush=True)
    export_result(group_info, qualified_map)

    # 全局资源完整释放
    os.sync()
    time.sleep(0.5)
    for t in threading.enumerate():
        if t != threading.main_thread():
            t.join(timeout=1)
    time.sleep(1)
    print("====== 分拣脚本执行结束 ======", flush=True)
    sys.exit(0)

if __name__ == "__main__":
    main()
