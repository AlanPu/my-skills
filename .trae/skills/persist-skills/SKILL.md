---
name: persist-skills
description: 将定义好的全局技能保存到github
---

# persist-skills

## 指令

### 1. 复制技能
- 将 ~/.trae-cn/skills/ 目录下的所有技能完整复制到 ./skills/ 目录下
- 包括每个技能目录下的所有文件（如 SKILL.md、脚本文件、依赖文件等）
- 确保每个技能的所有关联文件都被完整复制，不遗漏任何文件
- 对于已存在的技能，覆盖更新以确保与本地版本一致

### 2. 更新文档
- 扫描 ./skills/ 目录下的所有技能，更新 README.md 文档

### 3. 保存到 Github
- 将改动 commit 并 push 到 Github