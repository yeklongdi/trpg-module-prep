# trpg-campaign-docs ｜ 成稿

把跑团模组原文一次加工成可带的五目录文档。`改文`（trpg-module-refine）与 `读模组`（trpg-module-prep）合并后的单技能版本，两个旧技能已冻结。

## 上游参考

成稿版沿用 [trpg-module-prep](https://github.com/yeklongdi/trpg-module-prep) 的五目录结构与场景编号，增加原文抽取、术语台账和规则数值验收。

## 它做什么

把 PDF、DOCX、TXT 或 Markdown 原文整理为章节文字流程、可朗读的主持 CG、NPC 与怪物资料和总索引。章节路线用 Mermaid 或 Markdown 缩进大纲，NPC 和怪物可在场景内提供速查。已有 Canvas 可以继续互链使用。

## 五条硬规则

1. 表达层可写，信息层禁止——只写可观察的感官层与口语台词，不新增事实、动机、秘密、能力、线索
2. 原文只读——`模组/原文/` 由脚本生成，此后不动
3. 原文规则数值按整理范围保留，新增数值登记来源
4. 工作底稿只统一术语，保留链接目标和标题锚点
5. 结构不动——五目录加总索引，不新增顶层目录

## 安装

```text
使用 $skill-installer 从 https://github.com/yeklongdi/trpg-module-prep 安装 skills/trpg-campaign-docs。
```

也可复制整个 `trpg-campaign-docs` 目录到 Codex 的技能目录。

## 用法

```text
使用 $trpg-campaign-docs 整理我提供的模组。
原文路径：填写实际路径
输出位置：填写模组根目录
规则与版本：DND 5e 2014 / COC 7e / 其他
```

## 脚本

| 脚本 | 作用 | 退出码 |
| --- | --- | --- |
| `scripts/intake.py` | 抽取与版面清洗，产出 `模组/原文/` | 0 / 2 输入错误 / 3 部分失败 |
| `scripts/terms.py` | 术语台账、跨产物替换、可念性扫描 | 0 / 1 拒绝（改数字）/ 2 读取失败 |
| `scripts/check_campaign.py` | 只读校验：结构、数值、术语、字段、引用 | 0 通过 / 1 有问题 / 2 读取失败 |

分章验收时，填写章节文件夹名与该章在 `00-全文.md` 里的原文行范围。

```powershell
python scripts/check_campaign.py "模组根目录" --chapter "第01章 雾港" --source-lines 12-30 --json
```

三个脚本的命令行输出统一用 UTF-8。

## 依赖

- 文本、Markdown、DOCX 使用 Python 3.9 及以上的标准库
- PDF 版面还原：可选 `python -m pip install pymupdf`；不装则走模型直读，须按 `references/intake-and-extraction.md` 补齐 `segments.json` 与分段清单

## 测试

```powershell
python -m unittest discover -s tests -v
```

用例覆盖抽取、分章数值与引用校验、白板兼容、术语替换与撤销、UTF-8 输出等行为。

## 已知边界

- 没有用真实模组验证过大篇幅流程，处理能力按 150 页设计，分章断点写在 `模组/加工进度.json`
- 脚本只查机械可核对的部分，补写是否越界、台词是否泄密只能人看
