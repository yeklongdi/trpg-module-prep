#!/usr/bin/env python3
"""用虚构小模组验证 intake → terms → check_campaign 全流程。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
NAME = "雾港"
CHAPTER = "第01章 雾港"

SOURCE = """# 第一章 雾港

码头上只剩一盏煤气灯。灯下堆着木箱，箱盖上刻着 DC 15 的暗记。
守夜人受了 1d6+2 点伤害，缩在栈桥尽头。艾尔夫商队的旗号挂在桅杆上。
"""

INDEX = """# 雾港 · 模组总索引

## 备团入口

- [第01章 流程（文字）](带团文档/第01章 雾港/雾港 第01章 雾港 流程（文字）.md)

## 规则与模组概况

DND 5e 2014，单章。

## 章节及实体索引

| 编号 | 名称 | 已确认形态 |
| --- | --- | --- |
| S001 | 码头 | - |
| N001 | 港务官哈维尔 | 人形 |
| M001 | 礁石食尸鬼 | - |

## 资料读取清单

| 文件 | 页数 | 方式 | 已读 |
| --- | --- | --- | --- |
| 雾港.md | 1 | plain | 已读 |

## 待确认事项

- Q001 无。

## 新增数值

| 数值 | 位置 | 来源 |
| --- | --- | --- |
| 十步 | S001 CG | 原文照明范围改写 |

## 完成情况与修改记录

- 建立五目录资料。
"""

TEXT_FLOW = """# 雾港 · 第01章 雾港 · 流程（文字）

## 场景路线

- S001 码头

## S001 码头

玩家进入码头，可选行动：搜查木箱。
箱盖上的暗记需要 DC 15 检定；失败者受 1d6+2 点伤害。

- NPC · N001 港务官哈维尔 · 值守栈桥入口。[[雾港 NPC数据#N001 港务官哈维尔|数据]]
- ![[雾港 NPC人设#^dock-npc]]

**导航**：[[雾港 第01章 雾港 主持CG#S001 码头]]
"""

CG = """# 雾港 · 第01章 雾港 · 主持CG

## S001 码头

### S001 码头 · 触发

玩家进入码头范围，且未触发过 S002。

### S001 码头 · 朗读

> 码头上只剩一盏煤气灯，灯下的木箱刻着 DC 15 的暗记。
> 栈桥尽头缩着一个人，肩膀一抖一抖。艾尔夫商队的旗号挂在桅杆上。

### S001 码头 · 主持人备注

检定 DC 15；失败者受 1d6+2 点伤害。原文 p.3。

**导航**：[[雾港 第01章 雾港 流程（文字）#S001 码头]]
"""

BOARD_LINK = "[[雾港 第01章 雾港 流程（白板）#S001 码头]]"

CANVAS = {
    "nodes": [
        {
            "id": "n1",
            "type": "text",
            "x": 0,
            "y": 0,
            "width": 260,
            "height": 120,
            "text": "## S001 码头\n\n[[雾港 第01章 雾港 流程（文字）#S001 码头]]\n[[雾港 第01章 雾港 主持CG#S001 码头]]",
        }
    ],
    "edges": [],
}

NPC_PROFILE = """# 雾港 NPC人设

## N001 港务官哈维尔

- 动机：压住走私账本，保住职位。
- 秘密：账本副本藏在钟楼夹层。
- 口头禅：按规矩办。
- 弱点：怕火。
- 外貌：灰白鬓角，左手戴一枚磨旧的铜戒。
- 背景：在雾港做了十九年港务官。

哈维尔在栈桥入口查验来访者。 ^dock-npc

### N001 港务官哈维尔 · 客观外貌 · 人形

> 一个灰白鬓角的男人站在灯下，左手的铜戒磨得发亮。

### N001 港务官哈维尔 · 初见

> 他抬眼看你们，手里的笔没停。

### N001 港务官哈维尔 · 互动

> 他把账本合上，推到一边。

### N001 台词

- 谈判：规矩写在这儿，你自己看。
- 战斗：卫兵！码头有人闹事。
- 欺骗：账本早烧了，你翻也白翻。
- 求助：帮我按住那盏灯。
- 闲聊：雾港的雾，一年比一年重。

**导航**：[[雾港 NPC数据#N001 港务官哈维尔]] · [[雾港 NPC任务与地点#N001 港务官哈维尔]]
"""

NPC_DATA = """# 雾港 NPC数据

## N001 港务官哈维尔

- 护甲等级：13
- 生命值：22

**导航**：[[雾港 NPC人设#N001 港务官哈维尔]] · [[雾港 NPC任务与地点#N001 港务官哈维尔]]
"""

NPC_TASKS = """# 雾港 NPC任务与地点

## N001 港务官哈维尔

- 地点：码头栈桥。
- 任务：请玩家找回失窃的账页。

**导航**：[[雾港 NPC人设#N001 港务官哈维尔]] · [[雾港 NPC数据#N001 港务官哈维尔]]
"""

MONSTER_DATA = """# 雾港 怪物数据

## M001 礁石食尸鬼

- 护甲等级：14
- 生命值：26

**导航**：[[雾港 怪物描述#M001 礁石食尸鬼]]
"""

MONSTER_DESC = """# 雾港 怪物描述

## M001 礁石食尸鬼

海洋生物的尸体在礁石间站起来，动作迟缓。

**导航**：[[雾港 怪物数据#M001 礁石食尸鬼]]
"""


def run(script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "cp936"},
        cwd=str(cwd or ROOT),
    )


def codes(result: subprocess.CompletedProcess) -> list[str]:
    return [item["code"] for item in json.loads(result.stdout)["errors"]]


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / NAME
        self.root.mkdir(parents=True)
        self.source = Path(self.tmp.name) / "雾港原文.md"
        self.source.write_text(SOURCE, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self, legacy_canvas: bool = False) -> None:
        self.assertEqual(run("intake.py", str(self.source), str(self.root)).returncode, 0)
        raw = self.root / "模组" / "原文" / "00-全文.md"
        self.assertTrue(raw.is_file())
        (self.root / "模组" / "正文.md").write_text(raw.read_text(encoding="utf-8"), encoding="utf-8")
        chapter = self.root / "带团文档" / CHAPTER
        chapter.mkdir(parents=True)
        board_navigation = "\n" + BOARD_LINK + "\n" if legacy_canvas else ""
        (chapter / "雾港 第01章 雾港 流程（文字）.md").write_text(TEXT_FLOW + board_navigation, encoding="utf-8")
        (chapter / "雾港 第01章 雾港 主持CG.md").write_text(CG + board_navigation, encoding="utf-8")
        if legacy_canvas:
            (chapter / "雾港 第01章 雾港 流程（白板）.canvas").write_text(
                json.dumps(CANVAS, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        npc = self.root / "npc"
        npc.mkdir()
        (npc / "雾港 NPC人设.md").write_text(NPC_PROFILE, encoding="utf-8")
        (npc / "雾港 NPC数据.md").write_text(NPC_DATA, encoding="utf-8")
        (npc / "雾港 NPC任务与地点.md").write_text(NPC_TASKS, encoding="utf-8")
        monster = self.root / "怪物"
        monster.mkdir()
        (monster / "雾港 怪物数据.md").write_text(MONSTER_DATA, encoding="utf-8")
        (monster / "雾港 怪物描述.md").write_text(MONSTER_DESC, encoding="utf-8")
        (self.root / "资料附件").mkdir()
        (self.root / "模组总索引.md").write_text(INDEX, encoding="utf-8")
        self.assertEqual(run("terms.py", "add", "--root", str(self.root), "--group", "精灵", "--from", "艾尔夫", "--to", "精灵").returncode, 0)
        self.assertEqual(run("terms.py", "apply", str(self.root), "--write").returncode, 0)
        self.assertEqual(run("terms.py", "export", str(self.root), "模组/术语表.md").returncode, 0)

    def test_intake_writes_under_module(self) -> None:
        self.build()
        raw = self.root / "模组" / "原文"
        for name in ("00-全文.md", "01-分段清单.md", "segments.json"):
            self.assertTrue((raw / name).is_file(), name)
        data = json.loads((raw / "segments.json").read_text(encoding="utf-8"))
        self.assertIn("第一章", data["segments"][0]["title"])

    def test_terms_apply_across_products(self) -> None:
        self.build()
        body = (self.root / "模组" / "正文.md").read_text(encoding="utf-8")
        cg = (self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 主持CG.md").read_text(encoding="utf-8")
        self.assertNotIn("艾尔夫", body)
        self.assertNotIn("艾尔夫", cg)
        self.assertIn("精灵", cg)
        ledger = json.loads((self.root / "模组" / "术语台账.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["history"]), 1)
        self.assertEqual(ledger["history"][0]["total"], 2)

    def test_clean_module_passes(self) -> None:
        self.build()
        result = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["counts"]["场景"], 1)

    def test_numeric_loss_detected(self) -> None:
        self.build()
        for path in [
            self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 主持CG.md",
            self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 流程（文字）.md",
        ]:
            path.write_text(path.read_text(encoding="utf-8").replace("1d6+2", "1d6+3"), encoding="utf-8")
        result = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("numeric-loss", codes(result))

    def test_allow_numeric_loss_downgrades(self) -> None:
        self.build()
        for path in [
            self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 主持CG.md",
            self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 流程（文字）.md",
        ]:
            path.write_text(path.read_text(encoding="utf-8").replace("1d6+2", "1d6+3"), encoding="utf-8")
        result = run("check_campaign.py", str(self.root), "--json", "--allow-numeric-loss")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_rule_numbers_ignore_layout_and_keep_types(self) -> None:
        self.build()
        raw = self.root / "模组" / "原文" / "00-全文.md"
        with raw.open("a", encoding="utf-8") as stream:
            stream.write(
                "\n第 246 页\n2014 年\n2026 年 11 月 9 日\n42. 导读\n"
                "\nAC 14；HP 26；力量 12；Speed 30 ft.；150 gp；3 次/天。\n"
            )
        data = self.root / "怪物" / "雾港 怪物数据.md"
        content = MONSTER_DATA + "\n力量：12；速度 30 英尺；150 金币；3 次/日。\n"
        data.write_text(content, encoding="utf-8")
        passed = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(passed.returncode, 0, passed.stdout)
        data.write_text(content.replace("护甲等级：14", "DC 14"), encoding="utf-8")
        result = run("check_campaign.py", str(self.root), "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual([issue["subject"] for issue in payload["errors"] if issue["code"] == "numeric-loss"], ["AC 14"])
        self.assertIn("DC 14", [entry["数值"] for entry in payload["new_numbers"]])

    def test_chapter_checks_only_selected_source_and_linked_entries(self) -> None:
        self.build()
        raw = self.root / "模组" / "原文" / "00-全文.md"
        first = raw.read_text(encoding="utf-8").rstrip()
        source = first + "\n\n## 第二章\nDC 87 的检定。\n\n## 附录\nNPC 的 HP 22。\n"
        raw.write_text(source, encoding="utf-8")
        incomplete = self.root / "带团文档" / "第02章"
        incomplete.mkdir()
        (incomplete / "待整理.md").write_text("DC 87；HP 22。", encoding="utf-8")
        appendix_line = len(source.splitlines())
        args = (
            str(self.root), "--chapter", CHAPTER,
            "--source-lines", f"1-{len(first.splitlines())}",
            "--source-lines", f"{appendix_line}-{appendix_line}", "--json",
        )
        passed = run("check_campaign.py", *args)
        self.assertEqual(passed.returncode, 0, passed.stdout)
        self.assertEqual(json.loads(passed.stdout)["scope"], CHAPTER)

        data = self.root / "npc" / "雾港 NPC数据.md"
        data.write_text(NPC_DATA.replace("生命值：22", "生命值：23") + "\n## N002 其他角色\nHP 22。\n", encoding="utf-8")
        result = run("check_campaign.py", *args)
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual([issue["subject"] for issue in payload["errors"] if issue["code"] == "numeric-loss"], ["HP 22"])
        invalid = run("check_campaign.py", *args, "--source-lines", "9999-10000")
        self.assertEqual(invalid.returncode, 2, invalid.stdout)

    def test_term_leftover_detected(self) -> None:
        self.build()
        path = self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 主持CG.md"
        path.write_text(path.read_text(encoding="utf-8").replace("精灵商队", "艾尔夫商队"), encoding="utf-8")
        result = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("term-leftover", codes(result))

    def test_missing_npc_field_detected(self) -> None:
        self.build()
        path = self.root / "npc" / "雾港 NPC人设.md"
        path.write_text(path.read_text(encoding="utf-8").replace("- 弱点：怕火。\n", ""), encoding="utf-8")
        result = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("npc-field", codes(result))

    def test_canvas_duplicate_id_detected(self) -> None:
        self.build(legacy_canvas=True)
        path = self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 流程（白板）.canvas"
        legacy = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(legacy.returncode, 0, legacy.stdout)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["nodes"].append(dict(data["nodes"][0]))
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("canvas-id", codes(result))

    def test_term_replacement_keeps_link_targets_and_anchors(self) -> None:
        self.build(legacy_canvas=True)
        chapter = self.root / "带团文档" / CHAPTER
        note = self.root / "资料附件" / "艾尔夫(旧).md"
        note.write_text("# 艾尔夫\n\n艾尔夫来过这里。 ^dock-note\n", encoding="utf-8")
        alias_link = "[[艾尔夫(旧)#艾尔夫|艾尔夫]]"
        cg = chapter / "雾港 第01章 雾港 主持CG.md"
        with cg.open("a", encoding="utf-8") as stream:
            stream.write(
                f"\n{alias_link}\n"
                "[[艾尔夫(旧)#艾尔夫]]\n"
                "[[艾尔夫(旧)#艾尔夫\\|艾尔夫]]\n"
                "[艾尔夫](../../资料附件/艾尔夫(旧).md#艾尔夫)\n"
                "![[艾尔夫(旧)#^dock-note]]\n"
                "`艾尔夫`\n"
            )
        board = chapter / "雾港 第01章 雾港 流程（白板）.canvas"
        data = json.loads(board.read_text(encoding="utf-8"))
        data["nodes"][0]["text"] += "\n" + alias_link
        data["nodes"].append({
            "id": "艾尔夫", "type": "file", "file": "资料附件/艾尔夫(旧).md",
            "x": 0, "y": 140, "width": 260, "height": 120,
        })
        board.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = cg.read_bytes()
        preview = run("terms.py", "apply", str(self.root))
        self.assertEqual(preview.returncode, 0, preview.stdout)
        self.assertEqual(cg.read_bytes(), before)
        result = run("terms.py", "apply", str(self.root), "--write")
        self.assertEqual(result.returncode, 0, result.stdout)
        content = cg.read_text(encoding="utf-8")
        for protected in ("[[艾尔夫(旧)#艾尔夫]]", "![[艾尔夫(旧)#^dock-note]]", "`艾尔夫`"):
            self.assertIn(protected, content)
        self.assertIn("[[艾尔夫(旧)#艾尔夫|精灵]]", content)
        self.assertIn("[[艾尔夫(旧)#艾尔夫\\|精灵]]", content)
        self.assertIn("[精灵](../../资料附件/艾尔夫(旧).md#艾尔夫)", content)
        self.assertEqual(note.read_text(encoding="utf-8"), "# 艾尔夫\n\n精灵来过这里。 ^dock-note\n")
        updated = json.loads(board.read_text(encoding="utf-8"))
        self.assertIn("[[艾尔夫(旧)#艾尔夫|精灵]]", updated["nodes"][0]["text"])
        self.assertEqual(updated["nodes"][1], data["nodes"][1])
        checked = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(checked.returncode, 0, checked.stdout)

    def test_undo_restores_products(self) -> None:
        self.build()
        path = self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 主持CG.md"
        before = path.read_text(encoding="utf-8")
        self.assertEqual(run("terms.py", "add", "--root", str(self.root), "--from", "煤气灯", "--to", "风灯").returncode, 0)
        self.assertEqual(run("terms.py", "apply", str(self.root), "--write").returncode, 0)
        self.assertIn("风灯", path.read_text(encoding="utf-8"))
        self.assertEqual(run("terms.py", "undo", str(self.root)).returncode, 0)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_refuses_numeric_term(self) -> None:
        self.build()
        result = run("terms.py", "add", "--root", str(self.root), "--from", "22", "--to", "23")
        self.assertEqual(result.returncode, 1)

    def test_scan_flags_long_sentence(self) -> None:
        self.build()
        cg = self.root / "带团文档" / "第01章 雾港" / "雾港 第01章 雾港 主持CG.md"
        cg.write_text(
            CG.replace(
                "码头上只剩一盏煤气灯，灯下的木箱刻着 DC 15 的暗记。",
                "码头上只剩一盏煤气灯，灯下的木箱刻着 DC 15 的暗记，木箱旁边还堆着三只没有封口的木桶和一捆散开的缆绳。",
            ),
            encoding="utf-8",
        )
        result = run("terms.py", "scan", str(cg), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(json.loads(result.stdout)["summary"]["long_sentences"], 0)

    def test_missing_layout_detected(self) -> None:
        result = run("check_campaign.py", str(self.root), "--json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("root-layout", codes(result))


if __name__ == "__main__":
    unittest.main()
