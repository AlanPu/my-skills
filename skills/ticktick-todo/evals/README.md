# ticktick-todo · Eval 入门手册

这套 eval 是给 `ticktick-todo` 写的，但**格式和思路可以照搬到任何一个带脚本的 skill**。

跑一下先看效果：

```bash
cd skills/ticktick-todo
bash evals/run.sh            # 10 条行为契约用例
bash evals/run.sh --demo     # 只看"四类断言长什么样"
bash evals/triggers/run.sh   # 20 条触发器用例（该不该用这个 skill）
```

---

## 1. 先搞清楚：skill 的 eval 分两层

一个 skill 有两种坏法，对应两种完全不同的 eval：

| | 测什么 | 失败长什么样 | 在哪 |
|---|---|---|---|
| **触发器 eval**（trigger） | `SKILL.md` 的 `description` | 该用时没用；不该用时乱用 | `triggers/` |
| **行为契约 eval**（behavior） | skill 里的脚本 / 流程 | 用了，但结果算错了 | `cases/` |

你现在的 skill 两个问题都有。先说结论：

- **触发层**：`triggers/02-negative.yaml` 里「我明天要上英语课，帮我准备点单词」很可能被误触发——因为 `description` 里的「滴答清单」是主要锚点，但 SKILL.md 正文里「英语课 / 明天 09:00」才是高频词，模型可能被正文带偏。
- **行为层**：跑一遍 `bash evals/run.sh` 会看到 **5 条 KNOWN-FAIL**，都是真 bug（见第 6 节）。

这两层不能混着测。把触发问题混进行为用例，会让行为用例的失败原因变模糊。

---

## 2. 行为契约 eval 的核心：把副作用变成可断言的东西

`create_task_official_url.sh` 最后会执行 `open ticktick://...`。这一句：
- 在没装 TickTick 的机器上会报错；
- 在你自己的机器上会真的弹出创建窗口，污染你的清单；
- 返回的 URL 是"内部细节"，没有地方能读到。

**直接测它有两个问题：跑一次污染一次；而且没法断言。**

标准解法是 **shim（替身）**：在 `PATH` 最前面放一个假的 `open`，它只把参数写进日志。

```
PATH=<sandbox>/bin:$PATH bash create_task_official_url.sh ...
                 └─ bin/open  →  printf '%s\n' "$*" >> calls.log
```

这样：
- 不打开 TickTick（可重复跑、可上 CI）；
- 得到完整 URL（可断言）；
- 顺手还能数"构造了几个 URL"（防重复创建）。

同样的手法放了一个 `date` shim，用来把「明天」钉死成某个固定日期——**否则评测会随运行日期漂移**：周一跑绿的，周二可能就红了，而且和代码毫无关系。

> 凡是"时间 / 网络 / 剪贴板 / 弹窗 / 系统通知"这类副作用，都应该用 shim 换成可读的东西再断言。

---

## 3. 四类断言：从"最值得写"到"最不该滥用"

`cases/*.yaml` 里能用的断言就这四类：

### ① `url` —— URL 里某个参数的值（**主力**）

```yaml
- kind: url
  param: startDate
  value: "2026-03-02T19:50:00.000"
```

runner 会把 URL **解码后再比较**，所以中文直接写，不用手算 `%E8...`。

> 为什么不用 `grep` 原始 URL？因为百分号编码太长、太脆，而且一换编码器（Python vs Node）空格变成 `+` 就会误报。

### ② `direct` —— URL 之外的副作用

```yaml
- kind: direct
  param: count          # 只构造了 1 个 URL，没有重复创建
  value: "1"
- kind: direct
  param: has_line       # 终端上有用户可见的确认信息
  value: "- Task Start Time: 2026-03-02 19:50"
```

子类型：`count` / `none` / `has_line` / `lacks_line` / `matches` / `stderr_matches`。

### ③ `exit` —— 退出码

```yaml
- exit: "0"
```

**单独写它没有意义**。退出码 0 只证明"脚本跑完没崩"，不证明"算对了"——你现在的脚本就是算错了还返回 0。所以它一般和 `url` 断言一起出现，或者用来钉住"非法输入应当非零退出"。

### ④ `trace` —— 脚本内部变量（**少用**）

```yaml
trace: true            # 用例文件顶部要打开
- kind: trace
  param: TASK_DUE_TIME
  value: "19:50"
```

`trace` 是让 runner 用 `bash -x` 跑一遍，从轨迹里读变量。它能直接看到 `subtract_10_minutes 20:00` 算出了什么。

**但它和实现绑死**：脚本一改名/重构就全红。只在两种场景用：
- 测一个内部纯函数，为它造集成用例太浪费；
- 确认"某个分支本不该被执行"（`trace_absent`）。

---

## 4. 一个用例文件长什么样

```yaml
case: absolute_time_subtracts_10_minutes     # 必填，出现在报告里
note: 核心契约——开始时间提前 10 分钟，内容保留原始时间

args: ["英语课", "2026-03-02 20:00", "", "🧚提子"]   # 传给脚本的 4 个参数

# 可选：把"今天"钉死成某个日期（相对时间用例才需要）
fixed_today: "2026-10-18"
fixed_tomorrow: "2026-10-19"

checks:
  - kind: url
    param: startDate
    value: "2026-03-02T19:50:00.000"
  - kind: url_encoded
    param: content
    value: "英语课 2026-03-02 20:00"
  - kind: direct
    param: count
    value: "1"
  - exit: "0"

# 可选：已知 bug。断言"正确行为"，现在红着，修好后自动变绿
allow_fail: true
```

> 解析器是手写的，只支持这个子集。复杂了就换 PyYAML，别把解析器越写越玄。

---

## 5. 怎么加用例：一个可操作的顺序

1. **先抄最稳的那条**（`01-absolute-time.yaml`）→ 改 `args` 和期望值。
2. **每加一条新用例，先让它红一次**。改错的 bug：把期望值故意写错，确认报告变红，再改回来。**没红过的用例等于没写。**
3. **优先覆盖"分支"**：`if` 有几条分支就至少几条用例（`04` 明天 / `10` 只有日期）。
4. **再覆盖"边界"**：`00:05`（跨天）、`09:30`（前导零）、`20:75`（非法分钟）、带单引号的标题。
5. **最后才是内部逻辑**（`09-trace`），而且只在没有更外层的可观测行为时才用。

---

## 6. 这套 eval 已经抓到的真 bug

跑 `bash evals/run.sh` 会看到这些 KNOWN-FAIL。它们不是用例写错了，是脚本确实有问题：

| 用例 | 现象 | 根因 |
|---|---|---|
| `03-leading-zero` | `09:30` → `00:20` | bash 把 `09` 当八进制。`$((hour - 1))` 需写成 `$((10#$hour - 1))` |
| `05-relative-time-09` | `明天 09:00` 静默变成"全天任务" | 同上；且 SKILL.md 的第一个示例就是它 |
| `06-fail-loudly` | `20:75` → 造出 `21:15`，退出码 0 | 没有范围校验，且脚本没开 `set -euo pipefail` |
| `07-escaped-chars` | 标题含 `'` 时 title 整个丢失 | `python3 -c "...quote('$TASK_TITLE')..."` 字符串拼接，单引号把代码拼断 |
| `02-midnight-wrap` | `00:05` 不跨天 | 只对分钟回绕，日期没跟着减一天 |

顺带一个**文档 bug**：`SKILL.md` 里所有命令都写 `.trae/skills/ticktick-todo/...`，但仓库里的实际路径是 `skills/ticktick-todo/...`。照着 SKILL.md 抄命令会 file not found。

**怎么看这些红**：它们是"已知失败"，所以不阻断（退出码 0）。等你修好，它们会**自动变绿**——这正是 eval 的价值：你不需要记着自己还有哪些坑。

---

## 7. 触发器 eval：为什么它不做成全自动

`triggers/run.sh` 只打印用例，判定靠人。原因很实在：判定"这句话该不该触发"需要一个带这个 skill 的 agent 真跑一遍，而当前环境的 `dsh` 没有可直接调用的 headless 入口。

所以它的设计是**给判定提供一张好用的表**：

```bash
bash evals/triggers/run.sh              # 打印全部 20 条
bash evals/triggers/run.sh --template   # 打一张可填写的判定表
```

步骤：
1. 只挂载本 skill 开一个会话；
2. 把每个 `prompt` 原样发过去；
3. 记录：是否加载了 skill？第一个动作是否 exec 了脚本？
4. 回填 `RESULT.md`。

**改 description 的规则**：
- 正例漏召回 → description 里加"动作词"（建/加/记/安排），而不是堆关键词；
- 负例误召回 → 加排除条件，比如「仅在用户要求**在滴答清单中创建/添加**任务时使用；只是提到待办、清单、日历不触发」。

---

## 8. 怎么读报告 & 三个反模式

```
PASS        01-absolute-time                  # 绿
KNOWN-FAIL  03-leading-zero  (已登记的 bug)    # 黄：已知 bug，不阻断
FAIL        99-some-new-case                  # 红：你刚弄坏了什么
SKIP        xxx  (前置条件缺失)
```

退出码：`0` = 全绿或只有已知失败；`1` = 有意外失败；`2` = 环境/用例文件错误。

**反模式一：用例写着写着不红了，也没人管。**
→ `allow_fail` 必须配 `note` 说明"为什么允许红"，并且定期复查。

**反模式二：把当前行为原样抄成期望值。**
→ 期望值应该来自**规范**（SKILL.md 的契约 / 用户的需求），不是来自"脚本现在打印什么"。否则 eval 只是给 bug 拍照留念。

**反模式三：只测 happy path。**
→ 10 条用例里如果有 9 条是 `["英语课","2026-03-02 20:00","","🧚提子"]` 改改时间，那你只证明了"减 10 分钟不会崩"。上面五个真 bug 里，四个都藏在边界。

---

## 9. 文件结构

```
evals/
├── README.md              ← 你正在读的这份
├── run.sh                 ← 行为契约 runner（只依赖 macOS 自带 bash + python3）
├── lib/
│   └── trace.py           ← 从 bash -x 轨迹里抽变量（独立成文件，避免引号地狱）
├── cases/                 ← 行为契约用例（yaml = 数据，不是代码）
│   ├── 01-absolute-time.yaml
│   ├── ...
│   └── 10-all-day.yaml
└── triggers/
    ├── run.sh             ← 触发器 runner（打印 + 判定模板）
    ├── 01-positive.yaml   ← 该触发的 10 条
    └── 02-negative.yaml   ← 不该触发的 10 条
```

**为什么用例是 yaml 不是 shell 脚本：**
用例是**数据**。数据能被 review、能被生成、能不擅长 shell 的人写；把断言写成 `if/then` 脚本，很快会变成第二个 bug 源。

---

## 10. 下一步建议

1. **先把 5 个已知 bug 修掉**，把 `allow_fail: true` 删掉。这是最划算的——eval 已经帮你把清单列好了。
2. **修 `08-relative-bug-guard` 时记得删掉它**。它守卫的是"错误行为"，修好后会变红，这是提醒信号。
3. **加一条 SKILL.md 路径用例**：断言技能文档里引用的脚本路径真实存在（能自动抓到 `.trae/skills/` 那个路径 bug）。
4. **加日历识别用例**：截图 → 期望日期列表。这需要 mock 一个能读图的步骤，是更高阶的 eval（agent 行为 eval），等你熟悉了这套再动。
