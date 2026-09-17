# youtube-summary

一个给 AI 编程助手用的技能（Skill）：**给一个 YouTube 链接，抓它的字幕，产出一份带时间戳跳转的中文总结。**

适用于 Claude Code、WorkBuddy，以及任何支持 Skill / 可调用本地脚本的 agent 环境。

```
用户：帮我总结一下这个视频 https://youtu.be/FwOTs4UxQS4

助手：抓字幕 → 按规范产出 → 中文总结（含分节要点 + 关键金句 + [mm:ss] 跳转链接）
```

## 它产出什么

一份结构化的中文总结，包含：

- **一句话总结** —— 这个视频到底讲了什么、核心结论是什么
- **核心要点** —— 5–8 条，每条挂一个可点击的时间戳跳回原视频核对
- **详细内容** —— 按视频自带的章节（如果有）或话题分节展开
- **关键金句** —— 原话引用 + 时间戳
- **术语表** —— 视频涉及的专业名词

示例见 [`examples/example-summary.md`](examples/example-summary.md)。

## 为什么不是「随便写个摘要」

四个设计取向，决定了它的输出质量：

1. **优先取原语言字幕，而不是机翻字幕。**
   YouTube 的机翻中文字幕（`zh-Hans`）是二次加工产物，拿它做中文总结等于「翻译的翻译」。
   脚本会优先选人工字幕，其次选原语言的自动转写（`xx-orig`），尽量避免误差累积。

2. **强制标注时间戳。**
   每一条要点都能一键跳回原视频对应位置。这是它区别于普通摘要的核心价值 ——
   读完能核对，而不是只能信。

3. **区分中英文字幕的排版差异。**
   YouTube 自动字幕（英文）和人工字幕（中文）的 json3 结构**完全不同**：
   前者靠 `aAppend` 事件标记换行、单词会在换行处被粘连；后者每个 event 自成一行、完全没有换行标记。
   脚本会自动判别两种模式，并分别处理拼接空格（英文补空格，中文不补）。

4. **段落切得细，让时间戳准。**
   自动字幕没有标点，无法在语义边界切分，段落必然断在句中。因此把段落控制在 800 字符以内
   （中文按密度再缩放），让「段落首时间戳」与「该段实际讨论内容」的偏差尽量小。

## 安装

```bash
git clone <repo> ~/youtube-summary-skill
```

### 1. 装依赖

只有一项：**yt-dlp**（脚本本身是纯标准库实现，不需要任何 Python 包）。

```bash
pipx install yt-dlp
# 或
python3 -m venv ~/.venvs/yt && ~/.venvs/yt/bin/pip install -U yt-dlp
# 或（macOS）
brew install yt-dlp
```

可选装 **Node**，让 yt-dlp 解算 YouTube 签名（拿更完整的格式与字幕）；不装只会有条告警。

### 2. 注册为技能

把仓库放进你所用的 agent 的技能目录即可，例如：

```bash
ln -s ~/youtube-summary-skill ~/.workbuddy/skills/youtube-summary   # WorkBuddy
ln -s ~/youtube-summary-skill ~/.claude/skills/youtube-summary      # Claude Code
```

用符号链接是为了让改动即时生效；若环境不跟随符号链接，直接复制目录。

## 用法

技能由助手自动触发（给出 YouTube 链接并说「总结」即可），也可以直接调脚本：

```bash
# 抓字幕 → 结构化 JSON
python3 scripts/fetch_transcript.py "https://youtu.be/FwOTs4UxQS4" --out /tmp/video.json

# 只看看这个视频有哪些字幕语言
python3 scripts/fetch_transcript.py "https://youtu.be/FwOTs4UxQS4" --list-subs

# 只要段落纯文本，便于肉眼核对
python3 scripts/fetch_transcript.py "https://youtu.be/FwOTs4UxQS4" --paragraph-only

# 指定字幕语言（默认自动选原语言）
python3 scripts/fetch_transcript.py "https://youtu.be/FwOTs4UxQS4" --lang zh-Hans
```

支持的输入：`youtube.com/watch?v=`、`youtu.be/`、`/shorts/`、`/live/`、`/embed/`、
`music.youtube.com`，以及裸的 11 位视频 ID。带 `&t=` `&list=` 等参数会自动忽略。

### 输出 JSON 的关键字段

| 字段 | 说明 |
|------|------|
| `video.chapters` | 视频自带章节，有的话优先按它组织总结结构 |
| `subtitle.lang` / `kind` / `is_original` | 字幕语言、是人工还是自动、是否原语言 |
| `transcript.paragraphs` | 正文，每项 `{t, ts, text}` |
| `chunks` / `chunked` | 超长视频会自动分块，交给上层做 map-reduce 总结 |

## 代理配置（重要）

脚本会自动探测常见的本地代理端口（7890 / 7897 / 7891 / 10809 / 6152 …），
用 HTTP CONNECT 握手确认哪个真能建起到 YouTube 的隧道，选中后
**通过 `--proxy` 参数显式传给 yt-dlp**，并把已验证可用的代理复用给后续的字幕下载。

**为什么不直接靠环境变量**：不少受管运行环境（沙箱、CI、容器、某些 agent 平台）会强制覆盖
`HTTP_PROXY` / `HTTPS_PROXY` 指向一个不可用端口，此时 yt-dlp 报
`Tunnel connection failed: 502 Bad Gateway`，而同一个代理地址显式传参却完全正常。

手动指定：`--proxy http://127.0.0.1:7890`，或设 `YT_SUMMARY_PROXY`。
代理不在候选端口里的，也走这两个入口。

## 环境变量

| 变量 | 用途 |
|------|------|
| `YT_DLP` | 手动指定 yt-dlp 可执行文件路径 |
| `YT_SUMMARY_PROXY` | 手动指定代理 URL（优先级低于 `--proxy`） |
| `YT_SUMMARY_NODE` | 手动指定 Node 可执行文件路径 |

## 已知限制

- **无字幕的视频无法处理**（脚本以 exit 5 退出并告知）。
  如需覆盖，可扩展一条「下载音频 + 本地 Whisper 转写」的路径，目前未实现。
- 自动字幕没有标点、全小写，且存在同音词误识别（人名、术语尤甚）。
  总结会尽量修正明显错讹并在必要处标注，但无法保证完全准确。
- 超长视频（转写超过 18 万字符）会走分块摘要，二次汇总可能损失少量细节。

## 目录结构

```
SKILL.md                     技能说明（触发条件、执行流程、错误处理）
prompts/summarize.md         中文总结的输出规范（结构模板 + 五条铁律）
scripts/fetch_transcript.py  字幕抓取与解析（纯标准库，唯一外部依赖是 yt-dlp）
examples/example-summary.md  真实产出的示例总结
```
