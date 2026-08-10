# Mplus 版本适配

## 适配原则

本 Skill 不为每个版本复制一套基础 `.inp`。已自动化的 EFA、CFA、SEM、中介、基础线性增长、LPA 和 LCA 使用 Mplus 7+ 的共同语言子集：`DATA`、`VARIABLE`、`ANALYSIS`、`MODEL`、`OUTPUT`、`SAVEDATA`、`TECH11` 与 `TECH14`。运行时根据实际版本选择输出解析配置，并只在版本满足条件时提示可用的高级功能。

这意味着“版本较新”不会让 Agent 擅自把新语法塞进旧模型；“版本较旧”也不会仅因版本号被拒绝生成可复现的核心代码。实际运行仍由本机自检和 Mplus 输出决定。

## 版本差异与处理

| 版本 | 官方变化中与本 Skill 有关的部分 | Skill 处理 |
|---|---|---|
| 7.x | BCH、部分三步法、LTA 输出和若干 mixture/贝叶斯扩展在 7.x 小版本中陆续加入；部分安装不提供后续的分类 logits 输出。 | 核心模型使用旧版兼容解析，不依赖分类 logits；高级 mixture 只进入引导模式。 |
| 8.0-8.3 | RDSEM、时间序列、两层随机效应等扩展；8.3 增加复杂抽样 Bootstrap。 | 当前核心模板不调用这些专属功能；8.3 是目前 Mac M 系列实机记录。 |
| 8.4-8.8 | 8.4 简化了多潜类别变量的 mixture 输出；8.7 增加残差回归式 RI-CLPM；8.8 扩展 ESEM/SEM alignment。 | 单一 LPA/LCA 仍使用共同输出段落；RI-CLPM、ESEM 与 alignment 保持引导状态。 |
| 8.9-8.11 | 自动纵向测量不变性、PSEM、ESEM 的 hat 语言、扩展 DSEM/SAVEDATA 等。 | `catalog --mplus` 说明版本能力，但自动编译器不产生这些语法。 |
| 9.0 | 多步 mixture、新的 `AUXILIARY`/`TYPE=IMPUTATION`、两层 Bootstrap、PSEM 扩展等。 | 不把 9.0 专属命令写入核心代码；多步 mixture 与高级抽样保持引导/专家路径。 |
| 9.1 | SEFA/DSEFA、ESEM 并列输出、新版自动不变性模型输出、DSEM3 改进。 | 识别为 `v9.1` 输出配置；ESEM 新输出不交给当前 EFA 解析器，等待单独实现和回归测试。 |

## 输出解析

- Runtime 从 `.out` 中读取 `Mplus VERSION` 并记录为“输出版本”；
- SAVEDATA 同时识别 `Order of variables` 与 `Order and format of variables` 两种段落，不猜测列名；
- 7.x 不以缺少新式 logits 作为失败条件；
- 9.1 的 ESEM 输出布局与普通 `TYPE=EFA` 不同，因此当前不会把它伪装成已支持的自动 ESEM 解析。

## Agent 行为

1. 先运行 `doctor`，读取“版本适配配置”；
2. 再运行 `catalog --mplus <Mplus路径或安装目录>`，按检测到的版本路由分析；
3. 对核心标准模型，生成共同语法并运行本机自检；
4. 对版本专属高级模型，读取本表列出的官方增补说明和对应官方示例，进入引导或专家路径；
5. 输出报告必须记录 Mplus 版本、版本适配配置和解析配置。

## 官方依据

- [Mplus Version History](https://www.statmodel.com/verhistory.shtml)
- [Mplus 8.9-8.11 Language Addendum](https://www.statmodel.com/download/Version%208.9%208.10%20and%208.11%20Addendum.pdf)
- [Mplus 9 Language Addendum](https://www.statmodel.com/download/AddendumV9.pdf)
- [Mplus 9.1 Language Addendum](https://www.statmodel.com/download/Addendum%209.1.pdf)
- [Mplus User's Guide](https://www.statmodel.com/html_ug.shtml)
