# Mplus自动化分析skill

## 中文

![Mplus 自动化分析流程](./assets/mplus-analysis-workflow.png)

这个 Skill 来自一个很朴素的念头。我以前在小红书分享过 Mplus 的分析经验，后来发现大家最常卡住的地方，常常还没到模型本身。

SAV 怎么导出。TXT 怎么变成 Excel 能打开的 CSV。变量顺序乱了怎么办。Mplus 跑完以后，结果怎样整理成可以用的表。很多同学有数据、有研究问题，也愿意学，但前面的格式转换和反复排错已经耗掉了不少时间。

Mplus自动化分析skill 想把这些重复又容易出错的步骤连起来。把数据和研究问题交给 Agent 后，它会读取数据、检查变量与缺失值、转换成 Mplus 可读取的数据、选择受控的分析路线、生成代码、调用本机 Mplus、整理原始输出，并导出 Excel、CSV、中文报告和方法依据。

当前标准流程覆盖 EFA、CFA、SEM、观测变量或潜变量中介、基础线性增长、LPA 和 LCA。对于测量不变性、多层模型、增长混合模型、LTA、RI-CLPM、ESEM 和复杂抽样等更复杂的任务，Skill 会提示它们仍属于引导或专家路径，不会把尚未验证的自动代码包装成可靠结论。

### 使用方式

把数据文件和研究问题直接交给 Agent。它支持 SAV、Excel、CSV、DTA、TXT 和 DAT。

你可以这样说。

> 请读取这份 SAV 数据，检查变量和缺失值。我想用第 3 到第 8 题做 CFA。

> 请把这份 TXT 数据整理成 Excel 能直接打开的 CSV，同时保留原始变量顺序。

> 请用这几个指标做 LPA，比较一到五类。不要自动标准化，分析后告诉我类别数选择依据。

第一次使用时，Skill 会先尝试寻找本机 Mplus，并检查 Python 和数据读取依赖。你不需要预先知道 Mplus 安装路径。缺少环境时，Agent 会先解释需要配置什么，并在得到同意后再处理。

它不会自动删除异常值、填补缺失值或做 Z 标准化。样本量不足也不会阻止运行，但报告会写明风险和规划参考。LPA 与 LCA 的类别数会综合 BIC、TECH11、TECH14、类别规模、后验概率、稳定性和实质解释，最终的理论命名仍由研究者完成。

### 平台与测试边界

Skill 为 macOS 和 Windows 都准备了路径检测和启动方式。

完整的实机测试目前只在 Mac M 系列芯片加 Mplus 8.3 的环境完成，已覆盖 LPA、EFA、连续和分类 CFA、SEM、Bootstrap 中介、LCA 与基础线性增长。Windows 和其他 Mplus 版本保留兼容设计与本机自检流程，但尚未完成真实设备上的完整回归测试。

每台电脑第一次正式使用前都应先运行本机自检。Mplus 是商业软件，用户需要自行拥有合法安装。本项目是纯分享，希望让初学者少花一点时间在格式转换和重复整理上，把精力留给研究设计、理论解释和结果判断。

## English

This Skill grew out of a simple problem. I used to share Mplus tips on Xiaohongshu, and many questions arrived before anyone reached the statistical model itself.

How do I export a SAV file. How do I turn TXT into a CSV that Excel can open. Why did the variable order change. How do I turn raw Mplus output into usable tables. These tasks are small, repetitive, and surprisingly easy to get wrong.

Mplus Automated Analysis Skill connects those steps. Give an Agent a dataset and a research question. It can inspect variables and missing values, convert data into an Mplus-ready format, select a controlled workflow, generate syntax, call the local Mplus installation, preserve raw output, and export Excel files, CSV files, a Chinese report, and methodological references.

The current standard workflow covers EFA, CFA, SEM, observed or latent mediation, basic linear growth, LPA, and LCA. Measurement invariance, multilevel models, growth mixture models, LTA, RI-CLPM, ESEM, and complex survey analysis remain guided or expert paths. The Skill should state those limits clearly.

### How to use it

Give the Agent your data file and your question in plain language. Supported input formats include SAV, Excel, CSV, DTA, TXT, and DAT.

> Please read this SAV file and check the variables and missing values. I want to run a CFA with items 3 through 8.

> Please convert this TXT file into a CSV that Excel can open and preserve the original variable order.

> Please run an LPA with these indicators and compare one through five profiles. Do not standardize variables automatically. Explain the model-selection evidence after the analysis.

The Skill first tries to locate Mplus on the computer and checks the Python environment. Users do not need to know the installation path in advance. When setup is required, the Agent explains the change and asks for permission before configuring its own environment.

The workflow does not silently remove outliers, impute missing values, or standardize variables. Small samples do not block analysis, though the report flags the risk and gives planning guidance. LPA and LCA use BIC, TECH11, TECH14, class size, posterior probabilities, stability, and substantive interpretation together. Researchers still make the final theoretical judgment.

### Platform and testing boundary

The Skill includes path discovery and launchers for both macOS and Windows.

Its complete real-machine testing has so far been conducted only on an Apple Silicon Mac with Mplus 8.3. That testing covered LPA, EFA, continuous and categorical CFA, SEM, bootstrap mediation, LCA, and basic linear growth. Windows and other Mplus versions retain compatibility design and local self-tests, yet they have not completed full real-device regression testing.

Run a local self-test before formal analysis on each new computer. Mplus is commercial software and users need their own valid installation. This is a sharing project for researchers who would rather spend their time on design, theory, and interpretation than on file conversion and repetitive setup.

## License

MIT
