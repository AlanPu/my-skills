---
name: "ticktick-todo"
description: "操作电脑打开滴答清单软件并创建待办事项。当用户需要自动化打开滴答清单并添加新任务时调用。"
---

# 滴答清单待办事项创建器

## 功能
- 打开电脑上的滴答清单软件
- 在滴答清单中创建新的待办事项
- 支持设置任务标题、描述、截止时间等属性

## 使用方法

### 方法 1：官方 URL Scheme（推荐）
使用 TickTick 官方推荐的 URL Scheme 方法，无需权限，直接创建任务：

```bash
# 运行此脚本创建任务
bash .trae/skills/ticktick-todo/create_task_official_url.sh
```

### 方法 2：手动创建
1. 打开 **TickTick** 应用
2. 按 `Command+N` 或点击 "+" 按钮
3. 输入任务内容（例如：`上英语课 明天 09:00 @提子`）
4. 按 Enter 键保存

### 方法 3：邮件发送
通过发送邮件到 TickTick 的专用邮箱创建任务：
- **收件人**：`todo+elkk36yuo2eq@mail.ticktick.com`
- **主题**：任务标题
- **正文**：任务详情（包含时间、执行人等）

## 技术实现

### 官方 URL Scheme 格式
使用 TickTick 官方推荐的 URL Scheme 格式：

```
ticktick://x-callback-url/v1/add_task?title=任务标题&startDate=2026-02-23T09:00:00.000&allDay=false&content=任务内容
```

### 支持的参数
- `title`：任务标题（必填）
- `startDate`：开始时间（ISO 8601 格式）
- `allDay`：是否全天任务
- `content`：任务内容
- `list`：任务所属列表
- `priority`：优先级（0=无，1=低，3=中，5=高）

## 示例
- "帮我用滴答清单创建一个待办事项，标题是 '完成项目报告'"
- "打开滴答清单，添加任务 '购买生日礼物'，设置明天截止"
- "创建一个明天 9 点的英语课任务，执行人是提子"

## 注意事项
- 此技能依赖于电脑已安装滴答清单软件
- 官方 URL Scheme 方法是最可靠的自动化方式，无需任何系统权限
- 邮件方法需要配置邮件发送功能
- 手动创建是最直接的方式，适用于所有场景
