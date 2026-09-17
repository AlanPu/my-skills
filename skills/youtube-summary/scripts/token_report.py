#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
token_report.py — 统计一次 skill 调用消耗了多少 token

为什么需要两种口径
------------------
agent 平台通常**在每一轮对话结束后**才把执行记录（trace）落盘，所以 skill
在「输出总结的那一瞬间」是读不到自己这轮精确消耗的 —— 那时记录还不存在。
因此本脚本提供两种模式：

1. `estimate` —— **估算**，skill 执行过程中随时可用。
   原理：不猜，而是用**可精确测量的文件体积**推算。读进上下文的转写 JSON 有多大、
   生成的总结有多大，这些都是确定的；不确定的只有「推理开销」和「多轮往返放大」，
   这两项用一个实测标定的系数吸收。

2. `actual` —— **实测**，从执行记录里读精确值。
   代价是**滞后一轮**：只能在 skill 跑完之后的下一轮对话里查询。

模式说明
--------
    token_report.py estimate --transcript FILE [--summary FILE]
        估算本次调用消耗。--transcript 是 fetch_transcript.py 产出的 JSON，
        --summary 是生成的中文总结（可选，缺省时按经验比例推算）。

    token_report.py actual [--trace FILE] [--last N]
        从执行记录读精确消耗。默认自动定位当前会话最新的一条记录。

    token_report.py list [--limit N]
        列出当前会话最近的执行记录，便于挑选。

退出码
------
    0 成功 / 2 参数错误 / 3 找不到所需文件或记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 复用 fetch_transcript 里的分词估算，保证整个 skill 口径一致
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from fetch_transcript import count_cjk, estimate_tokens  # noqa: E402
except ImportError:  # 允许脚本被单独复制出去使用
    def count_cjk(text: str) -> int:  # type: ignore[misc]
        """回退实现：统计东亚文字字符数。"""
        ranges = ((0x3000, 0x303F), (0x3040, 0x30FF), (0x3400, 0x4DBF),
                  (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF))
        return sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in ranges))

    def estimate_tokens(text: str) -> int:  # type: ignore[misc]
        """回退实现：中文约 1 字符/token，其余约 3.5 字符/token。"""
        cjk = count_cjk(text)
        return int(cjk / 1.0 + max(0, len(text) - cjk) / 3.5)


# ---------------------------------------------------------------------------
# 标定常数（来自一次真实调用的实测数据，见 README）
# ---------------------------------------------------------------------------

#: 实测标定：JSON 文本的字符-token 比。一次真实调用中，带行号的转写 JSON
#: 共 14,184 字符，实测占 3,937 token，即 3.60 字符/token。
JSON_CHARS_PER_TOKEN = 3.60

#: 实测标定：总结文本每 1 token，在整个 agent loop 里会连带产生约 3.7 token 的总开销
#: （生成它 + 下一轮回灌它 + 围绕它产生的推理）。单点标定，误差约 ±20%。
SUMMARY_OVERHEAD_MULTIPLIER = 3.7

#: 固定开销：指令、工具调用结果、收尾轮等，与视频长度基本无关。
#: 实测一次 4 轮的小任务中，这部分约 1,600 token。
FIXED_OVERHEAD_TOKENS = 1_600

#: 未提供 --summary 时，用转写 token 数推算总结大小的经验比例（实测约 0.7）。
SUMMARY_TO_TRANSCRIPT_RATIO = 0.7

#: WorkBuddy 的执行记录与运行进程信息所在目录（其他平台可能不存在，
#: 此时只有 estimate 模式可用 —— 那也正是主路径）。
WORKBUDDY_HOME = Path.home() / ".workbuddy"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def humanize(number: float) -> str:
    """把 token 数格式化成便于阅读的形式（1,234 / 12.3K / 1.23M）。

    参数:
        number: 原始数值。

    返回:
        格式化后的字符串。
    """
    value = int(round(number))
    if value < 10_000:
        return f"{value:,}"
    if value < 1_000_000:
        return f"{value / 1000:.1f}K"
    return f"{value / 1_000_000:.2f}M"


def count_file_tokens(path: Path, with_line_numbers: bool = False) -> int:
    """统计一个文件「进入模型上下文时」大约是多少 token。

    注意这里不是简单地对原文分词：若该文件是被 Read 工具读入的，上下文里实际是
    **带行号前缀**的版本，会比原文件略大，因此 with_line_numbers 为真时要先把行号拼回去。

    参数:
        path:              文件路径。
        with_line_numbers: 是否模拟 Read 工具加上行号前缀（形如 "   123→"）。

    返回:
        估算的 token 数；文件不存在或读取失败时返回 0。
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if with_line_numbers:
        text = "\n".join(f"{index:>6}→{line}" for index, line in enumerate(raw.split("\n"), 1))
        return int(len(text) / JSON_CHARS_PER_TOKEN)
    return estimate_tokens(raw)


def find_current_session_id() -> Optional[str]:
    """定位当前正在运行的那个会话 ID。

    做法：扫描 `~/.workbuddy/sessions/*.json`，取 `lastHeartbeat` 最新的那条 ——
    心跳最新意味着那就是当前活跃的会话。

    参数:
        无。

    返回:
        会话 ID 字符串；找不到时返回 None。
    """
    sessions_dir = WORKBUDDY_HOME / "sessions"
    if not sessions_dir.is_dir():
        return None
    best: Optional[Tuple[int, str]] = None
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        heartbeat = data.get("lastHeartbeat")
        session_id = data.get("sessionId")
        if isinstance(heartbeat, int) and session_id:
            if best is None or heartbeat > best[0]:
                best = (heartbeat, session_id)
    return best[1] if best else None


def load_trace(path: Path) -> Optional[Dict[str, Any]]:
    """读取并解析一个执行记录（trace）文件。

    参数:
        path: trace 文件路径。

    返回:
        解析后的字典；失败时返回 None。
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def extract_rounds(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 trace 里抽出每一轮模型调用的 token 用量。

    数据藏在 `spans[]` 中 `type == "generation"` 的条目里：它们的 `toolOutput`
    是一段 **JSON 字符串**（chat.completion 响应），解析后取 `[0].usage`。

    参数:
        trace: load_trace 返回的字典。

    返回:
        列表，每项形如
        {"time": "12:25:44", "prompt": 146017, "cached": 142080,
         "output": 4748, "reasoning": 3190}。
    """
    rounds: List[Dict[str, Any]] = []
    for span in trace.get("spans") or []:
        if span.get("type") != "generation":
            continue
        raw = span.get("toolOutput")
        payload = None
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
        elif isinstance(raw, list):
            payload = raw
        usage: Dict[str, Any] = {}
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            usage = payload[0].get("usage") or {}
        elif isinstance(payload, dict):
            usage = payload.get("usage") or {}
        if not usage:
            continue
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        rounds.append({
            "time": (span.get("startedAt") or "")[11:19],
            "prompt": int(usage.get("prompt_tokens") or 0),
            "cached": int(prompt_details.get("cached_tokens") or 0),
            "output": int(usage.get("completion_tokens") or 0),
            "reasoning": int(completion_details.get("reasoning_tokens") or 0),
        })
    rounds.sort(key=lambda item: item["time"])
    return rounds


def locate_traces(session_id: Optional[str]) -> List[Path]:
    """找出属于指定会话的执行记录文件，按修改时间倒序（最新的在前）。

    参数:
        session_id: 会话 ID；为 None 时返回所有记录（仍按时间倒序）。

    返回:
        trace 文件路径列表。
    """
    traces_root = WORKBUDDY_HOME / "traces"
    if not traces_root.is_dir():
        return []

    matched: List[Tuple[float, Path]] = []
    for path in traces_root.glob("*/trace_*.json"):
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        # 先按修改时间排好序，再逐个校验 sessionId（避免解析全部大文件）
        matched.append((modified, path))
    matched.sort(key=lambda item: item[0], reverse=True)

    if session_id is None:
        return [path for _, path in matched]

    result: List[Path] = []
    checked = 0
    for _, path in matched:
        # 大文件全量解析，只挑最新的若干个做会话校验，避免无谓开销
        if checked >= 12:
            break
        checked += 1
        trace = load_trace(path)
        if trace and (trace.get("trace") or {}).get("sessionId") == session_id:
            result.append(path)
    return result


# ---------------------------------------------------------------------------
# estimate 模式
# ---------------------------------------------------------------------------

def command_estimate(args: argparse.Namespace) -> int:
    """估算一次 skill 调用的 token 消耗。

    参数:
        args: 含 transcript / summary 路径的命令行参数。

    返回:
        进程退出码。
    """
    transcript_path = Path(args.transcript)
    if not transcript_path.is_file():
        print(f"错误：找不到转写文件 {transcript_path}", file=sys.stderr)
        return 3

    # 转写 JSON 是被 Read 工具读进去的，所以要按「带行号」的口径算
    transcript_tokens = count_file_tokens(transcript_path, with_line_numbers=True)

    summary_tokens: Optional[int] = None
    summary_source = ""
    if args.summary:
        summary_path = Path(args.summary)
        if summary_path.is_file():
            summary_tokens = count_file_tokens(summary_path, with_line_numbers=False)
            summary_source = "（实测文件）"
        else:
            print(f"提示：找不到总结文件 {summary_path}，改为按经验比例推算。", file=sys.stderr)

    if summary_tokens is None:
        summary_tokens = int(transcript_tokens * SUMMARY_TO_TRANSCRIPT_RATIO)
        summary_source = "（按转写比例推算）"

    summary_overhead = int(summary_tokens * SUMMARY_OVERHEAD_MULTIPLIER)
    total = transcript_tokens + summary_overhead + FIXED_OVERHEAD_TOKENS

    # 估算误差：固定开销与推理开销都是经验值，给一个区间更诚实
    low = int(total * 0.85)
    high = int(total * 1.20)

    if args.json:
        print(json.dumps({
            "mode": "estimate",
            "transcript_tokens": transcript_tokens,
            "summary_tokens": summary_tokens,
            "summary_tokens_source": summary_source,
            "overhead_tokens": summary_overhead + FIXED_OVERHEAD_TOKENS,
            "total_tokens": total,
            "range_low": low,
            "range_high": high,
            "note": "估算值；精确值需在下一轮用 actual 模式读取执行记录",
        }, ensure_ascii=False, indent=2))
        return 0

    print("📊 本次消耗（估算）")
    print(f"  转写进上下文      {humanize(transcript_tokens):>9}  tokens")
    print(f"  总结生成与往返    {humanize(summary_overhead):>9}  tokens{summary_source}")
    print(f"  固定开销          {humanize(FIXED_OVERHEAD_TOKENS):>9}  tokens")
    print(f"  ── 合计            {humanize(total):>9}  tokens   （区间 {humanize(low)} – {humanize(high)}）")
    return 0


# ---------------------------------------------------------------------------
# actual 模式
# ---------------------------------------------------------------------------

def command_actual(args: argparse.Namespace) -> int:
    """从执行记录里读取精确的 token 消耗。

    参数:
        args: 含 trace / last 的命令行参数。

    返回:
        进程退出码。
    """
    if args.trace:
        candidates = [Path(args.trace)]
    else:
        session_id = find_current_session_id()
        candidates = locate_traces(session_id)
        if not candidates:
            print("错误：找不到本会话的执行记录。", file=sys.stderr)
            print("      执行记录在每轮对话结束后才落盘，若刚跑完 skill 请稍后再试；", file=sys.stderr)
            print("      也可以用 estimate 模式拿到估算值。", file=sys.stderr)
            return 3

    trace_path = candidates[args.last - 1] if args.last - 1 < len(candidates) else candidates[0]
    trace = load_trace(trace_path)
    if not trace:
        print(f"错误：无法解析执行记录 {trace_path}", file=sys.stderr)
        return 3

    meta = trace.get("trace") or {}
    rounds = extract_rounds(trace)
    if not rounds:
        print(f"错误：执行记录里没有可用的模型调用数据 {trace_path}", file=sys.stderr)
        return 3

    prompt = sum(r["prompt"] for r in rounds)
    cached = sum(r["cached"] for r in rounds)
    output = sum(r["output"] for r in rounds)
    reasoning = sum(r["reasoning"] for r in rounds)
    fresh_input = prompt - cached

    started = (meta.get("startedAt") or "")[11:19]
    ended = (meta.get("endedAt") or "")[11:19]

    if args.json:
        print(json.dumps({
            "mode": "actual",
            "trace": str(trace_path),
            "window": f"{started} → {ended}",
            "rounds": len(rounds),
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "fresh_input_tokens": fresh_input,
            "output_tokens": output,
            "reasoning_tokens": reasoning,
            "billable_total": fresh_input + output,
            "listed_total": prompt + output,
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"📊 实际消耗（执行记录 {started} → {ended}）")
    print(f"  模型调用轮次      {len(rounds):>9}  轮")
    print(f"  输入（累计）      {humanize(prompt):>9}  tokens")
    print(f"  ├ 命中缓存        {humanize(cached):>9}  tokens  ({100 * cached / max(1, prompt):.1f}%)")
    print(f"  └ 实际新增        {humanize(fresh_input):>9}  tokens")
    print(f"  模型输出          {humanize(output):>9}  tokens  (含推理 {humanize(reasoning)})")
    print(f"  ── 实际计费量      {humanize(fresh_input + output):>9}  tokens")
    print()
    print(f"  注：输入的累计值 {humanize(prompt)} 含每轮重发的完整上下文，绝大多数命中缓存，")
    print("      不代表真实开销；真实开销以「实际计费量」为准。")
    return 0


# ---------------------------------------------------------------------------
# list 模式
# ---------------------------------------------------------------------------

def command_list(args: argparse.Namespace) -> int:
    """列出当前会话最近的执行记录。

    参数:
        args: 含 limit 的命令行参数。

    返回:
        进程退出码。
    """
    session_id = find_current_session_id()
    candidates = locate_traces(session_id)
    if not candidates:
        print("找不到本会话的执行记录。", file=sys.stderr)
        return 3

    print(f"当前会话：{session_id or '(未知)'}")
    print(f"{'序号':<5}{'时间范围':<24}{'轮次':<7}{'实际计费':<12}{'文件'}")
    for index, path in enumerate(candidates[: args.limit], start=1):
        trace = load_trace(path)
        if not trace:
            continue
        meta = trace.get("trace") or {}
        rounds = extract_rounds(trace)
        if not rounds:
            continue
        billable = sum(r["prompt"] - r["cached"] + r["output"] for r in rounds)
        window = f"{(meta.get('startedAt') or '')[11:19]} → {(meta.get('endedAt') or '')[11:19]}"
        print(f"{index:<5}{window:<24}{len(rounds):<7}{humanize(billable):<12}{path.name[:28]}")
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """脚本入口：按子命令分派。

    参数:
        argv: 命令行参数；None 表示取 sys.argv[1:]。

    返回:
        进程退出码。
    """
    parser = argparse.ArgumentParser(
        description="统计一次 skill 调用消耗了多少 token。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  token_report.py estimate --transcript /tmp/yt-summary.json\n"
            "  token_report.py estimate --transcript /tmp/yt.json --summary /tmp/sum.md\n"
            "  token_report.py actual\n"
            "  token_report.py list --limit 5\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    estimate_parser = subparsers.add_parser("estimate", help="估算本次调用消耗（随时可用）")
    estimate_parser.add_argument("--transcript", required=True, help="fetch_transcript.py 产出的 JSON 路径")
    estimate_parser.add_argument("--summary", default=None, help="生成的中文总结文件路径（可选）")
    estimate_parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    estimate_parser.set_defaults(func=command_estimate)

    actual_parser = subparsers.add_parser("actual", help="读取精确消耗（滞后一轮）")
    actual_parser.add_argument("--trace", default=None, help="指定执行记录文件")
    actual_parser.add_argument("--last", type=int, default=1, help="取倒数第 N 条记录（默认 1）")
    actual_parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    actual_parser.set_defaults(func=command_actual)

    list_parser = subparsers.add_parser("list", help="列出最近的执行记录")
    list_parser.add_argument("--limit", type=int, default=8, help="最多列出几条（默认 8）")
    list_parser.set_defaults(func=command_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
