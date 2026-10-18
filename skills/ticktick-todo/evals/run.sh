#!/usr/bin/env bash
#
# ticktick-todo 的行为契约评测器（Behavior Contract Evals）
#
# 它回答一个问题：**create_task_official_url.sh 产出的 TickTick URL 对不对？**
#
# 做法是把脚本放进一个"手机壳"里跑一次：
#   1. 准备一个临时沙盒目录（每个用例一个，互相隔离）
#   2. 在 PATH 最前面放三个 shim，把不可测的副作用换成可断言的记录：
#        open     → 只把收到的 URL 追加到 calls.log，绝不打不开的应用
#        date     → 返回固定时间，让「明天/今天」类用例可复现（可选）
#        python3  → 透传到系统 python3，避开 Homebrew/pyenv 的差异
#   3. 同时用 bash -x 采集变量轨迹（trace.log），供 trace 类断言使用
#   4. 依次跑用例里的四类断言：url / exit / direct / trace
#
# 用法：
#   bash evals/run.sh                     # 跑全部用例
#   bash evals/run.sh relative            # 只跑文件名包含该子串的用例
#   bash evals/run.sh --verbose           # 额外打印失败用例的脚本输出
#   bash evals/run.sh --demo              # 只演示四类断言，不跑用例
#
# 退出码：0 = 全绿（或只有已知失败）；1 = 有预期外的失败；2 = 环境/用例文件错误
#
# 设计原则：只依赖 macOS 自带的 bash 3.2 + /usr/bin/python3。不装任何东西。

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$HERE/.." && pwd)"
SCRIPT="$SKILL_DIR/create_task_official_url.sh"

VERBOSE=0
FILTER=""
for arg in "$@"; do
  case "$arg" in
    --verbose|-v) VERBOSE=1 ;;
    --demo) MODE=demo ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) FILTER="$arg" ;;
  esac
done
MODE="${MODE:-suite}"

# ---------- 颜色（非 tty 时自动关闭） ----------
if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_FAIL=$'\033[31m'; C_SKIP=$'\033[33m'
  C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_END=$'\033[0m'
else
  C_OK=""; C_FAIL=""; C_SKIP=""; C_DIM=""; C_BOLD=""; C_END=""
fi

# ---------- 断言小工具 ----------
# 每条断言失败时调用 fail()，记下序号与第一条失败信息
FAIL_COUNT=0
FAIL_MSG=""
CUR_KIND=""

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  if [ -z "$FAIL_MSG" ]; then FAIL_MSG="$CUR_KIND: $1"
  else FAIL_MSG="$FAIL_MSG"$'\n'"    $CUR_KIND: $1"; fi
}
skip_whole_case() { CASE_SKIPPED="$1"; }

# ==================== 沙盒构建 ====================

# 造一个隔离的沙盒：bin/ 里放 shim，data/ 里放产物
make_sandbox() {
  SB="$(mktemp -d "${TMPDIR:-/tmp}/ticktick-eval.XXXXXX")"
  mkdir -p "$SB/bin" "$SB/data"
  : > "$SB/data/calls.log"

  # ① open shim：把参数原样记下来。参数里含换行也不怕——脚本从不把换行放进 URL
  cat > "$SB/bin/open" <<'SHIM'
#!/bin/bash
printf '%s\n' "$*" >> "$TT_CAPTURE"
SHIM

  # ② date shim：让「明天/今天」可复现。只在 fixture.clock 存在时才拦截，
  #    否则原样转给真 date——这样默认情况下测到的就是真实行为。
  cat > "$SB/bin/date" <<'SHIM'
#!/bin/bash
if [ -n "${TT_FIXED_TODAY:-}" ]; then
  if [ -n "${TT_FIXED_TOMORROW:-}" ]; then
    case "$*" in *-v+1d*) printf '%s\n' "$TT_FIXED_TOMORROW"; exit 0 ;; esac
  fi
  printf '%s\n' "$TT_FIXED_TODAY"; exit 0
fi
for d in /bin/date /usr/bin/date; do [ -x "$d" ] && exec "$d" "$@"; done
SHIM

  # ③ python3 shim：锁定系统 python3（脚本用它做 URL 编码）
  cat > "$SB/bin/python3" <<'SHIM'
#!/bin/bash
exec /usr/bin/python3 "$@"
SHIM
  chmod +x "$SB/bin/open" "$SB/bin/date" "$SB/bin/python3"
}

run_script() {  # $1 = stdout 文件, $2 = stderr 文件, $3 = trace 文件（空 = 不采集轨迹）
  local out="$1" err="$2" trace="$3" rc
  # 一次性跑 xtrace：轨迹和脚本自己的 stderr 都进 err.bin，之后再拆分。
  # 这样脚本最多只跑一遍（避免 double-open 一类副作用被触发两次）。
  (
    cd "$SB" || exit 1
    PATH="$SB/bin:$PATH" TT_CAPTURE="$SB/data/calls.log" \
    TT_FIXED_TODAY="${TT_FIXED_TODAY:-}" TT_FIXED_TOMORROW="${TT_FIXED_TOMORROW:-}" \
    PS4='+$ ' bash -x "$SCRIPT" "${CASE_ARGS[@]}"
  ) >"$out" 2>"${err%.bin}.bin"
  rc=$?

  # 拆分：轨迹行以 "+$ " 开头。用 LC_ALL=C 做字节过滤——
  # bash 3.2 会把非 ASCII 值打成 $'\xE6...'，产生非 UTF-8 字节，grep 会报错。
  LC_ALL=C grep -v '^+*\$ ' "${err%.bin}.bin" > "$err" 2>/dev/null
  [ -s "$err" ] || : > "$err"
  if [ -n "$trace" ]; then
    LC_ALL=C grep '^+*\$ ' "${err%.bin}.bin" > "$trace" 2>/dev/null
    [ -s "$trace" ] || : > "$trace"
  fi
  rm -f "${err%.bin}.bin"
  return $rc
}

# ==================== 提取器 ====================

# 从 URL 里安全地取一个 query 参数（URL 解码）。参数名不存在时输出空串。
# 用 stdin 传 URL，避免把 URL 塞进 python 的字符串字面量（URL 里可能有 ' 或 \）
url_param() {  # $1 = 参数名, stdin = URL
  /usr/bin/python3 -c '
import sys, urllib.parse
raw = sys.stdin.read().strip()
if not raw.startswith("ticktick://"):
    sys.exit(3)                     # 不是 TickTick URL
q = urllib.parse.urlsplit(raw).query
vals = urllib.parse.parse_qs(q, keep_blank_values=True)
print(vals.get(sys.argv[1], [""])[0])
' "$1"
}

# 从 bash -x 轨迹里取某个变量最后一次赋值的值。
# 轨迹行形如 `+ TASK_DUE_TIME=19:50` / `++ TASK_DUE_DATE=2026-03-02`，
# 含空格的值会被整体包一层引号；非 ASCII 字符在 macOS bash 3.2 里会被
# 打成 $'\xE6\x98...' 形式，这里一并还原。
trace_var() {  # $1 = 变量名, stdin = trace.log
  /usr/bin/python3 "$HERE/lib/trace.py" "$1"
}

# 用 argv 传值，绝不用字符串拼接——否则 title 里有单引号就会把 python 代码拼断。
# （被测脚本自己就踩了这个坑，见 07-escaped-chars 用例。）
urlencode_expected() {
  /usr/bin/python3 -c 'import sys, urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

# ==================== 断言执行器 ====================
# 每个 check_* 函数：kind / 参数校验 / 断言实现。参数为空一律记 fail——
# 空参数常常意味着上游解析错了（比如 yaml 读错字段），宁可红也别悄悄绿。

check_url() {
  local param="$1" expect="$2"
  need param "$param"; need param "$expect"
  local actual; actual="$(url_param "$param" < "$SB/data/calls.log")"
  [ "$actual" = "$expect" ] || fail "参数 $param 期望 [$expect]，实际 [$actual]"
}

check_url_encoded() {
  # 和 url 的区别：url 断言做「解码后比较」，所以你写中文就行；
  # url_encoded 断言额外检查**原始 URL** 里这一段的编码形状，
  # 用来钉住"空格必须是 %20 而不是 +"这类 URL 规范问题。
  local param="$1" raw="$2"
  need param "$param"; need param "$raw"
  check_url "$param" "$raw"
  local expect; expect="$(urlencode_expected "$raw")"
  if ! grep -qF -- "${param}=${expect}" "$SB/data/calls.log"; then
    fail "参数 $param 的编码形状不符：原始 URL 里应有 ${param}=${expect}"
  fi
}

check_url_regex() {
  local param="$1" pattern="$2"
  need param "$pattern"
  local actual; actual="$(url_param "$param" < "$SB/data/calls.log")"
  printf '%s' "$actual" | grep -Eq "$pattern" \
    || fail "参数 $param 值 [$actual] 不匹配正则 /$pattern/"
}

check_exit() {
  local expect="$1"
  need param "$expect"
  [ "$SCRIPT_EXIT" = "$expect" ] \
    || fail "退出码期望 [$expect]，实际 [$SCRIPT_EXIT]"
}

check_direct() {
  local kind="$1" value="$2"
  need param "$kind"; need param "$value"
  case "$kind" in
    count)      # 恰好构造了 N 个 URL
      local n; n=$(grep -c . "$SB/data/calls.log")
      [ "$n" -eq "$value" ] || fail "期望构造 $value 个 URL，实际 $n 个" ;;
    none)       # 一个 URL 都没构造（dry-run / 带病退出）
      local n; n=$(grep -c . "$SB/data/calls.log")
      [ "$n" -eq 0 ] || fail "期望 0 个 URL，实际 $n 个" ;;
    has_line)   # stdout 里出现某一行
      grep -qF -- "$value" "$SB/data/stdout.log" || fail "stdout 缺少行 [$value]" ;;
    lacks_line)
      grep -qF -- "$value" "$SB/data/stdout.log" && fail "stdout 意外出现 [$value]" ;;
    matches)    # stdout 匹配正则
      grep -Eq -- "$value" "$SB/data/stdout.log" || fail "stdout 不匹配正则 /$value/" ;;
    stderr_matches)  # stderr 匹配正则（用来钉住「静默失败」这个 bug）
      grep -Eq -- "$value" "$SB/data/stderr.log" || fail "stderr 不匹配正则 /$value/" ;;
    *) echo "未知 direct 断言类型: $kind" >&2; exit 2 ;;
  esac
}

check_trace() {
  local name="$1" expect="$2" actual
  need param "$name"; need param "$expect"
  [ -f "$SB/data/trace.log" ] || { fail "没有 trace.log（用例需 trace: true）"; return; }
  actual="$(trace_var "$name" < "$SB/data/trace.log")"
  [ "$actual" = "$expect" ] || fail "变量 $name 期望 [$expect]，实际 [$actual]"
}

check_trace_absent() {
  local name="$1"
  need param "$name"
  [ -f "$SB/data/trace.log" ] || { fail "没有 trace.log（用例需 trace: true）"; return; }
  if grep -Eq "^\++ ${name}=" "$SB/data/trace.log"; then
    fail "变量 $name 本不该被赋值，却赋了值"
  fi
}

need() {  # $1 = 参数名, $2 = 值
  [ -n "$2" ] || fail "断言缺参数: $1 为空"
}

# ==================== YAML 解析 ====================
# 不依赖 yq。解析规则只覆盖本项目用例用到的子集：
#   - 顶层 `key: value`（标量）
#   - `args: [a, b]` 或 `args:` 下面 `  - a`
#   - `checks:` 下每个 `  - kind: xxx` + `    param: v` 的列表项
# 刻意保持"蠢"：解析器是评测基础设施，它越复杂，坏了越难查。
# 如果将来要更强的 yaml，换成 python3 的 PyYAML 也行，但先确认机器上有。

T_CASE=(); T_ARGS=(); T_CHECKS=()   # 并行数组：每项依次是 kind|param|value

parse_case_file() {  # $1 = 文件路径
  T_CASE=(); T_ARGS=(); T_CHECKS=()
  T_EXPECT_TRIGGER=""; T_EXPECT_NO_TRIGGER=""; T_PROMPT=""; T_TRACE=0
  T_FIXED_TODAY=""; T_FIXED_TOMORROW=""; T_ALLOW_FAIL=0; T_NOTES=""
  T_NEEDS=""

  local line key val
  local in_checks=0 in_args=0
  local cur_kind="" cur_param="" cur_value=""

  flush_check() {
    if [ -n "$cur_kind" ]; then
      T_CHECKS+=("$cur_kind|$cur_param|$cur_value")
      cur_kind=""; cur_param=""; cur_value=""
    fi
  }

  while IFS= read -r line || [ -n "$line" ]; do
    # 跳过空行与整行注释
    case "$line" in ''|'#'*) continue ;; esac
    # 去行尾空白
    line="${line%"${line##*[![:space:]]}"}"

    if [[ "$line" =~ ^[[:space:]]*-(.*)$ ]]; then
      # 列表项
      rest="${BASH_REMATCH[1]}"
      rest="$(trim "$rest")"
      if [ "$in_args" = "1" ]; then
        T_ARGS+=("$(dequote "$rest")")
        continue
      fi
      if [ "$in_checks" = "1" ]; then
        flush_check
        # 列表项形如：`- kind: url`，或简写 `- url: startDate`（键名即断言类型）。
        # 特意不支持「kind 和 value 写在同一个列表项里」——那要求解析器懂缩进
        # 层级，等于手写 yaml parser。标准写法是 kind 单独一行，param/value 缩进。
        if [[ "$rest" =~ ^([A-Za-z_]+):[[:space:]]*(.*)$ ]]; then
          local mkey="${BASH_REMATCH[1]}" mval="${BASH_REMATCH[2]}"
          mval="$(dequote "$(trim "$mval")")"
          case "$mkey" in
            kind) cur_kind="$mval" ;;                        # 标准写法
            url|url_encoded|url_regex|exit|direct|trace|trace_absent)
               cur_kind="$mkey"; cur_param="$mval" ;;        # 简写
            *)    cur_kind="$mval" ;;                        # 容错：旧写法
          esac
        elif [[ "$rest" =~ ^[A-Za-z_]+$ ]]; then
          cur_kind="$rest"
        fi
        continue
      fi
      continue
    fi

    if [[ "$line" =~ ^([A-Za-z_]+):[[:space:]]*(.*)$ ]]; then
      # 先取出再声明 local：local 会重置 BASH_REMATCH
      key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
      in_checks=0; in_args=0
      case "$key" in
        case)            T_CASE+=("$(dequote "$val")") ;;
        note)            T_NOTES="$(dequote "$val")" ;;
        allow_fail)      T_ALLOW_FAIL=$([ "$val" = "true" ] && echo 1 || echo 0) ;;
        needs)           T_NEEDS="$(dequote "$val")" ;;
        trace)           T_TRACE=$([ "$val" = "true" ] && echo 1 || echo 0) ;;
        fixed_today)     T_FIXED_TODAY="$(dequote "$val")" ;;
        fixed_tomorrow)  T_FIXED_TOMORROW="$(dequote "$val")" ;;
        expect_trigger)  T_EXPECT_TRIGGER=1 ;;
        expect_no_trigger) T_EXPECT_NO_TRIGGER=1 ;;
        prompt)          T_PROMPT="$val" ;;
        args)
          if [[ "$val" == \[*\] ]]; then
            # 行内数组 [a, b, c]
            local inner="${val#[}"; inner="${inner%]}"
            local IFS_SAVE="$IFS"; IFS=','
            local item
            for item in $inner; do
              item="$(trim "$item")"
              T_ARGS+=("$(dequote "$item")")
            done
            IFS="$IFS_SAVE"
          else
            in_args=1
          fi ;;
        checks) in_checks=1 ;;
        runs|skip_if_no_ticktick) ;;   # 触发器用例专用字段，行为套件忽略
      esac
      continue
    fi

    if [[ "$line" =~ ^[[:space:]]+([A-Za-z_]+):[[:space:]]*(.*)$ ]]; then
      # 同上：先取值，再声明局部变量
      key="${BASH_REMATCH[1]}"; val="$(dequote "$(trim "${BASH_REMATCH[2]}")")"
      if [ "$in_checks" = "1" ]; then
        case "$key" in
          kind)  cur_kind="$val" ;;
          param) cur_param="$val" ;;
          value) cur_value="$val" ;;
        esac
      else
        case "$key" in
          today)    T_FIXED_TODAY="$val" ;;
          tomorrow) T_FIXED_TOMORROW="$val" ;;
        esac
      fi
      continue
    fi
  done < "$1"
  flush_check

  # args 支持多行块写法：`  args:` 后接 `  - x` 的缩进列表已由 in_args 处理
  # checks 的 param/value 可能是数字，保持字符串比较
}

trim() { sed 's/^[[:space:]]*//; s/[[:space:]]*$//' <<< "$1"; }

# 去掉包裹的引号；处理 yaml 的 \" 转义
dequote() {
  local s="$1"
  if [[ "$s" == \"*\" && ${#s} -ge 2 ]]; then
    s="${s:1:${#s}-2}"; s="${s//\\\"/\"}"
  elif [[ "$s" == \'*\' && ${#s} -ge 2 ]]; then
    s="${s:1:${#s}-2}"
  fi
  printf '%s' "$s"
}

# 用 python3 收集单个 yaml 文件的 case 名（用于 status 汇总）
list_case_files() {
  ls -1 "$HERE"/cases/*.yaml 2>/dev/null | sort
}

# ==================== 单条用例执行 ====================
# 返回：0 通过 / 1 预期失败 / 2 意外失败 / 3 跳过

execute_case() {  # $1 = 文件路径, $2 = 显示序号
  local file="$1" idx="$2"
  parse_case_file "$file"
  local case_name="$(basename "$file" .yaml)"

  CASE_SKIPPED=""
  CASE_ARGS=(${T_ARGS[@]+"${T_ARGS[@]}"})
  TT_FIXED_TODAY="$T_FIXED_TODAY"
  TT_FIXED_TOMORROW="$T_FIXED_TOMORROW"

  if [ -n "$T_NEEDS" ] && [ ! -e "$T_NEEDS" ]; then
    skip_whole_case "$T_NEEDS 不存在"
  fi

  make_sandbox
  SB_SANDBOX="$SB"

  local trace_arg=""
  [ "$T_TRACE" = "1" ] && trace_arg="$SB/data/trace.log"
  run_script "$SB/data/stdout.log" "$SB/data/stderr.log" "$trace_arg"
  SCRIPT_EXIT=$?
  # 拷贝计时进度无关，保留原始产物供 --verbose 展示

  FAIL_COUNT=0; FAIL_MSG=""
  local entry kind param value
  for entry in ${T_CHECKS[@]+"${T_CHECKS[@]}"}; do
    # 条目格式：kind|param|value。断言类型不同，三个字段的含义也不同：
    #   url / url_encoded / url_regex / trace → param=参数名, value=期望值
    #   direct                                 → param=子类型,  value=期望值
    #   exit / trace_absent                    → param 直接就是期望值/变量名（没有 value）
    # 所以这里只做切分，**由每个分支自己决定取哪个字段**，不要统一假设。
    kind="${entry%%|*}"
    local rest="${entry#*|}"
    param="${rest%%|*}"
    if [ "$rest" = "$param" ]; then value=""; else value="${rest#*|}"; fi

    CUR_KIND="$kind"
    case "$kind" in
      url)          check_url "$param" "$value" ;;
      url_encoded)  check_url_encoded "$param" "$value" ;;
      url_regex)    check_url_regex "$param" "$value" ;;
      direct)       check_direct "$param" "$value" ;;
      trace)        check_trace "$param" "$value" ;;
      trace_absent) check_trace_absent "$param" ;;
      exit)         check_exit "$param" ;;
      *)            fail "未知断言类型: $kind" ;;
    esac
  done

  local verdict=0
  if [ -n "$CASE_SKIPPED" ]; then
    verdict=3
    printf '%sSKIP%s %-34s %s(%s)%s\n' "$C_SKIP" "$C_END" "$case_name" "$C_DIM" "$CASE_SKIPPED" "$C_END"
  elif [ "$FAIL_COUNT" -eq 0 ]; then
    printf '%sPASS%s %-34s\n' "$C_OK" "$C_END" "$case_name"
  elif [ "$T_ALLOW_FAIL" = "1" ]; then
    verdict=1
    printf '%sKNOWN-FAIL%s %-28s %s(已登记的 skill bug%s)%s\n' \
      "$C_SKIP" "$C_END" "$case_name" "$C_DIM" "$([ -n "$T_NOTES" ] && echo "：$T_NOTES")" "$C_END"
    if [ "$VERBOSE" = "1" ]; then printf '%s\n' "$FAIL_MSG" | sed 's/^/    /'; fi
  else
    verdict=2
    printf '%sFAIL%s %-34s\n' "$C_FAIL" "$C_END" "$case_name"
    printf '%s\n' "$FAIL_MSG" | sed 's/^/    /'
    if [ "$VERBOSE" = "1" ]; then show_artifacts; fi
  fi

  [ -n "${KEEP_SANDBOX:-}" ] || rm -rf "$SB_SANDBOX"
  return $verdict
}

show_artifacts() {
  local label="$1"
  echo "    ${C_DIM}┌─ $label"; shift
  for f in "$@"; do
    [ -f "$SB/data/$f" ] || continue
    echo "    ├─ $f"
    sed 's/^/    │  /' "$SB/data/$f"
  done
  echo "    └─${C_END}"
}

# ==================== demo：四类断言长什么样 ====================
demo() {
  echo "${C_BOLD}── 四类断言演示（都在同一个真实脚本上跑）──${C_END}"
  echo
  echo "${C_BOLD}① url / url_encoded：URL 里某个参数的值对不对${C_END}"
  echo "   ${C_DIM}这是最高价值、最容易维护的断言。用 URL 解码后的值做比较，"
  echo "   而不是去 grep 原始 URL 的百分号编码——那样太脆。${C_END}"
  demo_case "url"          "startDate" "=='2026-03-02T19:50:00.000'" "title / content / list 同理"
  demo_case "url_encoded"  "content"   "=='英语课 2026-03-02 20:00'"    "中文与空格自动编码"
  demo_case "url_regex"    "startDate" "=~'^2026-03-02T'"              "格式约束（时间戳前缀）"
  echo
  echo "${C_BOLD}② exit：脚本退出码${C_END}"
  echo "   ${C_DIM}脚本"成功"不等于"做对了"。退出码 0 只证明脚本跑完了，"
  echo "   所以它永远和 url 断言一起出现。${C_END}"
  demo_case "exit" "" "=='0'" "跑完且无致命错误"
  echo
  echo "${C_BOLD}③ direct：URL 之外的副作用${C_END}"
  echo "   ${C_DIM}一个任务只能构造一次 URL——这是"没有重复创建"的断言。"
  echo "   还能断言 stdout 的关键行（用户可见的 Task details）。${C_END}"
  demo_case "direct" "count" "=='1'" "只构造 1 个 URL，没有重复创建"
  demo_case "direct" "has_line" "='- Task Start Time: 2026-03-02 19:50'" "用户能在终端看到确认信息"
  echo
  echo "${C_BOLD}④ trace：内部变量（只测核心算法时才用）${C_END}"
  echo "   ${C_DIM}不动外部行为的小纯函数（这里是 subtract_10_minutes），"
  echo "   可以用 bash -x 直接读变量，不必为它造一串集成用例。${C_END}"
  echo "   bash -x 轨迹里能看到这些赋值："
  echo "${C_DIM}"
  LC_ALL=C grep -E 'TASK_DUE_(DATE|TIME)=' "$SB/data/trace.log" 2>/dev/null | sed 's/^/     /' | head -4
  echo "${C_END}"
}

demo_case() {  # kind, param, human-expect, note；直接复用真 sandbox 的产物做断言
  local kind="$1" param="$2" human="$3" note="$4"
  local before=$FAIL_COUNT failmsg=""
  CUR_KIND="$kind"
  case "$kind" in
    url)         local v; v="$(url_param "$param" < "$SB/data/calls.log")"; [ "$v" = "2026-03-02T19:50:00.000" ] || fail x ;;
    url_encoded) local v; v="$(url_param "$param" < "$SB/data/calls.log")"; [ "$v" = "英语课 2026-03-02 20:00" ] || fail x ;;
    url_regex)   local v; v="$(url_param "$param" < "$SB/data/calls.log")"; printf '%s' "$v" | grep -Eq '^2026-03-02T' || fail x ;;
    exit)        [ "$SCRIPT_EXIT" = "0" ] || fail x ;;
    direct)      if [ "$param" = count ]; then [ "$(grep -c . "$SB/data/calls.log")" -eq 1 ] || fail x;
                 else grep -qF -- "- Task Start Time: 2026-03-02 19:50" "$SB/data/stdout.log" || fail x; fi ;;
  esac
  local mark="${C_OK}✓${C_END}"; [ "$FAIL_COUNT" -gt "$before" ] && mark="${C_FAIL}✗${C_END}"
  printf '   %s %-14s %-12s %s  %s— %s%s\n' "$mark" "$kind" "$param" "$human" "$C_DIM" "$note" "$C_END"
}

# ==================== 主流程 ====================

if [ ! -f "$SCRIPT" ]; then
  echo "找不到被测脚本: $SCRIPT" >&2; exit 2
fi

# demo 需要先跑一次真实脚本拿到产物
if [ "$MODE" = "demo" ]; then
  make_sandbox
  CASE_ARGS=("英语课" "2026-03-02 20:00" "" "提子")
  run_script "$SB/data/stdout.log" "$SB/data/stderr.log" "$SB/data/trace.log"
  SCRIPT_EXIT=$?
  demo
  rm -rf "$SB"
  exit 0
fi

# ---- 汇总 ----
echo "${C_BOLD}ticktick-todo · 行为契约评测${C_END}"
echo "${C_DIM}被测脚本: $SCRIPT${C_END}"
echo "${C_DIM}规则: 只捕获 URL，不打开 TickTick；每个用例独立沙盒，按文件名顺序跑${C_END}"
echo

total=0 pass=0 known_fail=0 unexpected=0 skipped=0
declare -a unexpected_names=()

# bash 3.2 没有 mapfile，用 read 循环
while IFS= read -r file; do
  [ -n "$file" ] || continue
  case_name="$(basename "$file" .yaml)"
  if [ -n "$FILTER" ] && [[ "$case_name" != *"$FILTER"* ]]; then continue; fi
  total=$((total + 1))
  execute_case "$file" "$total"
  case $? in
    0) pass=$((pass + 1)) ;;
    1) known_fail=$((known_fail + 1)) ;;
    2) unexpected=$((unexpected + 1)); unexpected_names+=("$case_name") ;;
    3) skipped=$((skipped + 1)) ;;
  esac
done <<< "$(list_case_files)"

# 一个用例文件都没有时给个提示，而不是假装全绿
if [ "$total" -eq 0 ]; then
  echo "${C_DIM}（没有匹配的用例文件）${C_END}"
fi

echo
echo "${C_BOLD}结果${C_END}  总计 $total ｜ ${C_OK}通过 $pass${C_END} ｜ ${C_SKIP}已知失败 $known_fail${C_END} ｜ ${C_FAIL}意外失败 $unexpected${C_END} ｜ ${C_SKIP}跳过 $skipped${C_END}"
if [ "$unexpected" -gt 0 ]; then
  echo "  意外失败：${unexpected_names[*]}"
  exit 1
fi
exit 0
