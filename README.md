# My Skills

## 技能列表

### ticktick-todo
操作电脑打开滴答清单软件并创建待办事项。当用户需要自动化打开滴答清单并添加新任务时调用。

### beautiful-mermaid
使用beautiful-mermaid库美化和渲染mermaid流程图，支持多种主题。当用户需要美化现有mermaid图或创建更漂亮的流程图时调用。

### normalizing-note
将用户的原始思绪记录（如碎片化想法、临时笔记、粘贴内容）转化为结构清晰、便于长期阅读和理解的 Markdown 笔记。当用户描述需要整理笔记时使用。

### pdf-processor
PDF 页面操作专家，支持复制、删除、移动指定页面

### superpowers
当要求从头开始搭建一个软件的时候触发，指导一步步完成软件的搭建。

### video-downloader
Downloads videos from various sites using yt-dlp. Invoke when user provides a video link and requests to download it.

### anysearch-skill
Real-time search engine supporting web search, vertical domain search (23 domains), parallel batch search, and URL content extraction.

### youtube-downloader
Download YouTube videos with customizable quality and format options. Use this skill when the user asks to download, save, or grab YouTube videos.

### fce-vocab-context
生成「FCE 词汇实战示范文」交互学习页——把 FCE / B2 First 高频词塞进一篇 90–120 词的短文，配中英双语、12 个词条精讲（音标 / 搭配 / 例句 / 易错点 / 升级替换）、写作迁移模板与高频易错点。覆盖 9 种体裁（人物传记 / 地点旅行 / 经历叙述 / 观点议论 / 建议信 / 评价评论 / 报告 / 活动节日 / 科技媒体），体裁与词表通过 `assets/pick.py` 随机轮换、互不重复。当用户抱怨「背了 FCE 单词记不住、写作文不会用」，或要求「找一篇包含 FCE 单词的例文」「再来一篇 / 换个主题」时调用。

### youtube-summary
根据 YouTube 视频链接抓取字幕，并生成中文总结。当用户给出 YouTube 链接（youtube.com / youtu.be）并要求「总结 / 摘要 / 提炼要点 / 讲了什么 / 翻译成中文」，或要求对视频做读书笔记、内容提炼、观点整理时使用。支持短视频、长视频、中文与外文视频，支持一次处理多个链接。产出含一句话总结、5–8 条带 `[mm:ss]` 时间戳的核心要点、分节详细内容、关键金句与术语表。**与 `youtube-downloader` 的区别：那个负责把视频下载到本地，这个负责「读懂并总结内容」。**

