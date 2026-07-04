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
TASK_GLOBAL_TIMEOUT = 12
BATCH_GLOBAL_TIMEOUT = 25
MIN_VERTICAL_RES = 1080
MAX_STREAM_PER_CHANNEL = 6
SOURCE_FETCH_TIMEOUT = 3
SOURCE_FETCH_WORKERS = 3
STREAM_EVAL_WORKERS = 12
batch_size = 60
DEBUG_LOG = False

# ==========需求1：标准化cctv核心ID函数==========
def standardize_core_id(raw_name: str) -> str:
    # 全部小写、删除所有横杠
    s = raw_name.lower().replace("-", "")
    # 匹配cctv+数字，只保留cctv+数字部分
    match = re.search(r"cctv(\d+)", s)
    if match:
        return f"cctv{match.group(1)}"
    # 非cctv频道返回原小写字符串兜底
    return s

# 新增：判断是否为4:3标清画面，4:3返回True需要过滤
def is_4_3_ratio(width: int, height: int) -> bool:
    if width <= 0 or height <= 0:
        return False
    ratio = width / height
    # 允许微小误差，接近4:3判定为标清黑边画面
    return abs(ratio - 4 / 3) <= 0.03

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

# ==========需求5、6：解析m3u8获取频道ID+画面宽高==========
def get_m3u8_info(headers, m3u8_url: str) -> tuple[str | None, int, int]:
    """返回(标准化频道coreID, 宽度, 高度)，无数据返回(None,0,0)"""
    try:
        resp = requests.get(m3u8_url, headers=headers, timeout=1.0, verify=False, allow_redirects=True)
        text = resp.text
        stream_core = None
        w = 0
        h = 0
        # 提取分辨率宽高
        res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", text)
        if res_match:
            w = int(res_match.group(1))
            h = int(res_match.group(2))
        # 优先匹配tvg-id
        tvg_match = re.search(r'tvg-id="([^"]+)"', text)
        if tvg_match:
            raw_tvg = tvg_match.group(1)
            stream_core = standardize_core_id(raw_tvg)
        # 兜底匹配#EXTINF频道名称
        ext_match = re.search(r'#EXTINF:-1,(.+?)\n', text)
        if ext_match and stream_core is None:
            raw_ch = ext_match.group(1)
            stream_core = standardize_core_id(raw_ch)
        return stream_core, w, h
    except Exception:
        return None, 0, 0

# 改造返回元组：(延迟, 分辨率高度, 是否频道匹配, 是否4:3标清)
def stream_quality_detect(url: str, target_core_id: str) -> tuple[float, int, bool, bool]:
    headers = {"User-Agent": "Mozilla/5.0 AndroidTV", "Connection": "close"}
    delay = 9999.0
    max_res = 0
    match_flag = False
    four_3_flag = False
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
        # 2xx/3xx 判定链接存活
        if 200 <= resp.status_code < 400:
            stream_core, w, h = get_m3u8_info(headers, url)
            if stream_core == target_core_id:
                match_flag = True
            if h > 0:
                max_res = h
                # 判断4:3画面标记
                if is_4_3_ratio(w, h):
                    four_3_flag = True
    except Exception:
        pass
    return delay, max_res, match_flag, four_3_flag

# 改造子任务，携带target_core_id参数
def batch_subtask(url_group: list[tuple[int, str, str, str]]) -> dict[int, tuple[float, int, bool, bool]]:
    task_start = time.time()
    local_result = {}
    for real_idx, ch_name, url, target_core in url_group:
        if time.time() - task_start >= TASK_GLOBAL_TIMEOUT:
            break
        local_result[real_idx] = stream_quality_detect(url, target_core)
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

# ==========需求2：白名单改为 core_id -> 完整频道名 映射==========
def load_white_list() -> tuple[list, dict]:
    group_info = []
    core_to_fullname = dict()  # key:标准化coreid  value:完整频道名 CCTV-1综合
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

# ==========需求3：源内频道标准化匹配，自动替换为白名单标准全名==========
def fetch_channel_from_source(src_link: str, core_mapping: dict) -> list[tuple[str, str]]:
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36", "Connection": "close"}
    result_pairs = []
    if src_link.startswith("//"):
        src_link = "https:" + src_link
    try:
        resp = requests.get(src_link, headers=headers, timeout=SOURCE_FETCH_TIMEOUT, verify=False)
        text = resp.text
        # 匹配txt格式 频道,url
        txt_pattern = re.compile(r"([^,]+),(https?://[^\n]+)")
        for raw_ch, stream_url in txt_pattern.findall(text):
            raw_ch = raw_ch.strip().replace("#genre#", "")
            stream_url = stream_url.strip()
            if raw_ch.startswith("#") or is_stream_incompatible(stream_url):
                continue
            # 标准化源频道ID，匹配白名单
            src_core = standardize_core_id(raw_ch)
            if src_core in core_mapping:
                full_ch_name = core_mapping[src_core]
                result_pairs.append((full_ch_name, stream_url))
        # 匹配m3u EXTINF格式
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

# 改造批量测速：携带每个频道对应的target_core_id用于比对流内置频道
def filter_best_streams(channel_raw_map: dict[str, list[str]], core_mapping: dict) -> dict[str, list[str]]:
    final_map = defaultdict(list)
    total_ch = len(channel_raw_map)
    ch_url_index = []
    curr_idx = 0
    # 构建完整频道名 -> 标准化核心ID 映射
    fullname_to_core = {full_name:cid for cid,full_name in core_mapping.items()}
    print(f"【全部待测速频道列表】{list(channel_raw_map.keys())}", flush=True)
    for ch_name, url_list in channel_raw_map.items():
        target_cid = fullname_to_core[ch_name]
        for url in url_list:
            ch_url_index.append((curr_idx, ch_name, url, target_cid))
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
                    print(f"【批次超时】本批运行已满25秒，终止剩余未完成测速，已测数据全部保留", flush=True)
                    urllib3.PoolManager().clear()
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
            exe.shutdown(wait=True, cancel_futures=False)
            urllib3.PoolManager().clear()
            pool_tmp = urllib3.PoolManager()
            pool_tmp.clear()
            print(f"【批次完成】{start+1}~{batch_end_idx} 批次资源回收完毕", flush=True)
    # 组装数据：(url, 源优先级, 是否不匹配频道, 延迟, -分辨率高度, 是否4:3标清)
    ch_temp = defaultdict(list)
    for idx, ch_name, url, _ in ch_url_index:
        delay, res_h, is_match, is_43 = task_result.get(idx, (9999, 0, False, False))
        prio = get_stream_priority(url)
        ch_temp[ch_name].append((url, prio, not is_match, delay, -res_h, is_43))
    curr = 0
    for ch_name, eval_res in ch_temp.items():
        curr += 1
        print(f"【阶段2测速进度】{curr}/{total_ch} 完成频道：{ch_name}", flush=True)
        # 排序规则：匹配优先>源优先级>延迟从小到大>分辨率从高到低
        eval_res.sort(key=lambda x: (x[2], x[1], x[3], x[4]))
        # 过滤规则：频道匹配 + 分辨率≥1080 + 不是4:3标清画面
        qualified = []
        for item in eval_res:
            url, prio, not_match, delay, neg_h, is_43 = item
            if not_match:
                continue
            if (not is_43) and ((-neg_h) >= MIN_VERTICAL_RES or (-neg_h == 0)):
                qualified.append(item)
        print(f"【频道统计】{ch_name} 达标链接总数：{len(qualified)}，单频道最大留存：{MAX_STREAM_PER_CHANNEL}", flush=True)
        final_map[ch_name] = [item[0] for item in qualified[:MAX_STREAM_PER_CHANNEL]]
    return final_map

# ==========需求4：输出严格使用白名单完整频道名，#genre#格式不变==========
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
        raw_channel_cache[ch] = list(dict.fromkeys(raw_channel_cache))
    print(f"【阶段1完成】待测速频道总数量：{len(raw_channel_cache)}", flush=True)
    qualified_channel_map = filter_best_streams(raw_channel_cache, core_mapping)
    print(f"【阶段2完成】完成测速筛选频道数量：{len(qualified_channel_map)}", flush=True)
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
