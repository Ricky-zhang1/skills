# Social Science Academic Writing Skills | 社科论文写作辅助技能库

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**English** | [中文](#中文文档)

---

## English

> **AI-powered skills for social science academic writing** - A collection of Claude Code skills designed to assist researchers in literature review, paper writing, and academic research methodology.

### Purpose

This repository stores personal Claude Code skills focused on:
- Academic literature positioning and review
- Social science research methodology
- Paper structure analysis and writing
- Knowledge management for researchers

### Available Skills

| Skill | Description | Status |
|-------|-------------|--------|
| [configurational-literature-mapping](./configurational-literature-mapping/) | Transform niche research topics into theoretical hubs using QCA logic and truth table analysis | ✅ Available |
| [mplus-automated-analysis-skill](./mplus-automated-analysis-skill/) | An AI-assisted Mplus workflow for data conversion, standard analysis, result export, and reporting | 1.1beta |

### Installation

#### Quick Install

```bash
cd ~/.claude/skills
git clone https://github.com/Ricky-zhang1/skills.git temp-skills
cp -r temp-skills/configurational-literature-mapping ./
rm -rf temp-skills
```

#### Manual Install

1. Navigate to the skill you want to install
2. Copy the skill folder to `~/.claude/skills/`
3. Restart Claude Code or run `/skill-name` to activate

### Usage

In Claude Code:
```
/configurational-literature-mapping
```

For other AI tools (ChatGPT, Gemini, etc.), see individual skill READMEs for prompt templates.

### Contributing

Contributions are welcome! If you have developed skills for social science research, feel free to:
1. Fork this repository
2. Add your skill following the existing structure
3. Submit a pull request

### Skill Structure

Each skill follows this structure:
```
skill-name/
├── SKILL.md              # Main skill file
├── README.md             # Documentation
└── references/           # Additional resources (optional)
    ├── WORKFLOW.md
    ├── METHODS.md
    └── TEMPLATES.md
```

---

## 中文文档

> **AI 驱动的社科论文写作辅助技能** - 专为社会科学研究者设计的 Claude Code 技能集合，辅助文献综述、论文写作和学术研究方法论。

### 仓库用途

本仓库存放个人开发的 Claude Code 技能，专注于：
- 学术文献定位与综述
- 社会科学研究方法论
- 论文结构分析与写作
- 研究者知识管理

### 已有技能列表

| 技能 | 描述 | 状态 |
|------|------|------|
| [configurational-literature-mapping](./configurational-literature-mapping/) | 组态思维文献定位法 - 使用 QCA 逻辑和真值表分析，将小众研究主题转化为理论枢纽 | ✅ 可用 |
| [mplus-automated-analysis-skill](./mplus-automated-analysis-skill/) | Mplus 自动化分析流程，覆盖数据转换、标准分析、结果导出与报告 | 1.1beta |

### 安装方法

#### 快速安装

```bash
cd ~/.claude/skills
git clone https://github.com/Ricky-zhang1/skills.git temp-skills
cp -r temp-skills/configurational-literature-mapping ./
rm -rf temp-skills
```

#### 手动安装

1. 进入你想安装的技能目录
2. 将技能文件夹复制到 `~/.claude/skills/`
3. 重启 Claude Code 或运行 `/技能名称` 激活

### 使用方法

在 Claude Code 中：
```
/configurational-literature-mapping
```

其他 AI 工具（ChatGPT、Gemini 等）请参考各技能的 README 获取提示词模板。

### 参与贡献

欢迎各位同学使用和贡献！如果你开发了社会科学研究相关的技能：
1. Fork 本仓库
2. 按照现有结构添加你的技能
3. 提交 Pull Request

### 技能结构

每个技能遵循以下结构：
```
技能名称/
├── SKILL.md              # 主技能文件
├── README.md             # 说明文档
└── references/           # 附加资源（可选）
    ├── WORKFLOW.md
    ├── METHODS.md
    └── TEMPLATES.md
```

---

## Changelog | 更新日志

| Date | Skill | Changes |
|------|-------|---------|
| 2025-02-15 | configurational-literature-mapping | Initial release - 组态思维文献定位法 v1.0 |
| 2026-08-11 | mplus-automated-analysis-skill | 1.0beta public release |
| 2026-08-11 | mplus-automated-analysis-skill | 1.1beta reference calibration and expanded regression coverage |

---

## License

MIT License - Feel free to use, modify, and distribute.
