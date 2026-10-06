"""通过命令行验证检查器；所有模组材料均为虚构，临时样本自动清理。"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills/trpg-module-prep/scripts/check_module.py"


class ModuleCheckTests(unittest.TestCase):
    def setUp(self):
        self.test_directory = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(prefix=".trpg-skill-test-", dir=self.test_directory)
        self.addCleanup(self.cleanup_sample)
        self.root = Path(self.temp.name) / "虚构验证模组"
        self.root.mkdir()
        self.paths = {
            "npc-character": "npc/虚构验证模组 NPC人设.md",
            "npc-data": "npc/虚构验证模组 NPC数据.md",
            "npc-state": "npc/虚构验证模组 NPC任务与地点.md",
            "monster-data": "怪物/虚构验证模组 怪物数据.md",
            "monster-description": "怪物/虚构验证模组 怪物描述.md",
            "text": "带团文档/第01章 码头/虚构验证模组 第01章 码头 流程（文字）.md",
            "cg": "带团文档/第01章 码头/虚构验证模组 第01章 码头 主持CG.md",
            "canvas": "带团文档/第01章 码头/虚构验证模组 第01章 码头 流程（白板）.canvas",
        }
        self.make_fixture()

    def cleanup_sample(self):
        # 仅清理本测试在tests内创建的临时目录，先确认绝对路径边界。
        sample_directory = Path(self.temp.name).resolve()
        self.assertTrue(sample_directory.is_relative_to(self.test_directory))
        self.assertTrue(sample_directory.name.startswith(".trpg-skill-test-"))
        self.temp.cleanup()

    def write(self, key, text):
        path = self.root / self.paths.get(key, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read(self, key):
        return (self.root / self.paths.get(key, key)).read_text(encoding="utf-8")

    def link(self, key, entity):
        return f"[[{self.paths[key]}#{entity}|{entity} · {key}]]" if key != "canvas" else f"[[{self.paths[key]}|{entity} · 白板]]"

    def make_fixture(self, entities=True):
        for name in ["带团文档", "怪物", "模组", "资料附件", "npc"]:
            (self.root / name).mkdir(exist_ok=True)
        self.write("模组/虚构原文.md", "# 虚构验证原文\n米洛有常态与石像形态；码头可通往仓库，仓库有铁甲蟹。所有测试数值和描述均为虚构。\n")
        index = "# 虚构验证模组 · 模组总索引\n\n"
        for title in ["备团入口", "规则与模组概况", "章节及实体索引", "资料读取清单", "待确认事项", "完成情况与修改记录"]:
            index += f"## {title}\n\n"
            if title == "章节及实体索引":
                index += "### 场景索引\n\n| 编号 | 名称 | 主条目 | 来源 |\n| --- | --- | --- | --- |\n"
                for code, name in [("S001", "码头"), ("S002", "仓库")]:
                    index += f"| {code} | {name} | {self.link('text',code+' '+name)} | 虚构原文第1段 |\n"
                if entities:
                    index += f"\n### NPC索引\n\n| 编号 | 名称 | 已确认形态 | 主条目 | 来源 |\n| --- | --- | --- | --- | --- |\n| N001 | 米洛 | 常态；石像 | {self.link('npc-character','N001 米洛')} | 虚构原文第1段 |\n"
                    index += f"\n### 怪物索引\n\n| 编号 | 名称 | 主条目 | 来源 |\n| --- | --- | --- | --- |\n| M001 | 铁甲蟹 | {self.link('monster-data','M001 铁甲蟹')} | 虚构原文第1段 |\n"
                else:
                    index += "\n原文无NPC。原文无怪物。\n"
            else:
                index += "虚构测试内容，待确认事项暂无。\n\n"
        self.write("模组总索引.md", index)
        for key, label in [("npc-character", "NPC人设"), ("npc-data", "NPC数据"), ("npc-state", "NPC任务与地点")]:
            content = f"# 虚构验证模组 {label}\n\n"
            if entities:
                content += "## N001 米洛\n\n" + "\n".join(self.link(k, "N001 米洛") for k in ["npc-character", "npc-data", "npc-state"] if k != key) + "\n\n"
                if key == "npc-character":
                    for form, appearance in [("常态", "他的黑发垂到耳侧，穿着带白色袖口的灰色外套。"), ("石像", "石像与常态等高，表面呈灰白色，衣褶和发丝都以浅刻线条表现。")]:
                        content += f"### N001 米洛 · 客观外貌 · {form}\n\n> {appearance}\n\n"
                    content += "### N001 米洛 · 初见描写\n\n> 米洛把油灯放在桌角，向门口抬起右手。\n\n### N001 米洛 · 互动描写\n\n> 米洛指着地图上的码头，询问你们打算从哪边进入。\n\n"
                else:
                    content += "虚构测试条目，实际模组任务须核对规则与来源。\n"
            else:
                content += "原文无NPC。\n"
            self.write(key, content)
        for key, label in [("monster-data", "怪物数据"), ("monster-description", "怪物描述")]:
            content = f"# 虚构验证模组 {label}\n\n"
            if entities:
                other = "monster-description" if key == "monster-data" else "monster-data"
                content += "## M001 铁甲蟹\n\n" + self.link(other, "M001 铁甲蟹") + "\n" + self.link("text", "S002 仓库") + "\n\n虚构测试怪物条目。\n"
            else:
                content += "原文无怪物。\n"
            self.write(key, content)
        for key, label in [("text", "流程（文字）"), ("cg", "主持CG")]:
            content = f"# 虚构验证模组 第01章 码头 {label}\n\n"
            for code, name in [("S001", "码头"), ("S002", "仓库")]:
                entry = code + " " + name
                content += f"## {entry}\n\n" + self.link("canvas", entry) + "\n" + self.link("cg" if key == "text" else "text", entry) + "\n\n"
                if key == "cg":
                    content += f"### {entry} · 触发条件\n\n玩家到达入口时。\n\n### {entry} · 初入朗读\n\n> 潮水拍打石阶，门边的油灯照着一片湿润的地面。木箱沿墙叠放，远处传来绳索摩擦的声音。\n\n### {entry} · 主持人备注\n\n按虚构测试原文选择条件。\n\n"
                elif code == "S002" and entities:
                    content += self.link("monster-data", "M001 铁甲蟹") + "\n" + self.link("monster-description", "M001 铁甲蟹") + "\n\n"
            self.write(key, content)
        nodes = []
        for i, (code, name) in enumerate([("S001", "码头"), ("S002", "仓库")]):
            title = code + " " + name
            nodes.append({"id": code, "type": "text", "text": "## " + title + "\n\n" + self.link("text", title) + "\n" + self.link("cg", title), "x": i * 500, "y": 0, "width": 440, "height": 300})
        self.write("canvas", json.dumps({"nodes": nodes, "edges": [{"id": "route", "fromNode": "S001", "toNode": "S002", "label": "玩家选择前往仓库"}]}, ensure_ascii=False))

    def run_check(self, root=None):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), str(root or self.root), "--json"], capture_output=True, text=True, encoding="utf-8")
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "检查器修改了被检查资料")
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def assert_error(self, code, exit_code=1):
        actual_exit, data = self.run_check()
        self.assertEqual(actual_exit, exit_code, data)
        self.assertIn(code, {e["code"] for e in data["errors"]}, data)

    def test_complete_module_and_read_only(self):
        code, data = self.run_check()
        self.assertEqual(code, 0, data)
        self.assertEqual(data["counts"]["场景"], 2)
        self.assertEqual(data["counts"]["NPC"], 1)

    def test_no_npc_or_monster_is_valid_when_confirmed(self):
        self.make_fixture(entities=False)
        code, data = self.run_check()
        self.assertEqual(code, 0, data)

    def test_extra_root_file(self):
        self.write("备团入口.md", "重复管理文件")
        self.assert_error("root-layout")

    def test_only_number_title(self):
        self.write("npc-data", self.read("npc-data").replace("## N001 米洛", "## N001"))
        self.assert_error("entity-title")

    def test_missing_form(self):
        content = self.read("npc-character")
        start = content.index("### N001 米洛 · 客观外貌 · 石像")
        end = content.index("### N001 米洛 · 初见描写")
        self.write("npc-character", content[:start] + content[end:])
        self.assert_error("npc-form-coverage")

    def test_form_requires_actual_text(self):
        self.write("npc-character", self.read("npc-character").replace("他的黑发垂到耳侧，穿着带白色袖口的灰色外套。", "同上"))
        self.assert_error("npc-appearance-text")

    def test_missing_same_entity_navigation(self):
        self.write("npc-data", self.read("npc-data").replace(self.link("npc-state", "N001 米洛"), ""))
        self.assert_error("mutual-navigation")

    def test_missing_scene_cg(self):
        self.write("cg", self.read("cg").split("## S002 仓库")[0])
        self.assert_error("scene-coverage")

    def test_broken_heading_anchor(self):
        self.write("npc-data", self.read("npc-data").replace("#N001 米洛", "#N099 不存在"))
        self.assert_error("link-anchor")

    def test_unknown_canvas_endpoint(self):
        board = json.loads(self.read("canvas"))
        board["edges"][0]["toNode"] = "不存在的节点"
        self.write("canvas", json.dumps(board, ensure_ascii=False))
        self.assert_error("canvas-endpoint")

    def test_invalid_canvas_json(self):
        self.write("canvas", "{无法解析")
        self.assert_error("read", 2)

    def test_missing_input_directory(self):
        code, data = self.run_check(self.root / "不存在")
        self.assertEqual(code, 2)
        self.assertEqual(data["errors"][0]["code"], "input")


if __name__ == "__main__":
    unittest.main()
