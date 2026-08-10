# Mplus自动化分析skill

[![Release](https://img.shields.io/badge/release-v0.3.0--alpha.9-0f766e)](https://github.com/Ricky-zhang1/skills/releases/tag/v0.3.0-alpha.9)
[![License](https://img.shields.io/badge/license-MIT-2f855a)](./LICENSE)

![Mplus 自动化分析流程](./assets/mplus-analysis-workflow.png)

做 Mplus 时，很多时间花在模型前后。SAV、Excel 和 TXT 要整理，变量顺序要核对，跑完模型还要把输出变成能用的表。这个 Skill 把这些步骤接起来，让 Agent 帮你准备数据、写 Mplus 代码、运行本机 Mplus，再整理 Excel、CSV 和分析报告。

适合写学位论文、课程论文和小型实证研究，也适合把一份旧的 Mplus 分析重新整理一遍。

## 安装

### Codex

在 Codex 中输入下面这句。

```text
$skill-installer install https://github.com/Ricky-zhang1/skills/tree/main/mplus-automated-analysis-skill
```

安装完成后重启 Codex。手动安装时，把整个 `mplus-automated-analysis-skill` 文件夹放进 `~/.codex/skills/`。

### Claude Code

下载或克隆本仓库，把整个 `mplus-automated-analysis-skill` 文件夹放进 `~/.claude/skills/`，然后重启 Claude Code。

### 其他 Agent

在平台的 Skill 或插件导入界面选择整个文件夹。导入时请保留 `SKILL.md`、`references`、`runtime`、`scripts` 和 `assets`。

## 第一次使用

电脑需要已经安装可正常使用的 Mplus。打开 Agent 后，先说。

```text
请检测我电脑上的 Mplus 和运行环境，完成一次自检。
```

Skill 会先自动查找 Mplus。找不到时，它会再问你安装位置。Python 和读写 SAV、Excel、CSV、DTA 所需的组件也会一并检查，缺少时会说明用途并询问是否安装。

你通常不需要自己打开终端。想手动检查时，可运行下面的命令。

macOS

```bash
./scripts/运行Mplus分析.sh doctor
```

Windows PowerShell

```powershell
.\scripts\运行Mplus分析.ps1 doctor
```

## 怎样开始

把数据文件和研究问题交给 Agent 就可以。当前可读取 SAV、Excel、CSV、DTA、TXT 和 DAT。

```text
请读取这份 SAV 数据，检查变量和缺失值。我想用第 3 到第 8 题做 CFA。
```

```text
请把这份 TXT 数据整理成 Excel 能直接打开的 CSV，同时保留原始变量顺序。
```

```text
请用这几个指标做 LPA，比较一到五类，并说明类别数的选择依据。
```

```text
这是一个三波追踪数据。请先判断能否做基础潜在增长模型，再帮我完成分析。
```

## 可以做什么

| 分析 | 当前用途 |
| --- | --- |
| 数据整理 | SAV、Excel、CSV、DTA、TXT 与 DAT 的读取、检查和转换 |
| EFA 与 CFA | 连续或分类指标的探索性与验证性因子分析 |
| SEM 与中介 | 结构方程模型、观测变量中介和潜变量中介 |
| 基础线性增长 | 多波数据的基础潜在增长模型 |
| LPA | 连续指标的一到 K 类比较与类别归属导出 |
| LCA | 分类指标的一到 K 类比较与类别归属导出 |
| 进阶模型 | 测量不变性、多层、GMM、LCGA、LTA、RI-CLPM、ESEM 与复杂抽样的引导式支持 |

每个分析项目都会单独保存，原始数据保持不动。常见产物包括数据检查结果、变量对照表、Mplus 输入文件、原始输出、Excel 和 CSV 结果表，以及带方法依据的中文报告。

LPA 和 LCA 的报告会同时看信息准则、类别规模、后验概率、模型检验和研究问题，再给出类别数建议。样本量偏小、模型估计异常或数据设定影响结果时，报告会把原因和可参考的处理方向写清楚。

## 使用时的小提醒

- 分析前准备好变量含义、缺失值编码和研究问题。时间点、分组和权重等信息也请一并告诉 Agent。
- 实际研究中，模型选择仍要回到理论、量表和研究设计。报告里的方法来源可直接作为进一步核对的入口。
- 项目文件保留在你的电脑上。分享结果前，检查文件里是否还有需要隐藏的个人信息。

## 测试环境

本项目已在 Mac M 系列芯片和 Mplus 8.3 上完成端到端测试。Windows、Mac Intel 与其他 Mplus 版本提供安装检测和自检支持，第一次使用时建议先跑一次自检。

## 版本与反馈

[下载最新版本](https://github.com/Ricky-zhang1/skills/releases/tag/v0.3.0-alpha.9)

[查看更新记录](./CHANGELOG.md)
[提交问题](https://github.com/Ricky-zhang1/skills/issues)

反馈时附上 Agent 平台、操作系统、Mplus 版本、数据格式、分析类型和完整报错信息，定位会快很多。

## License

[MIT](./LICENSE)

---

# English

Mplus Automated Analysis Skill helps an AI Agent take a project from raw data to a usable Mplus result. It prepares SAV, Excel, CSV, DTA, TXT, DAT and related files, creates Mplus syntax, runs local Mplus, and organizes Excel, CSV and report outputs.

## Install

### Codex

```text
$skill-installer install https://github.com/Ricky-zhang1/skills/tree/main/mplus-automated-analysis-skill
```

Restart Codex after installation. For a manual installation, copy the complete `mplus-automated-analysis-skill` folder to `~/.codex/skills/`.

### Claude Code and other agents

Copy or import the complete folder through the platform's Skill interface. Claude Code users can place it in `~/.claude/skills/`. Keep `SKILL.md`, `references`, `runtime`, `scripts` and `assets` together.

## First run

Install and activate Mplus on your computer, then ask the Agent.

```text
Please find Mplus on this computer, check the required environment, and run a self-test.
```

The Skill looks for Mplus automatically and asks for a location only when needed. It also checks the Python components used to read common data files.

## Workflows

It supports data preparation, EFA, CFA, SEM, observed and latent mediation, basic linear growth models, LPA, LCA, and guided support for measurement invariance, multilevel models, GMM, LCGA, LTA, RI-CLPM, ESEM and complex surveys.

Give the Agent a data file and a plain-language question. For example.

```text
Read this SAV file and run a CFA using items 3 through 8.
```

```text
Compare one through five latent profiles for these indicators, then explain the preferred solution.
```

Each project keeps its source data intact and produces Mplus files, output, spreadsheets and a report with method references.

## Tested environment

End-to-end testing has been completed on Apple Silicon Macs with Mplus 8.3. Windows, Intel Macs and other Mplus versions include setup detection and self-test support.

[Latest release](https://github.com/Ricky-zhang1/skills/releases/tag/v0.3.0-alpha.9)

[Changelog](./CHANGELOG.md)
[Issues](https://github.com/Ricky-zhang1/skills/issues)

## License

[MIT](./LICENSE)
