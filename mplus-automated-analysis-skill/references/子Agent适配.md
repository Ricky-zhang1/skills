# 子 Agent / 对抗性审查适配

本 Skill 不把某个平台的子 Agent API 写进统计 Runtime，因为 Claude Code、Codex、WorkBuddy、OpenCode 的多 Agent 接口可能变化。Skill 层采用能力检测：

1. 如果当前 Agent 支持创建只读子 Agent / task / fork context，启动一个独立上下文并加载 `agents/对抗性审查员.md`；
2. 将用户原始需求、分析设计清单、变量对应表、最终 `.inp/.out`、模型比较表、分析报告提供给审查 Agent；
3. 禁止审查 Agent 修改文件；
4. 审查返回 `PASS` 时仅写入内部 manifest，不向普通用户展示；
5. 返回 `FAIL` 时，主 Agent先尝试自动修复并重跑；无法修复才生成用户可见质量问题报告；
6. 如果平台没有独立子 Agent 能力，主 Agent必须使用同一审查清单进行第二遍“只读、反方立场”检查。

平台适配器应只负责“如何创建隔离上下文”，不能改变审查标准。
