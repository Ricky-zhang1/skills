# Mplus自动化分析skill

[![Release](https://img.shields.io/badge/release-v1.0beta-0f766e)](https://github.com/Ricky-zhang1/skills/releases/tag/v1.0beta)
[![License](https://img.shields.io/badge/license-MIT-2f855a)](./LICENSE)

![Mplus 自动化分析流程](./assets/mplus-analysis-workflow.png)

做 Mplus 时，很多时间花在模型前后。SAV、Excel 和 TXT 要整理，变量顺序要核对，跑完模型还要把输出变成能用的表。这个 Skill 把这些步骤接起来，让 Agent 帮你准备数据、写 Mplus 代码、运行本机 Mplus，再整理 Excel、CSV 和分析报告。

适合写学位论文、课程论文和小型实证研究，也适合把一份旧的 Mplus 分析重新整理一遍。

## 它能替你做什么

| 环节 | Skill 会完成的事 |
| --- | --- |
| 环境准备 | 自动查找本机 Mplus，识别版本，检查 Python 和常用数据读取组件。只有确实缺少东西时才请你确认。 |
| 数据整理 | 读取 SAV、Excel、CSV、DTA、TXT、DAT，检查变量、缺失值、异常编码和样本量，生成 Mplus 可读的数据。 |
| 变量管理 | 建立变量对照表和个案 ID 对照表，保留原始变量顺序，让 Mplus 内部变量能准确回到原数据。 |
| 分析设计 | 根据研究问题区分测量模型、结构模型、混合模型和纵向模型，只补问会影响分析的关键信息。 |
| 模型运行 | 生成可追溯的 Mplus 代码，在本机运行，并把原始输出按项目保存。 |
| 结果交付 | 导出 Excel、CSV、模型比较表、个体类别归属、代码说明、中文报告和方法文献链接。 |

## 从数据到报告

1. Agent 先读取数据，核对变量、缺失值、样本量和数据格式。
2. 根据研究问题和数据结构，选择合适的分析路线。
3. 生成可追溯的 Mplus 代码，并在本机运行 Mplus。
4. 读取模型输出，整理成 Excel、CSV 和中文分析报告。
5. 把代码、结果表、模型判断和方法来源放进同一个项目文件夹，方便回看和写论文。

## 它怎样把关

- 常用分析代码来自登记过的 Mplus 官方示例和方法文献，报告会附上相应来源。
- 数据转换、变量顺序和样本量会在运行前核对，模型输出会在运行后再核对一次。
- LPA 与 LCA 会先比较不同类别数，再结合信息准则、类别规模、后验概率、模型检验和研究问题给出建议。
- 原始数据保持不动。每次分析都有独立项目文件夹，代码和结果可以逐项回查。
- 样本量偏小或模型估计出现问题时，报告会说明它对结果意味着什么，并给出可参考的处理方向。

## 安装和第一次使用

把下面整段复制给你正在使用的 Agent。Codex、Claude Code、WorkBuddy 和其他支持 Skill 的 Agent 都用这一段。

```text
请从 GitHub 安装并启用这个 Skill。
https://github.com/Ricky-zhang1/skills/tree/main/mplus-automated-analysis-skill

请自行选择适合当前平台的安装方式，下载完整 Skill 并完成部署。安装完成后，检查这个 Skill 是否已经可以使用。

然后检测我电脑上的 Mplus、Python 和读取 SAV、Excel、CSV、DTA、TXT、DAT 所需组件。请先自动查找 Mplus。只有找不到时，再问我它的安装位置。缺少组件时，先告诉我准备安装什么，得到我的确认后再安装。最后完成一次自检，并告诉我结果。
```

电脑需要已经安装并激活 Mplus。之后只要把数据文件和研究问题交给 Agent 即可。

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
- 做研究时，模型选择仍要回到理论、量表和研究设计。报告里的方法来源可直接作为进一步核对的入口。
- 项目文件保留在你的电脑上。分享结果前，检查文件里是否还有需要隐藏的个人信息。

## 测试环境

本项目已在 Mac M 系列芯片和 Mplus 8.3 上完成端到端测试。Windows、Mac Intel 与其他 Mplus 版本提供安装检测和自检支持，第一次使用时建议先跑一次自检。

## 版本与反馈

[下载最新版本](https://github.com/Ricky-zhang1/skills/releases/tag/v1.0beta)

[查看更新记录](./CHANGELOG.md)
[提交问题](https://github.com/Ricky-zhang1/skills/issues)

反馈时附上 Agent 平台、操作系统、Mplus 版本、数据格式、分析类型和完整报错信息，定位会快很多。

## License

[MIT](./LICENSE)

---

# English

Mplus Automated Analysis Skill helps an AI Agent take a project from raw data to a usable Mplus result. It prepares SAV, Excel, CSV, DTA, TXT, DAT and related files, creates Mplus syntax, runs local Mplus, and organizes Excel, CSV and report outputs.

## What it does

| Stage | What the Skill handles |
| --- | --- |
| Setup | Finds local Mplus, identifies its version, and checks Python and data-reading components. |
| Data | Reads common data files, checks variables and missing-value codes, and prepares Mplus-ready data. |
| Mapping | Creates variable and case-ID maps so Mplus output can be joined back to the source data. |
| Design | Routes the research question to an appropriate model family and asks only for information that changes the analysis. |
| Output | Produces Mplus syntax, raw output, spreadsheets, result tables, a report, and method references. |

## From data to report

1. The Agent reads the data and checks variables, missing values, sample size, and file format.
2. It selects an analysis route from the research question and data structure.
3. It creates traceable Mplus syntax and runs Mplus locally.
4. It reads the output and prepares spreadsheets and a report.
5. It saves the code, results, model decisions, and method sources in one project folder.

## How it checks the work

- Standard code is linked to registered Mplus examples and method references.
- Data conversion, variable order, and sample size are checked before the run. Output is checked again afterwards.
- LPA and LCA recommendations consider fit information, class size, posterior probabilities, model tests, and the research question.
- Every analysis uses a separate project folder and keeps the source data intact.

## Install and first run

Copy this message into your AI Agent. It works for Codex, Claude Code, WorkBuddy, and other Agents that support Skills.

```text
Please install and enable this Skill from GitHub.
https://github.com/Ricky-zhang1/skills/tree/main/mplus-automated-analysis-skill

Choose the installation method that fits your current platform, download the complete Skill, and deploy it. Confirm that the Skill is available after installation.

Then check this computer for Mplus, Python, and the components needed to read SAV, Excel, CSV, DTA, TXT, and DAT files. Look for Mplus automatically first. Ask me for its location only when you cannot find it. Tell me what needs to be installed and wait for my approval before installing anything. Finish with a self-test and report the result.
```

Mplus needs to be installed and activated on the computer. After that, give the Agent your data file and research question.

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

[Latest release](https://github.com/Ricky-zhang1/skills/releases/tag/v1.0beta)

[Changelog](./CHANGELOG.md)
[Issues](https://github.com/Ricky-zhang1/skills/issues)

## License

[MIT](./LICENSE)
