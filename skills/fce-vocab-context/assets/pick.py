#!/usr/bin/env python3
"""从 FCE 词汇实战示范文技能的体裁库里随机抽一个「还没用过的」体裁。

为什么要有这个脚本：人工挑体裁会不自觉地总落在同一个（总是 biography），
用真随机数抽签才能保证体裁确实轮换开。

用法：
    python3 pick.py             # 随机抽一个未用体裁，并打印词表黑名单
    python3 pick.py --peek      # 只看已用 / 未用清单，不抽签
    python3 pick.py --avoid     # 只打印必须避开的旧词条
    python3 pick.py --genre place   # 指定体裁，只取它的说明书与黑名单

退出码：
    0  正常
    1  所有体裁都用过了（需要先扩充 genres.md 的体裁库）
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
GENRES_MD = SKILL_ROOT / "references" / "genres.md"
STATE_JSON = SKILL_ROOT / "references" / "state.json"

# genres.md 里每个体裁判卡的标题格式：## G1 biography —— 人物传记
CARD_RE = re.compile(r"^##\s+(G\d+)\s+(\w+)\s+——\s+(.+?)\s*$", re.M)


def load_genre_ids():
    """从 genres.md 末尾的 GENRE-IDS 标记块读出全部体裁 id（保持书写顺序）。"""
    if not GENRES_MD.exists():
        sys.exit(f"找不到体裁库文件：{GENRES_MD}")
    text = GENRES_MD.read_text(encoding="utf-8")
    block = re.search(r"<!--\s*GENRE-IDS\s*(.*?)\s*-->", text, re.S)
    if not block:
        sys.exit("genres.md 里找不到 <!-- GENRE-IDS ... --> 标记块")
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def load_genre_names():
    """返回 (体裁 id -> 中文名, 体裁 id -> 卡片编号)，用于输出可读的抽签结果。"""
    text = GENRES_MD.read_text(encoding="utf-8")
    names, numbers = {}, {}
    for gnum, gid, name in CARD_RE.findall(text):
        names[gid] = name
        numbers[gid] = gnum
    return names, numbers


def load_state():
    if not STATE_JSON.exists():
        return {"delivered": []}
    return json.loads(STATE_JSON.read_text(encoding="utf-8"))


def collect_used(state):
    """返回 (已用体裁列表, 已用词条集合)。"""
    delivered = state.get("delivered", [])
    used_genres = [d.get("genre", "") for d in delivered]
    used_terms = set()
    for item in delivered:
        used_terms.update(item.get("terms", []))
        # 容错：万一某条只填了 coreWords / phrases
        used_terms.update(item.get("coreWords", []))
        used_terms.update(item.get("phrases", []))
    return used_genres, used_terms


def print_avoid(used_terms):
    print(f"【词表黑名单】以下 {len(used_terms)} 个词条已在旧篇目用过，本篇禁止重复：")
    for term in sorted(used_terms):
        print(f"  - {term}")
    print("\n提示：新篇的 12 个词条与上面任何一条都不能相同（含同义替换要谨慎，"
          "例如已用 dedicate oneself to，就别再用 devote oneself to 当核心词条）。")


def main():
    parser = argparse.ArgumentParser(description="随机抽一个未用过的 FCE 短文体裁")
    parser.add_argument("--peek", action="store_true", help="只列出已用/未用清单")
    parser.add_argument("--avoid", action="store_true", help="只打印词表黑名单")
    parser.add_argument("--genre", metavar="ID", help="指定体裁，不随机抽")
    args = parser.parse_args()

    all_ids = load_genre_ids()
    names, numbers = load_genre_names()
    state = load_state()
    used_genres, used_terms = collect_used(state)
    unused = [g for g in all_ids if g not in used_genres]

    if args.avoid:
        print_avoid(used_terms)
        return 0

    if args.peek:
        print("【体裁库总览】")
        for gid in all_ids:
            mark = "已用" if gid in used_genres else "可用"
            print(f"  [{mark}] {gid:<12} {names.get(gid, '')}")
        print(f"\n已交付 {len(state.get('delivered', []))} 篇；"
              f"未用体裁 {len(unused)} 个 / 共 {len(all_ids)} 个。")
        return 0

    if args.genre:
        if args.genre not in all_ids:
            sys.exit(f"体裁 '{args.genre}' 不在库里。可用：{', '.join(all_ids)}")
        chosen = args.genre
        why = "（用户或本步骤指定）"
    else:
        if not unused:
            print("所有体裁都用过了。请先在 genres.md 里扩充体裁库（追加新卡片 + GENRE-IDS 里加 id）。",
                  file=sys.stderr)
            return 1
        chosen = random.choice(unused)
        why = "（随机抽取，从未用体裁中选）"

    print("=" * 60)
    print(f"抽中体裁：{chosen} —— {names.get(chosen, '')} {why}")
    print("=" * 60)
    print(f"\n接下来打开 references/genres.md，照 "
          f"「## {numbers.get(chosen, 'G?')} {chosen} —— …」那张卡片执行。")
    print("卡片里给了：FCE 题型 / 待用候选词 / 第 ③ 区块形式 / 第 ④ 表单 / 骨架 / 铁律 / 易错点方向。\n")
    print(f"目前 {chosen} 这个体裁已交付 "
          f"{used_genres.count(chosen)} 篇。同一体裁最多连续 2 篇，且第 2 篇必须换子领域。\n")
    print_avoid(used_terms)
    print("\n【交付后别忘了】把新篇目追加到 references/state.json 的 delivered 数组。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
