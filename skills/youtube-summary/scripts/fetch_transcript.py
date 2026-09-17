#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_transcript.py — YouTube 字幕抓取与结构化解析器

用途
----
给定一个 YouTube 视频链接（或视频 ID），抓取该视频的字幕（优先人工字幕，回退自动字幕），
把字幕解析成「带时间戳的段落」，连同视频元数据一起输出为一份结构化 JSON。
该 JSON 是给上层 LLM 做「中文总结」用的输入，因此字段设计以「省 token + 可直接引用」为目标。

设计要点（都是踩过坑总结出来的，改动时请勿回退）
------------------------------------------------
1. 代理必须「显式传参」，不能依赖环境变量。
   不少受管运行环境（沙箱、CI、容器、某些 agent 平台）会强制覆盖
   `HTTP_PROXY` / `HTTPS_PROXY` 环境变量，把它们指向一个不可用的端口。
   此时 yt-dlp 会报 `Tunnel connection failed: 502 Bad Gateway`，
   而同一个代理地址改用 `--proxy` 参数显式传入却完全正常。
   因此本脚本自行探测可用代理，并通过 `--proxy` 参数显式传递，不依赖环境变量。

2. 字幕格式优先 json3，回退 vtt。
   json3 是结构化的（events/segs），能拿到精确到毫秒的时间戳，且没有 vtt 的「滚动行重复」问题。

3. 自动字幕按行拼接时必须「补空格」。
   YouTube 自动字幕在视觉换行处会丢失词间空格，直接拼接会得到 `wouldlike`、`likechpd`
   这类粘连词。正确做法是按 `aAppend` 事件分行，行与行之间以空格连接。
   注意中文不适用该规则（中文本身不用空格分词），见 `smart_join()`。

4. json3 存在两种截然不同的排版模式，必须分别判别（详见 `parse_json3()`）：
   自动字幕靠 `aAppend` 事件标记换行，人工字幕／中文则每个 event 自成一行。

用法
----
    python3 fetch_transcript.py <url_or_id> [选项]

需要 Python 3.9+（脚本本身只用标准库）。唯一的外部依赖是 yt-dlp 可执行文件，
脚本会按 `$YT_DLP` → PATH → 解释器同目录 → 常见安装位置 的顺序自动定位。

选项
----
    --lang LANG        指定字幕语言（如 en / zh-Hans / ja），默认自动选原语言
    --out FILE         把 JSON 写入文件（默认输出到 stdout）
    --paragraph-only   只输出段落文本（调试用，非 JSON）
    --full-text        额外输出拼接好的全文（默认不输出，避免 JSON 体积翻倍）
    --max-chars N      分块阈值字符数，超出则切分为 chunks（默认 180000）
    --no-chunks        强制不分块（即使超长）
    --list-subs        只列出该视频的可选字幕语言，不下载
    --keep-files       保留下载的字幕原始文件（调试用）
    --timeout N        单步网络操作超时秒数（默认 60）
    --proxy URL        显式指定代理，如 http://127.0.0.1:7890

环境变量
--------
    YT_DLP             手动指定 yt-dlp 可执行文件路径
    YT_SUMMARY_PROXY   手动指定代理 URL（等价于 --proxy，但优先级低于命令行参数）
    YT_SUMMARY_NODE    手动指定 Node 可执行文件路径（供 yt-dlp 解算签名）

输出
----
stdout 输出 JSON，结构见 `build_output()` 的注释。

退出码
------
    0  成功
    2  参数错误
    3  依赖缺失 / 代理或网络不可用
    4  视频不可访问（私有 / 删除 / 地区限制 / 需登录）
    5  该视频没有可用字幕
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------

#: 代理候选端口列表（按优先级排序）。
#: 7890 / 7897 是 Clash 系（ClashX、Clash Verge 等）的默认混合端口；7891 为其默认 SOCKS5 端口；
#: 其余为 v2rayN / Surge / Privoxy 等常见代理软件的默认端口。
#: 若你的代理端口不在列表中，用 `--proxy` 或环境变量 `YT_SUMMARY_PROXY` 显式指定即可。
PROXY_HOST = "127.0.0.1"
PROXY_PORT_CANDIDATES: Tuple[int, ...] = (
    7890,       # Clash / ClashX / Clash Verge 混合端口
    7897,       # Clash Verge Rev 默认混合端口
    7891,       # Clash SOCKS5
    10809,      # v2rayN HTTP
    10808,      # v2rayN SOCKS
    1087,       # 旧版 v2ray HTTP
    1080,       # 通用 SOCKS
    8118,       # Privoxy
    8888,       # 通用
    6152,       # Surge
    2080,       # 通用
)

#: 代理探测时用来验证隧道是否可建立的目标（YouTube 主站）
PROBE_TARGET_HOST = "www.youtube.com"
PROBE_TARGET_PORT = 443

#: 探测单个代理的超时（秒）。本地代理响应极快，短超时即可，避免逐个探测太慢。
PROBE_TIMEOUT = 0.8

#: 段落切分参数。
#: 注意：YouTube 自动字幕的行是「固定宽度的显示行」（实测每行约 36 字符、每约 2.2 秒
#: 滚动一行），行与行之间几乎没有真实空隙（实测仅约 1.4% 的行间存在正间隙），
#: 因此「静音停顿」只能作为软信号，切段主要依赖长度阈值。
#:
#: 段落刻意切得比较细（上限约 800 字符 ≈ 3 分钟语音）：因为自动字幕没有标点、无法
#: 在语义边界精确切分，段落必然会在句子中间断开。段落越短，段落首时间戳与「该段实际
#: 讨论内容」的偏差就越小，上层引用时间戳做跳转链接时才越精准。
PARA_TARGET_CHARS = 600     # 目标段落长度：达到后若遇句末标点或明显停顿即断开
PARA_MAX_CHARS = 800        # 强制断段长度：达到即无条件断开
PARA_MIN_CHARS = 150        # 单段落最小字符数，不足则并入上一段（避免碎片段）
PARA_GAP_SEC = 2.6          # 判定为「明显停顿」的行间空隙阈值（秒），软信号

#: 中文（CJK 占比超过该阈值即视为中文内容）的段落长度缩放系数。
#: 中文信息密度高，同样字符数对应的语音时长约为英文的两倍，故按比例缩小段落。
PARA_CJK_RATIO = 0.30
PARA_CJK_SCALE = 0.60

#: 估算 token 数用的「每 token 字符数」。
#: 英文约 4 字符/token，这里保守取 3.5；中文（CJK）约 1 字符/token。
CHARS_PER_TOKEN = 3.5
CJK_CHARS_PER_TOKEN = 1.0

#: 默认分块阈值（字符）。超过则切成 chunks 交给上层做 map-reduce 总结。
#: 取值依据：实测英文转写约 3.5 字符/token，18 万字符 ≈ 5.1 万 token，
#: 仍可被主流模型的上下文一次性容纳；再长才需要分块（分块会带来信息损耗）。
DEFAULT_MAX_CHARS = 180_000

#: 输出 JSON 中「其他可用字幕语言」最多列出多少项（列全了会白白吃掉大量 token）
MAX_OTHER_LANGUAGES = 12

#: 视频简介最多保留多少字符
MAX_DESCRIPTION_CHARS = 800

#: 分块模式下每个 chunk 的目标字符数（约 4 万字符 ≈ 1.1 万 token）。
#: 注意它与 DEFAULT_MAX_CHARS 是两个独立概念：后者决定「是否需要分块」，
#: 前者决定「每块切多大」。混用会导致切出仍然过大的块，失去分块的意义。
CHUNK_SIZE_CHARS = 40_000

#: 字幕原始文件的扩展名
SUB_EXTENSIONS = ("json3", "vtt", "srv3", "srv2", "srv1", "ttml", "srt")


# ---------------------------------------------------------------------------
# 小工具函数
# ---------------------------------------------------------------------------

#: Unicode 区间：东亚文字（中日韩）与全角标点。
#: 用来判断「拼接两段文本时要不要加空格」以及「估算 token 数」。
CJK_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x3000, 0x303F),   # CJK 标点（、。「」等）
    (0x3040, 0x30FF),   # 日文平假名 / 片假名
    (0x3400, 0x4DBF),   # CJK 统一表意文字扩展 A
    (0x4E00, 0x9FFF),   # CJK 统一表意文字
    (0xAC00, 0xD7AF),   # 韩文音节
    (0xF900, 0xFAFF),   # CJK 兼容表意文字
    (0xFF00, 0xFFEF),   # 全角字符
)


def is_cjk(character: str) -> bool:
    """判断单个字符是否属于东亚文字（CJK）。

    参数:
        character: 单个字符。传入更长的字符串时只看首字符。

    返回:
        True 表示该字符是中日韩文字或全角标点。
    """
    if not character:
        return False
    code = ord(character[0])
    return any(low <= code <= high for low, high in CJK_RANGES)


def count_cjk(text: str) -> int:
    """统计字符串里东亚文字字符的个数。

    参数:
        text: 待统计的文本。

    返回:
        CJK 字符数量。
    """
    return sum(1 for character in text if is_cjk(character))


def smart_join(parts: Iterable[str]) -> str:
    """按语言习惯拼接文本片段：中文之间不加空格，英文之间加空格。

    必要性：转写文本是按「显示行」切分后再拼回来的。英文行与行之间需要补空格
    （否则出现 `wouldlike` 这类粘连词），但中文本身不用空格分词，若同样补空格
    就会得到「大家好 今天是我们」这种多余空格。

    规则：只要相邻两段的**任一侧边界是 CJK 字符**，就不插空格。
        中文 + 中文 → 「大家好今天…」
        英文 + 英文 → 「hello world」
        Transformer + 中文 → 「Transformer这个模型」
        中文 + 英文 → 「我们用的是GPT」

    参数:
        parts: 文本片段序列。

    返回:
        拼接好的字符串。
    """
    result = ""
    for part in parts:
        if not part:
            continue
        if result:
            left = result[-1]
            right = part[0]
            if not (is_cjk(left) or is_cjk(right)):
                result += " "
        result += part
    return result


def normalize_whitespace(text: str) -> str:
    """把文本规范化成「单行、无冗余空白」的形式。

    字幕原始文本里常混有换行符（人工字幕尤其明显，例如
    `"in front of the\\nelephants"`）。直接留在段落文本里会让 JSON 出现多行字符串，
    也会干扰上层阅读。

    处理方式是**按换行拆开后再用 smart_join 拼回** —— 这样英文换行处会补上空格，
    而中文换行处不会多出空格。

    参数:
        text: 原始文本。

    返回:
        规范化后的单行文本。
    """
    if not text:
        return ""
    if "\n" in text or "\r" in text:
        pieces = [piece for piece in re.split(r"\s*[\r\n]+\s*", text) if piece]
        text = smart_join(pieces)
    # 折叠连续空格/制表符（不影响中文字符，因为中文不属于 \s）
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量。

    不同语言的字符-token 比差异极大：英文约 4 字符/token，中文约 1 字符/token。
    若统一按英文比例估算，中文文本的 token 数会被严重低估（实测差 3 倍以上），
    进而影响「是否分块」的判断。

    参数:
        text: 待估算的文本。

    返回:
        估算的 token 数（整数）。
    """
    cjk = count_cjk(text)
    other = max(0, len(text) - cjk)
    return int(cjk / CJK_CHARS_PER_TOKEN + other / CHARS_PER_TOKEN)


def log(message: str) -> None:
    """向 stderr 打印进度信息。

    参数:
        message: 要打印的文本。之所以写到 stderr，是为了不污染 stdout 的 JSON 输出。

    返回:
        None
    """
    print(f"[fetch_transcript] {message}", file=sys.stderr, flush=True)


def fatal(message: str, code: int) -> "NoReturn":  # type: ignore[valid-type]
    """打印错误信息并终止进程。

    参数:
        message: 错误描述。
        code:    退出码（见模块 docstring 的「退出码」小节）。

    返回:
        该函数不会返回，内部调用 sys.exit()。
    """
    print(f"[fetch_transcript] ERROR: {message}", file=sys.stderr, flush=True)
    sys.exit(code)


def find_node_runtime() -> Optional[str]:
    """定位可用的 JavaScript 运行时（Node），供 yt-dlp 解算 YouTube 签名用。

    yt-dlp 近年来在缺少 JS 运行时时会打印告警并降级，可能拿不到部分格式或字幕。
    本函数按以下顺序探测，返回第一个可用的 Node：

        1. 环境变量 `YT_SUMMARY_NODE`（显式指定，优先级最高）
        2. PATH 中的 `node`（最标准的来源）
        3. 常见安装位置（Homebrew、/usr/local、~/.local/bin）
        4. 某些 agent 平台的受管运行时目录（~/.workbuddy/binaries/node/versions/*）

    参数:
        无。

    返回:
        Node 可执行文件的绝对路径字符串；若都找不到则返回 None
        （此时调用方不应传 `--js-runtimes`，交由 yt-dlp 使用其默认行为）。
    """
    env_override = os.environ.get("YT_SUMMARY_NODE", "").strip()
    if env_override and Path(env_override).is_file() and os.access(env_override, os.X_OK):
        return env_override

    on_path = shutil.which("node")
    if on_path:
        return on_path

    candidates: List[Path] = [
        Path("/opt/homebrew/bin/node"),      # macOS (Apple Silicon) Homebrew
        Path("/usr/local/bin/node"),         # macOS (Intel) Homebrew / 通用
        Path.home() / ".local" / "bin" / "node",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    # 部分 agent 平台（如 WorkBuddy）会自管一份 Node 运行时，一并纳入候选
    managed_root = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if managed_root.is_dir():
        for version_dir in sorted(managed_root.glob("*/bin/node"), reverse=True):
            if version_dir.is_file() and os.access(version_dir, os.X_OK):
                return str(version_dir)

    return None


def find_ytdlp() -> str:
    """定位 yt-dlp 可执行文件。

    本脚本自身是纯标准库实现，唯一的运行依赖就是这个外部的 yt-dlp。
    按以下顺序查找：

        1. 环境变量 `YT_DLP`（显式指定可执行文件路径）
        2. PATH 中的 `yt-dlp`
        3. 与当前 Python 解释器同目录（虚拟环境里的标准安装位置）
        4. 常见安装位置（Homebrew、~/.local/bin、agent 平台受管环境）

    参数:
        无。

    返回:
        yt-dlp 可执行文件的绝对路径。

    异常:
        找不到时直接以退出码 3 终止进程，并给出各平台的安装指引。
    """
    env_override = os.environ.get("YT_DLP", "").strip()
    if env_override and Path(env_override).is_file() and os.access(env_override, os.X_OK):
        return env_override

    on_path = shutil.which("yt-dlp")
    if on_path:
        return on_path

    candidates: List[Path] = []
    # 虚拟环境场景：yt-dlp 通常与 python 解释器装在同一个 bin 目录下
    if sys.executable:
        candidates.append(Path(sys.executable).parent / "yt-dlp")
    candidates += [
        Path("/opt/homebrew/bin/yt-dlp"),    # macOS (Apple Silicon) Homebrew
        Path("/usr/local/bin/yt-dlp"),       # macOS (Intel) Homebrew / 通用
        Path.home() / ".local" / "bin" / "yt-dlp",
        Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "bin" / "yt-dlp",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    fatal(
        "找不到 yt-dlp。请任选一种方式安装后重试：\n"
        "  • pip（推荐装进虚拟环境）: python3 -m venv ~/.venvs/yt && ~/.venvs/yt/bin/pip install -U yt-dlp\n"
        "  • pipx:  pipx install yt-dlp\n"
        "  • Homebrew (macOS):  brew install yt-dlp\n"
        "  • 或直接指定路径：   export YT_DLP=/path/to/yt-dlp",
        3,
    )
    raise AssertionError("unreachable")  # 让类型检查器满意


# ---------------------------------------------------------------------------
# 代理探测
# ---------------------------------------------------------------------------

def probe_http_proxy(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """探测某个「HTTP 代理」端口是否能成功建立到 YouTube 的 CONNECT 隧道。

    实现方式是手工完成一次 HTTP CONNECT 握手，判断响应状态行是否含 200。
    这样做不依赖任何第三方库，且比「跑一次 yt-dlp 试试」快得多（毫秒级）。

    参数:
        host:    代理主机，通常为 127.0.0.1。
        port:    代理端口。
        timeout: 连接与读取超时（秒）。

    返回:
        True 表示该端口是可用 HTTP 代理；False 表示端口未监听、非 HTTP 代理或隧道被拒。
    """
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        request = (
            f"CONNECT {PROBE_TARGET_HOST}:{PROBE_TARGET_PORT} HTTP/1.1\r\n"
            f"Host: {PROBE_TARGET_HOST}:{PROBE_TARGET_PORT}\r\n"
            f"\r\n"
        )
        sock.sendall(request.encode("ascii"))
        sock.settimeout(timeout)
        response = sock.recv(128)
        if not response:
            return False
        # 状态行形如 "HTTP/1.1 200 Connection established"
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        return " 200" in status_line
    except (OSError, socket.timeout):
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def port_is_open(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """判断某端口是否有进程监听（仅做 TCP 连通性测试）。

    用于识别「可能是 SOCKS 代理」的端口 —— 这类端口无法通过 HTTP CONNECT 探测，
    但 TCP 能连上，值得让 yt-dlp 用 socks5 协议实际试一次。

    参数:
        host:    目标主机。
        port:    目标端口。
        timeout: 连接超时（秒）。

    返回:
        True 表示 TCP 三次握手成功。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def resolve_proxy_candidates(explicit_proxy: Optional[str]) -> List[Optional[str]]:
    """构建「按优先级排序的代理候选列表」，供后续逐个尝试。

    优先级顺序：
        1. 命令行 --proxy 显式指定的代理（最高优先级，用户意图优先）
        2. 环境变量 YT_SUMMARY_PROXY（本项目自定义的覆盖开关）
        3. 常见本地代理端口（先探测 HTTP CONNECT，能通则排前面；仅 TCP 通的按 socks5 排后面）
        4. 环境变量 HTTP_PROXY / HTTPS_PROXY（注意：沙箱可能劫持成失效端口，故放在很后面）
        5. None，即直连（兜底，适用于用户本身就在墙外或有透明代理的场景）

    参数:
        explicit_proxy: 命令行传入的代理 URL，形如 "http://127.0.0.1:7890"；未传则为 None。

    返回:
        代理 URL 字符串与 None 混合的列表，去重且保持顺序。None 永远排在最后。
    """
    http_hits: List[str] = []       # 确认可用的 HTTP 代理
    socks_maybe: List[str] = []     # 端口在监听但 HTTP 探测不通，疑似 SOCKS
    seen_ports: set = set()

    # --- 1) 显式参数优先 ---
    if explicit_proxy:
        return [explicit_proxy, None]

    # --- 2) 自定义环境变量开关 ---
    env_override = os.environ.get("YT_SUMMARY_PROXY", "").strip()
    if env_override:
        return [env_override, None]

    # --- 3) 扫描常见端口 ---
    for port in PROXY_PORT_CANDIDATES:
        if port in seen_ports:
            continue
        seen_ports.add(port)
        http_url = f"http://{PROXY_HOST}:{port}"
        if probe_http_proxy(PROXY_HOST, port):
            http_hits.append(http_url)
            log(f"探测到可用 HTTP 代理：{http_url}")
        elif port_is_open(PROXY_HOST, port):
            socks_maybe.append(f"socks5://{PROXY_HOST}:{port}")
            log(f"端口 {port} 有监听但非 HTTP 代理，按 SOCKS5 备用")

    # --- 4) 环境变量代理（可能被沙箱劫持，故排后） ---
    env_proxies: List[str] = []
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if value and value not in env_proxies:
            env_proxies.append(value)

    candidates: List[Optional[str]] = []
    candidates.extend(http_hits)
    candidates.extend(env_proxies)
    candidates.extend(socks_maybe)
    candidates.append(None)  # 直连兜底

    # 去重同时保持顺序
    deduped: List[Optional[str]] = []
    for item in candidates:
        if item not in deduped:
            deduped.append(item)

    if len(deduped) == 1:
        log("未探测到任何本地代理，将尝试直连")
    return deduped


# ---------------------------------------------------------------------------
# URL 解析
# ---------------------------------------------------------------------------

#: 匹配各类 YouTube 链接中视频 ID 的正则（ID 恒定 11 个字符）
VIDEO_ID_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/live/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/v/([A-Za-z0-9_-]{11})"),
    re.compile(r"music\.youtube\.com/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})"),
)


def extract_video_id(raw: str) -> Optional[str]:
    """从任意形态的 YouTube 链接（或裸 ID）中提取 11 位视频 ID。

    支持的输入形态：
        - https://www.youtube.com/watch?v=XXXXXXXXXXX&t=42s
        - https://youtu.be/XXXXXXXXXXX?si=xxx
        - https://www.youtube.com/shorts/XXXXXXXXXXX
        - https://www.youtube.com/live/XXXXXXXXXXX
        - https://music.youtube.com/watch?v=XXXXXXXXXXX
        - 裸 ID：XXXXXXXXXXX

    参数:
        raw: 用户输入的原始字符串。

    返回:
        11 位视频 ID；无法识别时返回 None。
    """
    text = raw.strip().strip("<>\"'")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    for pattern in VIDEO_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def canonical_url(video_id: str) -> str:
    """由视频 ID 构造规范化的观看链接。

    参数:
        video_id: 11 位视频 ID。

    返回:
        形如 "https://www.youtube.com/watch?v=XXXXXXXXXXX" 的链接。
    """
    return f"https://www.youtube.com/watch?v={video_id}"


def format_timestamp(seconds: float) -> str:
    """把秒数格式化为 mm:ss 或 h:mm:ss 形式的时间戳。

    参数:
        seconds: 秒数（可为浮点）。

    返回:
        时长 < 1 小时返回 "mm:ss"；≥ 1 小时返回 "h:mm:ss"。
    """
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def deep_link(video_id: str, start_seconds: float) -> str:
    """构造可跳转到指定时间点的 YouTube 链接。

    参数:
        video_id:      11 位视频 ID。
        start_seconds: 目标起始秒数。

    返回:
        形如 "https://youtu.be/XXXXXXXXXXX?t=123" 的深链。
    """
    return f"https://youtu.be/{video_id}?t={int(max(0, start_seconds))}"


# ---------------------------------------------------------------------------
# yt-dlp 调用层
# ---------------------------------------------------------------------------

def run_ytdlp(
    ytdlp: str,
    args: Sequence[str],
    proxy: Optional[str],
    node_runtime: Optional[str],
    timeout: int,
) -> subprocess.CompletedProcess:
    """执行一次 yt-dlp 命令，统一注入代理、JS 运行时等公共参数。

    参数:
        ytdlp:        yt-dlp 可执行文件路径。
        args:         本次调用特有的参数列表。
        proxy:        代理 URL；为 None 时不加 --proxy（即直连）。
        node_runtime: Node 可执行文件路径；为 None 时不加 --js-runtimes。
        timeout:      子进程超时（秒）。

    返回:
        已完成子进程的 CompletedProcess 对象（stdout / stderr 均为文本）。
    """
    command: List[str] = [
        ytdlp,
        "--no-warnings",       # 抑制非致命告警，避免污染 stderr 判断
        "--no-playlist",       # 只处理单个视频，忽略链接里附带的播放列表
        "--retries", "1",      # 代理失败时不要疯狂重试，否则逐个候选尝试会非常慢
        "--socket-timeout", "15",
    ]
    # 只在确实找到 Node 时才注入 JS 运行时参数；否则交给 yt-dlp 用内置默认值
    if node_runtime:
        command += ["--js-runtimes", f"node:{node_runtime}"]
    if proxy:
        command += ["--proxy", proxy]
    command += list(args)

    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def fetch_metadata(
    ytdlp: str,
    url: str,
    proxy: Optional[str],
    node_runtime: Optional[str],
    timeout: int,
) -> Optional[Dict[str, Any]]:
    """抓取视频元数据（含字幕清单）。

    调用 `yt-dlp --dump-json`，一次拿到标题、频道、时长、章节、简介，以及
    `subtitles`（人工字幕）与 `automatic_captions`（自动字幕）两份语言清单。
    有了这两份清单，就能在不下载任何字幕的前提下判断「哪些语言可选、是人工还是自动」。

    参数:
        ytdlp:        yt-dlp 可执行文件路径。
        url:          规范化的视频链接。
        proxy:        代理 URL 或 None。
        node_runtime: Node 可执行文件路径或 None。
        timeout:      超时秒数。

    返回:
        解析成功返回元数据字典；失败返回 None。
    """
    result = run_ytdlp(
        ytdlp,
        ["--dump-json", "--skip-download", url],
        proxy,
        node_runtime,
        timeout,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def pick_best_subtitle(
    info: Dict[str, Any],
    prefer_lang: Optional[str],
) -> Optional[Dict[str, Any]]:
    """从元数据的两份字幕清单中挑选「最适合做总结」的那一条。

    挑选优先级（数字越小越优先）：
        1. 用户 --lang 指定语言的人工字幕
        2. 视频原语言的人工字幕
        3. 人工字幕里的第一条
        4. 用户 --lang 指定语言的自动字幕
        5. 视频原语言的自动字幕（通常是 `<lang>-orig`，即 ASR 原始转写，质量最好）
        6. 自动字幕里的 en
        7. 自动字幕里的第一条

    说明：之所以强烈优先「人工字幕」和「原语言」，是因为 YouTube 的机翻字幕
    （如 zh-Hans）本身是二次加工产物，用它做中文总结会出现「翻译的翻译」的误差累积。

    参数:
        info:        `--dump-json` 返回的元数据字典。
        prefer_lang: 用户指定的语言代码（如 "en" / "zh-Hans"），未指定为 None。

    返回:
        形如 {"lang": "en-orig", "kind": "auto"|"manual", "is_original": bool} 的字典；
        该视频完全没有字幕时返回 None。
    """
    manual: Dict[str, Any] = info.get("subtitles") or {}
    auto: Dict[str, Any] = info.get("automatic_captions") or {}
    original_lang: Optional[str] = info.get("language")

    # yt-dlp 并非总能提供 `language` 字段。此时可以借自动字幕里的 `<lang>-orig` 键反推：
    # 带 `-orig` 后缀的那条就是「原始语言自动转写」，其前缀即视频原语言。
    if not original_lang:
        for lang_key in auto:
            if lang_key.endswith("-orig"):
                original_lang = lang_key[: -len("-orig")]
                break

    def normalize(lang: str) -> str:
        """把 "en-US" 之类的区域化语言码归一为 "en"。"""
        return lang.split("-")[0].lower()

    def matches(lang: str, wanted: str) -> bool:
        """判断语言码是否匹配用户诉求（支持精确匹配与主语言匹配）。"""
        low = lang.lower()
        want = wanted.lower()
        return low == want or normalize(low) == normalize(want)

    def make(lang: str, kind: str) -> Dict[str, Any]:
        """构造候选字幕描述对象，并标注它是否为视频原语言。"""
        base = normalize(lang)
        is_original = (
            lang.endswith("-orig")
            or (original_lang is not None and normalize(original_lang) == base)
        )
        return {"lang": lang, "kind": kind, "is_original": is_original}

    # ---- 1) 用户指定语言的人工字幕 ----
    if prefer_lang:
        for lang in manual:
            if matches(lang, prefer_lang):
                return make(lang, "manual")

    # ---- 2) 视频原语言的人工字幕 ----
    if original_lang:
        for lang in manual:
            if matches(lang, original_lang):
                return make(lang, "manual")

    # ---- 3) 任意人工字幕 ----
    if manual:
        return make(next(iter(manual)), "manual")

    # ---- 4) 用户指定语言的自动字幕 ----
    if prefer_lang:
        for lang in auto:
            if matches(lang, prefer_lang):
                return make(lang, "auto")

    # ---- 5) 原语言的自动字幕（优先带 -orig 后缀者，即 ASR 原始转写）----
    for lang in auto:
        if lang.endswith("-orig"):
            return make(lang, "auto")
    if original_lang:
        for lang in auto:
            if matches(lang, original_lang):
                return make(lang, "auto")

    # ---- 6) 英文自动字幕 ----
    if "en" in auto:
        return make("en", "auto")
    for lang in auto:
        if normalize(lang) == "en":
            return make(lang, "auto")

    # ---- 7) 兜底：第一条自动字幕 ----
    if auto:
        return make(next(iter(auto)), "auto")

    return None


def download_subtitle(
    ytdlp: str,
    url: str,
    lang: str,
    workdir: Path,
    proxy: Optional[str],
    node_runtime: Optional[str],
    timeout: int,
) -> Optional[Path]:
    """下载指定语言的字幕文件到临时目录。

    使用 `--sub-format json3/vtt` 让 yt-dlp 优先取 json3（结构化、时间戳精确），
    无法提供 json3 时自动回退 vtt。`--sub-langs` 用锚定正则精确锁定单个语言，
    避免 yt-dlp 的前缀匹配顺带下载一堆无关语言。

    参数:
        ytdlp:        yt-dlp 可执行文件路径。
        url:          视频链接。
        lang:         目标字幕语言码（如 "en-orig"）。
        workdir:      临时工作目录。
        proxy:        代理 URL 或 None。
        node_runtime: Node 可执行文件路径或 None。
        timeout:      超时秒数。

    返回:
        下载到的字幕文件路径；下载失败返回 None。
    """
    # 先清掉上一次尝试可能留下的残留文件，否则「按文件是否落盘判断成功」的逻辑会误判
    video_id = extract_video_id(url) or "*"
    for stale in workdir.glob(f"{video_id}*"):
        try:
            stale.unlink()
        except OSError:
            pass

    result = run_ytdlp(
        ytdlp,
        [
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", f"^{re.escape(lang)}$",
            "--sub-format", "json3/vtt/srv3/ttml/srt",
            "-o", str(workdir / "%(id)s.%(ext)s"),
            url,
        ],
        proxy,
        node_runtime,
        timeout,
    )

    # 注意：yt-dlp 会在文件名里插入语言后缀（形如 `xxxxxxx.en-orig.json3`），
    # 而不是简单的 `xxxxxxx.json3`，因此必须用 glob 匹配，不能按固定名去找。
    # 按扩展名优先级（json3 优于 vtt）轮询，命中即返回。
    for extension in SUB_EXTENSIONS:
        exact = workdir / f"{video_id}.{lang}.{extension}"
        if exact.is_file() and exact.stat().st_size > 0:
            return exact
        matches = sorted(path for path in workdir.glob(f"{video_id}*.{extension}") if path.stat().st_size > 0)
        if matches:
            return matches[0]

    # yt-dlp 的退出码在部分字幕下载场景不可靠，因此以「文件是否落盘」为准
    if result.stderr.strip():
        log(f"字幕下载 stderr：{result.stderr.strip()[:300]}")
    return None


# ---------------------------------------------------------------------------
# 字幕解析
# ---------------------------------------------------------------------------

def parse_json3(path: Path) -> List[Dict[str, Any]]:
    """把 YouTube json3 字幕解析成「行」列表。

    实测 json3 存在**两种截然不同的排版模式**，必须分别处理：

    **模式 A —— 自动字幕（ASR）**：一个显示行被拆成多个 event 的 karaoke 片段，
    换行由专门的 `{"aAppend": true, "segs": [{"utf8": "\\n"}]}` 事件标记。
    例如英文自动字幕：`hi` / ` everyone` / ` so` … 是同一行的多个片段。
    此时必须**按 aAppend 事件分组**，否则会把整段内容粘成一坨。

    **模式 B —— 人工字幕 / 中文等语言**：每个 event 自带完整一行，**完全没有 aAppend 事件**。
    例如中文人工字幕的每个 event 就是一句「今天是我们论文精读系列的第三篇文章」。
    此时必须**把每个 event 当作独立一行**。

    判别方式：扫描是否存在 `aAppend` 事件。存在则走模式 A，否则走模式 B。
    （曾因只实现模式 A，导致中文视频 2195 行字幕被合并成 1 个段落。）

    参数:
        path: json3 文件路径。

    返回:
        行列表，每项形如 {"t": 起始秒(float), "dur": 时长(float), "text": 行文本}。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    events = payload.get("events") or []
    lines: List[Dict[str, Any]] = []

    def segments_to_text(event: Dict[str, Any]) -> str:
        """把一个 event 的所有 seg 片段拼成文本（跳过纯换行片段）。"""
        chunks: List[str] = []
        for segment in event.get("segs") or []:
            piece = segment.get("utf8") or ""
            if piece == "\n":
                continue
            chunks.append(piece)
        return "".join(chunks).strip()

    has_append_marker = any(event.get("aAppend") for event in events)

    if has_append_marker:
        # ---- 模式 A：按 aAppend 分组，组内片段直接拼接 ----
        buffer: List[str] = []
        start_ms = 0
        declared_duration = 0.0

        def flush() -> None:
            """把当前缓冲区中的片段合成为一行并追加到结果列表。"""
            text = "".join(buffer).strip()
            if text:
                lines.append({
                    "t": start_ms / 1000.0,
                    "dur": declared_duration / 1000.0,
                    "text": text,
                })
            buffer.clear()

        for event in events:
            segments = event.get("segs")
            if not segments:
                continue
            if event.get("aAppend"):
                # 新行开始：先结算上一行，再以本事件时间戳/时长作为新行的起点
                flush()
                start_ms = event.get("tStartMs", start_ms)
                declared_duration = float(event.get("dDurationMs") or 0.0)
            for segment in segments:
                piece = segment.get("utf8", "")
                if piece == "\n":
                    continue
                buffer.append(piece)
        flush()
        return lines

    # ---- 模式 B：每个 event 独立成行 ----
    for event in events:
        text = segments_to_text(event)
        if not text:
            continue
        lines.append({
            "t": float(event.get("tStartMs") or 0) / 1000.0,
            "dur": float(event.get("dDurationMs") or 0.0) / 1000.0,
            "text": text,
        })

    return lines


def parse_vtt(path: Path) -> List[Dict[str, Any]]:
    """把 WebVTT 字幕解析成「行」列表（json3 不可用时的兜底路径）。

    VTT 的麻烦在于「滚动重复」：同一句话会连续出现在多个 cue 里，直接拼接会大量重复。
    这里的去重策略是：逐 cue 解析出文本，若某个 cue 的文本等于「上一条已采纳文本」或以
    其结尾，则视为滚动残留而丢弃；否则采纳为新行。

    参数:
        path: vtt 文件路径。

    返回:
        行列表，每项形如 {"t": 起始秒(float), "text": 行文本}。
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    timestamp_pattern = re.compile(
        r"^(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
    )

    lines: List[Dict[str, Any]] = []
    current_start: Optional[float] = None
    current_text: List[str] = []
    last_text = ""

    def flush() -> None:
        """结算当前 cue：与上一条比较去重后追加为新行。"""
        nonlocal last_text
        if current_start is None:
            return
        text = normalize_whitespace(smart_join(current_text))
        current_text.clear()
        if not text:
            return
        # 若本 cue 文本与上一条完全相同，或就是上一条的末尾片段，则认定为滚动重复
        if text == last_text or (last_text and last_text.endswith(text)):
            return
        # 若本 cue 以「上一条文本 + 续写」的形式出现，只保留新增部分
        if last_text and text.startswith(last_text) and len(text) > len(last_text):
            text = text[len(last_text):].strip()
            if not text:
                return
        lines.append({"t": current_start, "text": text})
        last_text = text

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        match = timestamp_pattern.match(line)
        if match:
            flush()
            hours, minutes, seconds, millis = (int(match.group(i)) for i in range(1, 5))
            current_start = hours * 3600 + minutes * 60 + seconds + millis / 1000.0
            continue
        if not line or line.upper().startswith(("WEBVTT", "NOTE", "KIND:", "LANGUAGE:")):
            continue
        if line.isdigit():
            continue
        # 去掉 VTT 的行内标记，如 <00:00:01.000><c>文本</c>
        cleaned = re.sub(r"<[^>]+>", "", line)
        if cleaned.strip():
            current_text.append(cleaned.strip())

    flush()

    # 合并成行后，把被切成多段的同一句重新拼接（VTT 里一句话常跨多个 cue）
    merged: List[Dict[str, Any]] = []
    for item in lines:
        if merged:
            previous = merged[-1]
            # 前一行不以句末标点结尾、且当前片段很短时，认为它是断句残片，合并
            if not previous["text"].endswith((".", "?", "!", "。", "？", "！")) and len(item["text"]) < 60:
                previous["text"] = smart_join([previous["text"], item["text"]])
                continue
        merged.append(item)

    return merged


def parse_subtitle_file(path: Path) -> List[Dict[str, Any]]:
    """按文件扩展名分派到对应的解析器。

    参数:
        path: 字幕文件路径。

    返回:
        行列表；解析不出内容时返回空列表。
    """
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "json3":
        return parse_json3(path)
    if suffix in ("vtt", "srt", "srv1", "srv2", "srv3"):
        return parse_vtt(path)
    # 未知格式：先按 json3 试，再按 vtt 试
    parsed = parse_json3(path)
    return parsed if parsed else parse_vtt(path)


def build_paragraphs(lines: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把「行」合并成「段落」。

    切段规则（软条件优先，硬条件兜底）：
        a. 已积累到 PARA_TARGET_CHARS 且本行以句末标点结尾 → 断开（最自然的切点）
        b. 已积累到 PARA_TARGET_CHARS 且与下一行之间存在明显停顿 → 断开
        c. 已积累到 PARA_MAX_CHARS → 无条件断开（防止出现巨型段落）

    之所以以长度为主：YouTube 自动字幕的行是固定宽度的显示行，行间几乎不存在真实停顿，
    纯靠停顿切段会得到长度极不均衡的段落。人工字幕通常带标点，会大量命中规则 a，效果更好。

    收尾处理：把长度不足 PARA_MIN_CHARS 的碎片段并入前一段，避免出现大量残句。

    参数:
        lines: parse_* 返回的行列表（需带 dur 字段，见 attach_durations）。

    返回:
        段落列表，每项形如 {"t": 起始秒, "ts": "mm:ss", "text": 段落文本}。
    """
    if not lines:
        return []

    # 语言自适应：中文字符的信息密度远高于英文，同样字符数对应的「语音时长」更长，
    # 因此中文内容要把段落阈值按比例缩小，否则会切出长达四五分钟的段落。
    sample = "".join(line["text"] for line in lines[:200])
    cjk_ratio = count_cjk(sample) / max(1, len(sample))
    scale = PARA_CJK_SCALE if cjk_ratio > PARA_CJK_RATIO else 1.0
    target_chars = int(PARA_TARGET_CHARS * scale)
    max_chars = int(PARA_MAX_CHARS * scale)
    min_chars = int(PARA_MIN_CHARS * scale)

    paragraphs: List[Dict[str, Any]] = []
    buffer: List[str] = []
    para_start = lines[0]["t"]

    def flush() -> None:
        """把当前段落缓冲区结算为一个段落。"""
        text = normalize_whitespace(smart_join(buffer))
        buffer.clear()
        if text:
            paragraphs.append({"t": para_start, "ts": format_timestamp(para_start), "text": text})

    for index, line in enumerate(lines):
        if not buffer:
            para_start = line["t"]
        buffer.append(line["text"])

        current_length = len(smart_join(buffer))

        # 规则 c：长度硬上限
        if current_length >= max_chars:
            flush()
            continue

        if index + 1 >= len(lines) or current_length < target_chars:
            continue

        nxt = lines[index + 1]
        # 规则 a：句末标点（人工字幕的主要切点）
        if buffer[-1].rstrip().endswith((".", "?", "!", "。", "？", "！", "…")):
            flush()
            continue
        # 规则 b：明显停顿
        if gap_before(line, nxt) > PARA_GAP_SEC:
            flush()

    flush()

    # 收尾：合并过短的碎片段
    merged: List[Dict[str, Any]] = []
    for paragraph in paragraphs:
        if merged and len(paragraph["text"]) < min_chars:
            merged[-1]["text"] = smart_join([merged[-1]["text"], paragraph["text"]])
        else:
            merged.append(paragraph)
    return merged


def gap_before(line: Dict[str, Any], next_line: Dict[str, Any]) -> float:
    """计算「当前行播放结束」到「下一行开始」之间的空隙时长。

    自动字幕的 dDurationMs 常大于到下一行的实际间隔（滚动重叠），因此取其与间隔的较小值，
    保证结果非负且不会把连续语音误判成停顿。

    参数:
        line:      当前行（需带 dur 字段）。
        next_line: 下一行。

    返回:
        空隙时长（秒），最小为 0。
    """
    to_next = max(0.0, next_line["t"] - line["t"])
    declared = float(line.get("dur") or 0.0)
    if declared <= 0:
        return 0.0
    return max(0.0, to_next - min(declared, to_next))


def attach_durations(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为缺少 dur 字段的行补上「到下一行的时间跨度」，使各解析器产出保持结构一致。

    注意：不覆盖解析器已提供的真实时长（json3 的 dDurationMs），仅在缺失时补齐。

    参数:
        lines: 行列表（原地修改）。

    返回:
        同一个列表对象。
    """
    for index, line in enumerate(lines):
        if not line.get("dur"):
            if index + 1 < len(lines):
                line["dur"] = max(0.0, lines[index + 1]["t"] - line["t"])
            else:
                line["dur"] = 0.0
    return lines


# ---------------------------------------------------------------------------
# 输出组装
# ---------------------------------------------------------------------------

def chunk_paragraphs(
    paragraphs: Sequence[Dict[str, Any]],
    max_chars: int,
) -> List[Dict[str, Any]]:
    """把段落列表按字符预算切分成若干「块」，供上层做 map-reduce 式总结。

    当视频很长（例如 2 小时以上）时，转写文本可能超出单次可处理的上下文；
    此时把文本切成若干块，让上层先分块摘要、再汇总，避免信息被截断丢弃。

    参数:
        paragraphs: 段落列表。
        max_chars:  单块字符上限。

    返回:
        块列表，每项形如：
        {"index": 1, "t_start": 0, "t_end": 600, "ts_start": "00:00",
         "ts_end": "10:00", "char_count": 5000, "text": "…"}
    """
    chunks: List[Dict[str, Any]] = []
    buffer: List[str] = []
    buffer_chars = 0
    start_paragraph: Optional[Dict[str, Any]] = None
    end_paragraph: Optional[Dict[str, Any]] = None

    def flush() -> None:
        """把当前缓冲区结算为一个块。"""
        nonlocal buffer, buffer_chars, start_paragraph, end_paragraph
        if not buffer or start_paragraph is None or end_paragraph is None:
            buffer = []
            buffer_chars = 0
            return
        chunks.append({
            "index": len(chunks) + 1,
            "t_start": int(start_paragraph["t"]),
            "t_end": int(end_paragraph["t"]),
            "ts_start": start_paragraph["ts"],
            "ts_end": end_paragraph["ts"],
            "char_count": buffer_chars,
            "text": "\n\n".join(buffer),
        })
        buffer = []
        buffer_chars = 0
        start_paragraph = None
        end_paragraph = None

    for paragraph in paragraphs:
        if start_paragraph is None:
            start_paragraph = paragraph
        buffer.append(paragraph["text"])
        buffer_chars += len(paragraph["text"])
        end_paragraph = paragraph
        if buffer_chars >= max_chars:
            flush()

    flush()
    return chunks


def humanize_duration(seconds: Optional[float]) -> str:
    """把秒数转成 "1小时23分45秒" 这类便于阅读的中文时长描述。

    参数:
        seconds: 秒数；None 或非正数时返回 "未知"。

    返回:
        中文时长字符串。
    """
    if not seconds or seconds <= 0:
        return "未知"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: List[str] = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if secs and not hours:
        parts.append(f"{secs}秒")
    return "".join(parts) or "0秒"


def build_output(
    info: Dict[str, Any],
    subtitle_meta: Dict[str, Any],
    paragraphs: List[Dict[str, Any]],
    max_chars: int,
    make_chunks: bool,
    include_full_text: bool = False,
) -> Dict[str, Any]:
    """组装最终输出给上层的 JSON。

    输出结构：
        {
          "ok": true,
          "video": {id, url, title, channel, channel_url, duration_sec, duration_human,
                    upload_date, view_count, like_count, description, chapters, thumbnail},
          "subtitle": {lang, kind, is_original, paragraph_count, char_count, est_tokens,
                       other_available_languages, other_languages_truncated},
          "transcript": {
              "full_text": "…",                        # 仅 --full-text 时输出
              "paragraphs": [{t, ts, text}, …]         # 未分块时带 text；分块时只留 {t, ts} 时间轴
          },
          "chunks": [{index, t_start, t_end, ts_start, ts_end, char_count, text}, …],
          "chunked": true|false,                       # true 表示上层应走 map-reduce 总结
          "link_template": "https://youtu.be/{video_id}?t={seconds}"
        }

    关于 `full_text`：默认**不输出**。因为它与 `paragraphs` 是同一份正文，
    同时输出会让 JSON 体积白白翻倍（实测 2 小时视频从 135KB 涨到 270KB）。
    `paragraphs` 本身已包含全部文本且带时间戳，信息量严格更优。

    参数:
        info:              yt-dlp 的元数据字典。
        subtitle_meta:     pick_best_subtitle 选出的字幕描述。
        paragraphs:        段落列表。
        max_chars:         分块阈值（超过则启用分块模式）。
        make_chunks:       是否允许分块（--no-chunks 时为 False）。
        include_full_text: 是否额外输出拼接好的全文。

    返回:
        可直接 json.dumps 的字典。
    """
    video_id: str = info.get("id") or ""
    duration = info.get("duration")
    full_text = "\n\n".join(paragraph["text"] for paragraph in paragraphs)
    char_count = len(full_text)

    # 章节信息（并非所有视频都有）；有的话对总结的结构化帮助极大
    chapters: List[Dict[str, Any]] = []
    for chapter in (info.get("chapters") or []):
        start = chapter.get("start_time")
        end = chapter.get("end_time")
        if start is None:
            continue
        chapters.append({
            "title": chapter.get("title") or "",
            "t": int(start),
            "ts": format_timestamp(start),
            "t_end": int(end) if end is not None else None,
            "link": deep_link(video_id, start),
        })

    # 该视频还提供哪些语言的字幕（供上层在需要时做交叉校验）。
    # 只截取前若干项 —— 有些热门视频有 100+ 种语言，全列出来纯属浪费 token。
    all_languages = sorted(
        set(list((info.get("subtitles") or {}).keys()) + list((info.get("automatic_captions") or {}).keys()))
    )
    selected_lang = subtitle_meta.get("lang", "")
    other_languages = [lang for lang in all_languages if lang != selected_lang]
    other_languages_truncated = len(other_languages) > MAX_OTHER_LANGUAGES
    other_languages = other_languages[:MAX_OTHER_LANGUAGES]

    output: Dict[str, Any] = {
        "ok": True,
        "video": {
            "id": video_id,
            "url": canonical_url(video_id) if video_id else info.get("webpage_url", ""),
            "title": info.get("title") or "",
            "channel": info.get("channel") or info.get("uploader") or "",
            "channel_url": info.get("channel_url") or info.get("uploader_url") or "",
            "duration_sec": int(duration) if isinstance(duration, (int, float)) else None,
            "duration_human": humanize_duration(duration if isinstance(duration, (int, float)) else None),
            "upload_date": _normalize_upload_date(info.get("upload_date")),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "description": (info.get("description") or "")[:MAX_DESCRIPTION_CHARS],
            "chapters": chapters,
            "thumbnail": info.get("thumbnail") or "",
        },
        "subtitle": {
            "lang": selected_lang,
            "kind": subtitle_meta.get("kind"),
            "is_original": bool(subtitle_meta.get("is_original")),
            "paragraph_count": len(paragraphs),
            "char_count": char_count,
            "est_tokens": estimate_tokens(full_text),
            "other_available_languages": other_languages,
            "other_languages_truncated": other_languages_truncated,
        },
        "transcript": {
            "full_text": full_text if include_full_text else "",
            "paragraphs": paragraphs,
        },
        "link_template": "https://youtu.be/{video_id}?t={seconds}",
    }

    if make_chunks and char_count > max_chars:
        output["chunks"] = chunk_paragraphs(paragraphs, CHUNK_SIZE_CHARS)
        output["chunked"] = True
        # 分块时正文只保留在 chunks 里。transcript 部分退化成「纯时间轴索引」——
        # 因为 chunks 本来就是按段落边界切出来的，若两边都存文本，JSON 体积会白白翻倍。
        output["transcript"]["full_text"] = ""
        output["transcript"]["paragraphs"] = [
            {"t": paragraph["t"], "ts": paragraph["ts"]} for paragraph in paragraphs
        ]
    else:
        output["chunked"] = False

    return output


def _normalize_upload_date(raw: Optional[str]) -> Optional[str]:
    """把 yt-dlp 的 "YYYYMMDD" 日期字符串转成 "YYYY-MM-DD"。

    参数:
        raw: 形如 "20240222" 的字符串；可能为 None。

    返回:
        形如 "2024-02-22" 的字符串；无法解析时原样返回。
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(raw)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def list_subtitles(
    ytdlp: str,
    url: str,
    proxies: Sequence[Optional[str]],
    node_runtime: Optional[str],
    timeout: int,
) -> int:
    """列出某视频所有可选字幕语言（供 `--list-subs` 使用）。

    参数:
        ytdlp:       yt-dlp 路径。
        url:         视频链接。
        proxies:     代理候选列表。
        node_runtime: Node 路径。
        timeout:     超时秒数。

    返回:
        进程退出码。
    """
    info, _ = fetch_with_retry(ytdlp, url, proxies, node_runtime, timeout)
    if info is None:
        fatal("无法获取视频信息（网络不通或视频不可访问）", 3)

    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    print(f"视频：{info.get('title')}")
    print(f"频道：{info.get('channel')}")
    print(f"时长：{humanize_duration(info.get('duration'))}")
    print("")
    print(f"人工字幕（{len(manual)} 种）：")
    for lang in sorted(manual):
        print(f"  - {lang}")
    if not manual:
        print("  （无）")
    print("")
    print(f"自动字幕（{len(auto)} 种，仅显示前 30）：")
    for lang in sorted(auto)[:30]:
        print(f"  - {lang}")
    return 0


def fetch_with_retry(
    ytdlp: str,
    url: str,
    proxies: Sequence[Optional[str]],
    node_runtime: Optional[str],
    timeout: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """依次尝试各个代理候选，直到成功拿到视频元数据。

    参数:
        ytdlp:       yt-dlp 路径。
        url:         视频链接。
        proxies:     代理候选列表（末尾为 None 表示直连）。
        node_runtime: Node 路径。
        timeout:     超时秒数。

    返回:
        二元组 (元数据字典, 生效的代理)。
        成功后一个元素是**已验证可用**的代理 —— 后续下载字幕时应复用它，
        避免对已知失效的候选重复超时重试（实测这会把整体耗时从十几秒拉到两分钟以上）。
        全部候选都失败时返回 (None, None)。
    """
    last_error = ""
    for proxy in proxies:
        label = proxy or "直连"
        log(f"尝试通过 {label} 获取视频信息…")
        try:
            info = fetch_metadata(ytdlp, url, proxy, node_runtime, timeout)
        except subprocess.TimeoutExpired:
            last_error = f"{label} 超时"
            log(last_error)
            continue
        if info:
            log(f"{label} 成功")
            return info, proxy
        last_error = f"{label} 失败"
        log(last_error)

    if last_error:
        log(f"全部代理候选均失败，最后一次：{last_error}")
    return None, None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """脚本入口：解析参数 → 探测代理 → 抓字幕 → 解析 → 输出 JSON。

    参数:
        argv: 命令行参数列表；None 表示取 sys.argv[1:]。

    返回:
        进程退出码（0 成功，其余见模块 docstring）。
    """
    parser = argparse.ArgumentParser(
        description="抓取 YouTube 视频字幕并输出结构化 JSON，供中文总结使用。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="YouTube 视频链接或 11 位视频 ID")
    parser.add_argument("--lang", default=None, help="指定字幕语言，如 en / zh-Hans / ja")
    parser.add_argument("--out", default=None, help="把 JSON 写入指定文件（默认输出到 stdout）")
    parser.add_argument("--paragraph-only", action="store_true", help="只输出段落纯文本（调试用）")
    parser.add_argument("--full-text", action="store_true", help="额外输出拼接好的全文（默认不输出，避免 JSON 翻倍）")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="单块最大字符数")
    parser.add_argument("--no-chunks", action="store_true", help="强制不分块")
    parser.add_argument("--list-subs", action="store_true", help="只列出可选字幕语言")
    parser.add_argument("--keep-files", action="store_true", help="保留下载的字幕原始文件")
    parser.add_argument("--timeout", type=int, default=60, help="单步网络操作超时（秒）")
    parser.add_argument("--proxy", default=None, help="显式指定代理，如 http://127.0.0.1:7890")
    args = parser.parse_args(argv)

    video_id = extract_video_id(args.url)
    if not video_id:
        fatal(f"无法从输入中识别 YouTube 视频 ID：{args.url}", 2)
    url = canonical_url(video_id)

    ytdlp = find_ytdlp()
    node_runtime = find_node_runtime()
    proxies = resolve_proxy_candidates(args.proxy)

    if args.list_subs:
        return list_subtitles(ytdlp, url, proxies, node_runtime, args.timeout)

    # --- 1) 取元数据与字幕清单 ---
    info, working_proxy = fetch_with_retry(ytdlp, url, proxies, node_runtime, args.timeout)
    if info is None:
        fatal(
            "无法获取视频信息。可能原因：网络/代理不通、视频为私有或已删除、需要登录验证。",
            4,
        )

    # --- 2) 选字幕 ---
    subtitle_meta = pick_best_subtitle(info, args.lang)
    if not subtitle_meta:
        fatal(
            f"该视频没有任何可用字幕（包括自动字幕）：{info.get('title')}",
            5,
        )
    log(f"选中字幕：{subtitle_meta['lang']}（{'人工' if subtitle_meta['kind'] == 'manual' else '自动'}）")

    # --- 3) 下载字幕 ---
    # 优先复用「刚刚验证过可用」的代理；只有它下载失败时才回退到其余候选。
    workdir = Path(tempfile.mkdtemp(prefix="ytsub-"))
    download_candidates: List[Optional[str]] = []
    if working_proxy is not None:
        download_candidates.append(working_proxy)
    for candidate_proxy in proxies:
        if candidate_proxy != working_proxy:
            download_candidates.append(candidate_proxy)

    subtitle_path: Optional[Path] = None
    subtitle_proxy: Optional[str] = None
    try:
        for proxy in download_candidates:
            try:
                found = download_subtitle(
                    ytdlp, url, subtitle_meta["lang"], workdir, proxy, node_runtime, args.timeout
                )
            except subprocess.TimeoutExpired:
                continue
            if found:
                subtitle_path = found
                subtitle_proxy = proxy
                break

        if subtitle_path is None:
            fatal("字幕文件下载失败（可能是网络问题，或该语言字幕已失效）", 3)

        log(f"字幕文件：{subtitle_path.name}（{(subtitle_path.stat().st_size / 1024):.1f} KB）")

        # --- 4) 解析 ---
        lines = attach_durations(parse_subtitle_file(subtitle_path))
        if not lines:
            fatal("字幕文件解析后内容为空", 5)

        paragraphs = build_paragraphs(lines)

        if args.paragraph_only:
            for paragraph in paragraphs:
                print(f"[{paragraph['ts']}] {paragraph['text']}")
            return 0

        output = build_output(
            info=info,
            subtitle_meta=subtitle_meta,
            paragraphs=paragraphs,
            max_chars=args.max_chars,
            make_chunks=not args.no_chunks,
            include_full_text=args.full_text,
        )
        output["subtitle"]["fetched_via_proxy"] = subtitle_proxy

        payload = json.dumps(output, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(payload, encoding="utf-8")
            log(f"已写入 {args.out}")
            summary = {
                "ok": True,
                "video": output["video"]["title"],
                "subtitle": output["subtitle"],
                "chunked": output["chunked"],
                "out": str(Path(args.out).resolve()),
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(payload)
        return 0

    finally:
        if not args.keep_files:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            log(f"字幕原始文件保留于 {workdir}")


if __name__ == "__main__":
    sys.exit(main())
