# 读模组与带团资料整理

把 DND、COC 等跑团模组整理成互联、具名、可朗读的主持资料。技能名：`trpg-module-prep`。

这份 skill 来自“读模组工具流”Canvas，包含实际执行步骤、按需阅读的规范和只读结构检查脚本。它由使用技能的 AI 读取原文并执行整理；检查脚本只检查已生成资料，不负责生成全部内容。

## 使用

安装后，在 Codex 输入：

```text
使用 $trpg-module-prep 整理我提供的模组资料。
原文路径：填写实际路径
输出位置：填写实际模组目录
整理范围：全部模组，或指定章节／资料类别
保留已有手工修改，完成互联、逐场景主持CG、逐形态NPC客观外貌描写及验收。
```

继续更新时说明要修改的章节、对象或资料类别，技能会同步受影响的索引与引用。

## 安装

可以在 Codex 中让内置安装技能处理这个仓库：

```text
使用 $skill-installer 从 https://github.com/yeklongdi/trpg-module-prep 安装 skills/trpg-module-prep。
```

也可以将本仓库的 `skills/trpg-module-prep` 文件夹复制到项目的 `.agents/skills/trpg-module-prep`，保留整个目录。技能入口是 `SKILL.md`，参考规范、UI元数据和检查脚本一起安装。[OpenAI 官方技能文档](https://learn.chatgpt.com/docs/build-skills)说明了本地发现和显式调用方式。

## 固定输出结构

```text
模组名/
├─ 带团文档/
├─ 怪物/
├─ 模组/
├─ 资料附件/
├─ npc/
└─ 模组总索引.md
```

- 每章：文字流程、白板流程、主持CG三份资料，两两互引同一个S场景。
- NPC：人设、数据、任务与地点三份汇总，两两互引同一个N角色。
- 怪物：数据、描述两份汇总，与S场景遭遇互引。
- 管理信息合并总索引；事件清单、任务、线索和遭遇合并章节资料；图片与备份放资料附件。不按单个人物、形态或场景拆文件。

标题写编号加实际名称，折叠大纲也能认出角色、怪物和场景。每个场景都有触发条件、足够朗读正文和主持人备注；NPC除了性格分析与初见／互动，还要有独立的纯外貌念白，逐一覆盖原文实际存在的形态。

## 仓库内容

| 文件 | 用途 |
| --- | --- |
| `skills/trpg-module-prep/SKILL.md` | 执行入口、步骤、缺口处理与交付要求 |
| `skills/trpg-module-prep/agents/openai.yaml` | 技能名称、简介与默认调用提示 |
| `skills/trpg-module-prep/references/structure-and-index.md` | 五目录、总索引、具名标题与内部引用 |
| `skills/trpg-module-prep/references/chapters-and-cg.md` | 章节互联、白板、逐场景主持CG |
| `skills/trpg-module-prep/references/npc-and-monsters.md` | NPC性格、各形态外貌、立绘、怪物及遭遇 |
| `skills/trpg-module-prep/scripts/check_module.py` | 只读结构与覆盖检查，Python 3.9以上，无第三方依赖 |
| `workflow/读模组工具流.canvas` | 去掉个人机器路径的可视工作流 |
| `tests/test_check_module.py` | 虚构小模组的正常与缺陷验证 |

## 结构检查

在仓库根目录执行：

```text
python skills/trpg-module-prep/scripts/check_module.py "实际模组根目录"
python skills/trpg-module-prep/scripts/check_module.py "实际模组根目录" --json
python -m unittest discover -s tests -v
```

检查五目录边界、文件分类、具名标题、内部链接和锚点、同编号资料导航、S节点覆盖、NPC索引形态与外貌段落、初见／互动正文、Canvas ID及连线端点。NPC索引的【已确认形态】列使用中文分号分隔多形态，详见参考规范。

退出码：0为结构通过，1为发现结构问题，2为输入或读取失败。脚本只输出到终端，不移动、删除或修改模组文件，也不另生成报告。

原文真实性、实际阅读覆盖、性格依据、外貌是否纯客观、CG是否足够、规则版本和秘密边界仍需实际核对。通过脚本不能单独代表模组已经完整可带团。

仓库只包含通用技能、格式示例、虚构测试和工作流，不包含任何模组正文、个人笔记或个人机器绝对路径。
