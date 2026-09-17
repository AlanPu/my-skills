---
name: youtube-summary
description: 根据 YouTube 视频链接抓取字幕，并生成中文总结。当用户给出 YouTube 链接（youtube.com / youtu.be）并要求「总结 / 摘要 / 提炼要点 / 讲了什么 / 翻译成中文」，或要求对视频做读书笔记、内容提炼、观点整理时使用。支持短视频、长视频、中文与外文视频，支持一次处理多个链接。
---

# YouTube 视频中文总结

给一个 YouTube 链接，抓它的字幕，产出一份**中文总结** —— 带分节要点、关键金句，
以及可直接跳回原视频对应位置的时间戳链接。

## 关于路径

下文用 `<skill_dir>` 指代**本 skill 的安装目录**（即包含本 SKILL.md 的那一层）。
各平台按实际约定替换即可：

| 平台 | `<skill_dir>` |
|------|--------------|
| Claude Code | `$CLAUDE_SKILL_DIR` |
| WorkBuddy | `~/.workbuddy/skills/<skill-name>` |
| 其他 | 本 skill 的实际安装路径 |

---

## 一、执行流程

### Step 1 · 抓取字幕

用脚本抓字幕。**不要**自己去访问 YouTube 网页或调用任何 YouTube API。

```bash
python3 "<skill_dir>/scripts/fetch_transcript.py" "<视频链接>" --out /tmp/yt-summary.json
```

脚本会把结构化 JSON 写到 `--out` 指定的路径，同时向 stdout 打印一份**精简摘要**
（标题、字幕语言、段落数、字数、是否需要分块）。**读那份精简摘要即可判断下一步**，
不要为了看元数据而重复读整个 JSON 文件。

- 脚本的进度日志走 stderr（前缀 `[fetch_transcript]`），stdout 只有结果，互不干扰。
- 退出码：`0` 成功 / `2` 链接无法识别 / `3` 依赖缺失或网络不通 / `4` 视频不可访问 /
  `5` 该视频没有字幕。**exit 5 要单独处理**，见第五节。
- 若报「找不到 yt-dlp」，按第二节安装后重试。

### Step 2 · 读转写内容

用 Read 工具读 `--out` 写出的 JSON。你要用的字段是：

| 字段 | 用途 |
|------|------|
| `video.title` / `channel` / `duration_human` / `upload_date` / `url` | 写总结开头的元信息 |
| `video.description` | 判断视频主题、是否附了时间轴 |
| `video.chapters` | **有的话优先按它组织内容结构** |
| `video.id` | 拼跳转链接用 |
| `subtitle.lang` / `kind` | 判断原文语言、是人工还是自动字幕（决定要不要补标点） |
| `subtitle.est_tokens` | 判断内容量级 |
| `transcript.paragraphs` | **正文**，每项含 `t`（秒）、`ts`（mm:ss）、`text` |
| `chunks` / `chunked` | 转写超长时出现，走分块策略 |
| `link_template` | 跳转链接的格式模板 |

跳转链接拼法：把 `link_template` 里的 `{video_id}` 换成 `video.id`、
`{seconds}` 换成段落的 `t`。例如 `https://youtu.be/EWvNQjAaOHw?t=492`。

### Step 3 · 按规范写总结

读 `<skill_dir>/prompts/summarize.md`，**严格按其中的结构和铁律**产出中文总结。
那份 prompt 定义了：

- 五条铁律（只依据转写、必标时间戳、中文表达保留术语、补回标点、不编造数字）
- 五类视频类型的差异化结构
- 输出模板（一句话总结 / 核心要点 / 详细内容 / 关键金句 / 术语表）
- 长度控制与超长视频的 map-reduce 策略

### Step 4 · 输出

**默认直接输出到对话**，不写文件。

用户明确要求保存 / 导出 / 给我一个文件时，才把总结写成 Markdown 文件，
命名 `<视频标题>-总结.md`，放在用户当前工作目录下。

### Step 5 · 报告消耗

总结输出完毕后，**在末尾附一行消耗说明**。先跑统计脚本：

```bash
python3 "<skill_dir>/scripts/token_report.py" estimate --transcript /tmp/yt-summary.json
```

然后在总结末尾加上一行，形如：

> 📊 本次消耗约 15.8K tokens（转写进上下文 4.0K + 总结生成与往返 10.2K + 固定开销 1.6K）

几个要点：

- **`--summary` 是可选的。** 不传时脚本按转写体积的比例推算总结大小（实测标定过，够准）。
  如果用户本来就要求保存总结文件，顺手把该文件路径传给 `--summary`，数字会更精确。
- **不要为了统计而额外写一遍总结文件** —— 写文件的工具调用本身也会进上下文，
  多花的 token 比它带来的精度更贵。
- **这是估算值，如实说明。** 脚本给出的区间（如 13.4K – 18.9K）比单点数字更诚实。
- **精确值要滞后一轮。** agent 平台的执行记录在每轮结束后才落盘，所以本轮读不到本轮的精确值。
  如果用户追问「到底花了多少」，在**下一轮**跑 `actual` 模式即可：

  ```bash
  python3 "<skill_dir>/scripts/token_report.py" actual
  ```

  它会给出轮次、缓存命中率、实际新增输入与输出的完整分解。
  注意 `actual` 依赖 agent 平台的执行记录（WorkBuddy 在 `~/.workbuddy/traces/`），
  换平台可能不可用 —— 但只要 `estimate` 能用，这个功能的主动意义就还在。
- 用户若明确说不想看消耗，跳过本步骤。

---

## 二、依赖与安装

### 运行时要求

- **Python 3.9+** —— 脚本本身只用标准库，无需 pip 安装任何 Python 包。
- **yt-dlp** —— 唯一的外部依赖，负责实际抓取。
- **Node（可选）** —— 装了能让 yt-dlp 解算 YouTube 签名，拿到更完整的格式与字幕；
  没装只会打印一条告警，多数视频仍可正常处理。

### 安装 yt-dlp（任选其一）

```bash
# 方式一：pipx（隔离，推荐）
pipx install yt-dlp

# 方式二：装进独立虚拟环境
python3 -m venv ~/.venvs/yt && ~/.venvs/yt/bin/pip install -U yt-dlp

# 方式三：Homebrew（macOS）
brew install yt-dlp

# 方式四：已装在别处，直接告诉脚本路径
export YT_DLP=/path/to/yt-dlp
```

脚本会按 `$YT_DLP` → PATH → 与当前 Python 解释器同目录 → 常见安装位置 的顺序自动定位，
一般装完即可用。yt-dlp 更新频繁（YouTube 经常改接口），遇到解析失败先试 `yt-dlp -U` 升级。

---

## 三、命令行参数速查

```
fetch_transcript.py <url_or_id> [选项]

  --lang LANG      指定字幕语言（en / zh-Hans / ja / ko …），默认自动选原语言
  --out FILE       JSON 写入文件（推荐：避免大输出涌入上下文）
  --list-subs      只列出该视频有哪些字幕语言，不下载
  --paragraph-only 只输出段落纯文本，便于肉眼核对
  --full-text      额外输出拼接好的全文（默认不输出，避免 JSON 翻倍）
  --max-chars N    分块阈值字符数（默认 180000）
  --no-chunks      强制不分块
  --keep-files     保留下载的原始字幕文件（调试用）
  --timeout N      单步网络超时秒数（默认 60）
  --proxy URL      显式指定代理，如 http://127.0.0.1:7890
```

环境变量：`YT_DLP`（yt-dlp 路径）、`YT_SUMMARY_PROXY`（代理）、`YT_SUMMARY_NODE`（Node 路径）。

支持的输入形态（已覆盖）：`youtube.com/watch?v=`、`youtu.be/`、`/shorts/`、`/live/`、
`/embed/`、`music.youtube.com`，以及裸的 11 位视频 ID。
带 `&t=` `&list=` 等参数会自动忽略。

### 消耗统计脚本

```
token_report.py estimate --transcript FILE [--summary FILE] [--json]
    估算本次调用消耗。--transcript 是 fetch_transcript.py 产出的 JSON。
    随时可用，不依赖任何平台特性。

token_report.py actual [--trace FILE] [--last N] [--json]
    从 agent 平台的执行记录读精确消耗（滞后一轮）。

token_report.py list [--limit N]
    列出本会话最近的执行记录。
```

估算口径的标定依据（都在脚本注释里，改动时请一并更新）：

| 常数 | 值 | 来源 |
|------|----|------|
| JSON 字符-token 比 | 3.60 | 带行号的转写 JSON 14,184 字符 = 实测 3,937 token |
| 总结开销倍数 | 3.7 | 生成 + 回灌 + 推理，实测单点标定 |
| 固定开销 | 1,600 | 指令、工具结果、收尾轮 |

实测校验：对那段 10 分钟视频的演示，脚本估 15.8K，实际 15.5K，偏差 1.7%。

---

## 四、网络与代理

### 脚本怎么选代理

脚本自己探测常见的本地代理端口（Clash 系的 7890 / 7897 / 7891、v2rayN 的 10809、
Surge 的 6152 等），用 HTTP CONNECT 握手确认哪个**真能建起到 YouTube 的隧道**，
选中后**通过 `--proxy` 参数显式传给 yt-dlp**，并把「已验证可用的代理」复用给后续的字幕下载
（避免对已知失效的候选重复超时 —— 实测不做这步，耗时会被拉到两分钟以上）。

若一个都没探测到，会退回直连 —— 适用于本机本身就在墙外、或走透明代理的场景。
代理端口不在候选列表里的，用 `--proxy` 或 `YT_SUMMARY_PROXY` 显式指定。

### 为什么不用环境变量

**这是个通用陷阱，不是某台机器的问题。** 不少受管运行环境（沙箱、CI、容器、某些 agent 平台）
会强制覆盖 `HTTP_PROXY` / `HTTPS_PROXY`，把它们指向一个不可用的端口。此时：

- yt-dlp 报 `Tunnel connection failed: 502 Bad Gateway`
- 而**同一个代理地址**用 `--proxy` 显式传入却完全正常

判别方法：手工 `curl -x http://127.0.0.1:7890 https://www.youtube.com` 能通，
但设 `HTTPS_PROXY=http://127.0.0.1:7890` 再 curl 同一地址就不通 —— 说明环境变量被劫持了。

所以本 skill 一律显式传参，不依赖环境变量。

---

## 五、失败与边界情况

| 症状 / 退出码 | 原因 | 处理 |
|--------------|------|------|
| exit 3，日志出现 `找不到 yt-dlp` | 依赖未安装 | 按第二节安装；或设 `YT_DLP` 指定路径 |
| exit 3，日志出现 `502 Bad Gateway` | 代理挂了或端口变了 | 让用户确认代理软件在跑；或 `--proxy` 手动指定端口 |
| exit 3，日志出现 `Sign in to confirm you're not a bot` | YouTube 反爬 | 属于风控，重试一次；仍失败则告知用户稍后再试 |
| exit 3，日志出现 `Unable to connect` / 全部候选失败 | 网络不通 | 提示用户检查代理，**不要**改成直连硬试 |
| exit 4 | 视频私有 / 已删除 / 地区限制 / 会员专享 | 明确告诉用户是哪种，不要假装能处理 |
| **exit 5** | **该视频没有字幕** | 见下方「无字幕视频」 |
| 日志出现 `No supported JavaScript runtime` | 没找到 Node | 不影响大多数视频；如需补全可装 Node |
| 视频有字幕但抓不到 | yt-dlp 版本过旧 | 提示用户 `yt-dlp -U` 升级后重试 |
| JSON 异常大 | 2 小时以上的长视频 | 属正常，已自动走 `chunks` 分块 |

### 无字幕视频

脚本会以 exit 5 结束并打印视频标题。此时**如实告知用户该视频没有字幕**，
并给出可选方案：

1. 换一个有字幕的版本（很多视频有多个转载版本，其中一个带字幕）；
2. 如果视频本身是外语，确认是否只是没有原语言字幕而存在自动字幕
   （脚本已优先尝试自动字幕，报 exit 5 说明两者都没有）；
3. 若用户确实需要处理，告知可以提供该视频**下载好的音频文件**，用本地
   Whisper 转写后再总结 —— 属于额外步骤，需用户明确要求才做。

**不要**在无字幕时凭视频标题和你的先验知识去「推测」视频内容写成总结。
这是最严重的错误，直接违反第一条铁律。

### 中文视频

脚本已适配中文：中文人工字幕的 json3 结构与英文自动字幕不同（**没有换行标记事件**），
脚本会自动判别两种排版模式；同时中文字符间不会插多余空格，段落阈值也会按中文密度缩小。
你只需正常按 prompt 输出中文总结即可（注意简繁统一，`zh-Hant` 的源输出用简体）。

### 机翻字幕

若 `subtitle.kind` 是 `auto` **且** `subtitle.is_original` 为 `false`，
说明拿到的是 YouTube 机翻字幕，准确度有限 —— 按 prompt 第七节的要求处理
（重结构、轻字面，并在开头注明字幕来源）。

---

## 六、批量处理

用户一次给多个链接时：

1. **串行**抓取（并发访问 YouTube 容易触发限流），每个 `--out` 到不同临时文件；
2. 逐个生成中文总结；
3. 如果是同主题的多集视频，末尾追加一节「多集串联小结」，
   指出几集之间的承接关系与整体脉络。

若某个链接失败，**跳过并在最后列出失败清单**，不要因一个失败而中断整批。

---

## 七、绝对不要做的事

- ❌ 不要用 `WebFetch` 去抓 YouTube 页面来「读内容」—— 页面里没有字幕，只会浪费 token。
- ❌ 不要在**没有字幕**时靠标题推测内容写总结。
- ❌ 不要把整份 JSON 再读一遍只为确认已经打印在 stdout 上的元数据。
- ❌ 不要省略时间戳 —— 那是这份总结相较普通摘要的核心价值。
- ❌ 不要为了凑长度而复述转写，转写里的寒暄、口播广告、跑题闲聊应直接过滤。
