#!/usr/bin/env bash
#
# 触发器评测（Trigger Evals）——打印待测用例，并给出判定标准。
#
# 为什么要单独一类 eval：
#   一个 skill 有两层失败模式
#     ① 该用它的时候没用（description 写得不好，召回不到）
#     ② 用了但做错了（脚本逻辑错，cases/ 里的行为契约负责）
#   cases/ 测的是 ②。这里测 ①，靠的是 SKILL.md 里那句 description。
#
# 为什么不做成全自动：
#   "这句话该不该触发" 属于语言判断，需要把 prompt 真的丢给一个带
#   这个 skill 的 agent 跑一遍、看它有没有 load_skill。当前环境里
#   没有可直接调用的 headless 入口（dsh 需要 web profile，且会写
#   用户目录）。所以 runner 先做**机械可验的部分**：把用例打印成
#   一张可直接丢给 agent 的表格；判定留给人和 agent。
#
#   等你有 headless 入口后，把 run_case() 换成一次真实调用即可——
#   用例文件的格式不用动。
#
# 用法：
#   bash evals/triggers/run.sh              # 打印全部用例
#   bash evals/triggers/run.sh negative     # 只打印文件名含 negative 的用例
#   bash evals/triggers/run.sh --template   # 打印一个可填写的判定表模板
#
# 人工/半自动判定步骤：
#   1. 把 prompt 原样发给一个**只挂载了本 skill** 的 agent
#   2. 记录：是否加载了 ticktick-todo？是否跑了 create_task_official_url.sh？
#   3. 和 expect_trigger / runs 对一下
#   4. 把结果回填到本目录 RESULT.md（模板见 --template）

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILTER=""

for arg in "$@"; do
  case "$arg" in
    --template) TEMPLATE=1 ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) FILTER="$arg" ;;
  esac
done

# 极简 yaml 列表解析：读 `- id:` / `prompt:` / `expect_trigger:` / `runs:` / `note:`
list_cases() {
  local file="$1"
  /usr/bin/python3 - "$file" <<'PY'
import sys, re, shutil

path = sys.argv[1]
rows, cur, in_note = [], {}, False
for raw in open(path, encoding="utf-8"):
    line = raw.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    m = re.match(r"^\s*-\s+(\w+):\s*(.*)$", line)
    if m:
        if "id" in cur:
            rows.append(cur)
        cur = {m.group(1): m.group(2)}
        in_note = False
        if m.group(1) == "note" and m.group(2).strip() in ("|", ">"):
            cur["note"] = ""
            in_note = True
        continue
    m = re.match(r"^\s+(\w+):\s*(.*)$", line)
    if m and cur:
        k, v = m.group(1), m.group(2).strip()
        if in_note and k == "note":
            in_note = False
        cur[k] = v
        if k == "note" and v in ("|", ">"):
            cur["note"] = ""
            in_note = True
        continue
    if in_note and cur:
        cur["note"] = (cur.get("note", "") + " " + line.strip()).strip()

if "id" in cur:
    rows.append(cur)

for r in rows:
    print("\t".join([
        r.get("id", ""),
        r.get("prompt", ""),
        r.get("expect_trigger", ""),
        r.get("runs", "(任意)"),
        r.get("note", ""),
    ]))
PY
}

if [ "${TEMPLATE:-0}" = "1" ]; then
  echo "| # | prompt | 期望触发 | 期望脚本 | 实际触发 | 实际脚本 | 判定 | 备注 |"
  echo "|---|--------|----------|----------|----------|----------|------|------|"
  n=0
  for f in "$HERE"/*.yaml; do
    [ -e "$f" ] || continue
    while IFS=$'\t' read -r id prompt expect runs note; do
      [ -n "$id" ] || continue
      n=$((n + 1))
      echo "| $n | $prompt | $expect | $runs | | | | $note |"
    done <<< "$(list_cases "$f")"
  done
  exit 0
fi

echo "ticktick-todo · 触发器评测用例"
echo "── 判定标准：skill 是否被加载（=description 召回），以及第一个动作是否跑脚本 ──"
echo

total=0 pos=0 neg=0
for f in "$HERE"/*.yaml; do
  [ -e "$f" ] || continue
  base="$(basename "$f")"
  [ -n "$FILTER" ] && [[ "$base" != *"$FILTER"* ]] && continue
  echo "▸ $base"
  while IFS=$'\t' read -r id prompt expect runs note; do
    [ -n "$id" ] || continue
    total=$((total + 1))
    case "$expect" in true) pos=$((pos + 1)) ;; false) neg=$((neg + 1)) ;; esac
    printf '  %-26s expect_trigger=%-5s  %s\n' "$id" "$expect" "$prompt"
    [ "$runs" = "(任意)" ] || printf '  %-26s runs=%s\n' "" "$runs"
    [ -z "$note" ] || printf '  %-26s ⓘ %s\n' "" "$(printf '%s' "$note" | sed 's/^"//; s/"$//')"
  done <<< "$(list_cases "$f")"
  echo
done

echo "总计 $total 条：该触发 $pos 条，不该触发 $neg 条"
echo
echo "怎么判定（见 README.md 第 3 节）："
echo "  1. 只挂载本 skill 开一个会话，把每个 prompt 原样发过去"
echo "  2. 看重放/日志：是否 load 了 ticktick-todo、是否 exec 了 create_task_official_url.sh"
echo "  3. bash triggers/run.sh --template 打一张表，填完存成 RESULT.md"
echo "  4. 正例漏召回 → 改 description；负例误召回 → 在 description 里加排除条件"
