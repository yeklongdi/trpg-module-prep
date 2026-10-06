#!/usr/bin/env python3
"""只读检查模组结构、标题、内部引用和可检查的条目覆盖。仅依赖标准库。"""

import argparse
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import unquote

FOLDERS = {"带团文档", "怪物", "模组", "资料附件", "npc"}
INDEX_SECTIONS = {"备团入口", "规则与模组概况", "章节及实体索引", "资料读取清单", "待确认事项", "完成情况与修改记录"}
ENTITY = re.compile(r"^([SNM]\d{3,})(?:\s+(.+))?$")
PLACEHOLDERS = {"角色名", "怪物名", "场景名", "实际场景名", "实际姓名或称呼", "实际怪物名", "NPC名称", "形态名"}


def clean(value):
    return value.strip().strip("【】").replace("**", "").replace("`", "").strip()


def visible(text):
    """跳过代码块和文件头YAML，防止把格式示例误认作交付正文。"""
    result, fence, front = [], None, text.startswith("---\n") or text.startswith("---\r\n")
    for i, line in enumerate(text.splitlines()):
        if front:
            if i and line.strip() == "---":
                front = False
            result.append("")
            continue
        m = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            result.append("")
        else:
            result.append("" if fence else line)
    return "\n".join(result)


def headings(text, level=None):
    found = list(re.finditer(r"(?m)^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", visible(text)))
    result = []
    for i, m in enumerate(found):
        depth = len(m.group(1))
        if level is not None and depth != level:
            continue
        stop = next((n.start() for n in found[i + 1:] if len(n.group(1)) <= depth), len(visible(text)))
        result.append((clean(m.group(2)), visible(text)[m.end():stop]))
    return result


def has_quote(text):
    parts = [m.group(1).strip() for m in re.finditer(r"(?m)^\s*>\s?(.*)$", visible(text))]
    parts = [p for p in parts if p and not re.fullmatch(r"\[![^]]+\].*", p)]
    return bool(parts) and any(clean(p) not in {"同上", "变了样", "待补", "可朗读正文", "……", "..."} for p in parts)


def table_cells(line):
    line = re.sub(r"\[\[.*?\]\]", lambda m: m.group().replace("|", "\x1f"), line)
    line = line.replace(r"\|", "\x1f")
    return [clean(c.replace("\x1f", "|")) for c in line.strip().strip("|").split("|")]


def index_rows(text):
    header = None
    for line in visible(text).splitlines():
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = table_cells(line)
        if "编号" in cells and "名称" in cells:
            header = cells
        elif header and len(cells) == len(header) and not all(re.fullmatch(r":?-+:?", c) for c in cells):
            yield dict(zip(header, cells))


class Checker:
    def __init__(self, root):
        self.root = root.resolve()
        self.errors, self.warnings, self.docs, self.boards = [], [], {}, {}
        self.names, self.forms, self.entries = {}, {}, {}
        self.files = [p for p in root.rglob("*") if p.is_file()]

    def issue(self, code, message, path=None, subject=None, warning=False):
        item = {"code": code, "message": message}
        if path is not None:
            try:
                item["path"] = str(path.resolve().relative_to(self.root)).replace("\\", "/")
            except ValueError:
                item["path"] = str(path)
        if subject:
            item["subject"] = subject
        (self.warnings if warning else self.errors).append(item)

    def resolve_link(self, path, target):
        target = unquote(target.strip().strip("<>"))
        name, _, anchor = target.partition("#")
        if re.match(r"^[a-zA-Z][\w+.-]*:", name) or name.startswith("//"):
            return None
        if not name:
            return path.resolve(), anchor
        name = name.replace("\\", "/")
        if name.startswith(self.root.name + "/"):
            name = name[len(self.root.name) + 1:]
        candidates = [self.root / name, path.parent / name]
        if not Path(name).suffix:
            candidates += [p.with_suffix(s) for p in candidates[:] for s in (".md", ".canvas")]
        if "/" not in name:
            candidates += [p for p in self.files if p.name == name or p.stem == name]
        found = {p.resolve() for p in candidates if p.is_file()}
        inside = {p for p in found if p.is_relative_to(self.root)}
        if len(inside) == 1:
            return next(iter(inside)), anchor
        if found and not inside:
            self.issue("external-source", "外部源文件链接需人工核验。", path, target, True)
            return None
        self.issue("link-target", "内部链接目标缺失或名称存在歧义。", path, target)
        return None

    def links(self, path, text):
        content = visible(text)
        raw = [m.group(1).split("|", 1)[0] for m in re.finditer(r"\[\[([^]\n]+)\]\]", content)]
        raw += [m.group(1) for m in re.finditer(r"(?<!!)\[[^]\n]*\]\(([^)\n]+)\)", content)]
        return [value for target in raw if (value := self.resolve_link(path, target)) is not None]

    @staticmethod
    def slug(title):
        return re.sub(r"[^\w\- ]", "", title.casefold()).replace(" ", "-")

    def check_links(self, path, text):
        for target, anchor in self.links(path, text):
            if anchor and target in self.docs:
                titles = {title for title, _ in headings(self.docs[target])}
                if anchor not in titles and anchor not in {self.slug(t) for t in titles}:
                    self.issue("link-anchor", "标题锚点无法定位。", path, anchor)

    def require_navigation(self, path, body, targets, entity_id):
        links = self.links(path, body)
        for target in targets:
            if not any(p == target and (p.suffix == ".canvas" or a.startswith(entity_id)) for p, a in links):
                self.issue("mutual-navigation", "同编号条目缺少对应资料导航。", path, entity_id + " → " + target.name)

    def parse_entities(self, path, text, prefix):
        entries = {}
        for title, body in headings(text, 2):
            if not title.startswith(prefix):
                continue
            match = ENTITY.fullmatch(title)
            if not match or not match.group(2):
                self.issue("entity-title", "H2须写编号加实际名称。", path, title)
                continue
            entity_id, name = match.groups()
            if not entity_id.startswith(prefix):
                continue
            if name in PLACEHOLDERS:
                self.issue("placeholder-title", "实体标题仍是模板占位名。", path, title)
            if entity_id in entries:
                self.issue("duplicate-entity", "同文档实体编号重复。", path, entity_id)
            if entity_id in self.names and self.names[entity_id] != name:
                self.issue("name-mismatch", "名称与总索引不一致。", path, entity_id)
            entries[entity_id] = (name, body)
        self.entries[path] = entries
        return entries

    def bundle(self, folder, labels, prefix):
        files = [p for p in self.files if p.is_relative_to(self.root / folder)]
        selected = []
        for label in labels:
            matches = [p.resolve() for p in files if p.suffix == ".md" and p.stem.endswith(label)]
            if len(matches) != 1:
                self.issue("bundle-file", "须有一份汇总文档：" + label, self.root / folder)
            else:
                selected.append(matches[0])
                if label not in next((title for title, _ in headings(self.docs[matches[0]], 1)), ""):
                    self.issue("document-title", "H1缺少文档类别。", matches[0], label)
        for path in files:
            if path.resolve() not in selected:
                self.issue("extra-bundle-file", "分类中有额外文件，应按约定合并或归资料附件。", path)
        wanted = {key for key in self.names if key.startswith(prefix)}
        for path in selected:
            found = self.parse_entities(path, self.docs[path], prefix)
            if set(found) != wanted:
                self.issue("entity-coverage", "实体条目与总索引不一致。", path, "缺少:" + ",".join(sorted(wanted - set(found))) + "；多出:" + ",".join(sorted(set(found) - wanted)))
            for entity_id, (_, body) in found.items():
                self.require_navigation(path, body, [p for p in selected if p != path], entity_id)
        return selected

    def check_canvas(self, path):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges"), list):
            self.issue("canvas-schema", "Canvas须包含nodes与edges数组。", path)
            return {}
        nodes, edges = data["nodes"], data["edges"]
        if any(not isinstance(n, dict) for n in nodes + edges):
            self.issue("canvas-schema", "Canvas节点与连线须为对象。", path)
            return {}
        ids = [n.get("id") for n in nodes]
        edge_ids = [e.get("id") for e in edges]
        if any(not isinstance(x, str) or not x for x in ids + edge_ids):
            self.issue("canvas-id", "Canvas ID须为非空字符串。", path)
            return {}
        if len(ids) != len(set(ids)) or len(edge_ids) != len(set(edge_ids)):
            self.issue("canvas-id", "Canvas节点或连线ID重复。", path)
        scenes = {}
        for n in nodes:
            if n.get("type") not in {"text", "file", "link", "group"}:
                self.issue("canvas-node", "Canvas节点类型无效。", path, n["id"])
            for key in ("x", "y", "width", "height"):
                v = n.get(key)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (key in {"width", "height"} and v <= 0):
                    self.issue("canvas-layout", "Canvas节点坐标或尺寸无效。", path, n["id"])
            if n.get("type") == "text":
                content = n.get("text", "")
                if not isinstance(content, str):
                    self.issue("canvas-node", "文字节点正文无效。", path, n["id"])
                    continue
                title = clean(content.splitlines()[0].lstrip("# ")) if content.splitlines() else ""
                if title.startswith("S"):
                    match = ENTITY.fullmatch(title)
                    if not match or not match.group(2):
                        self.issue("entity-title", "白板场景标题须写S编号加名称。", path, title)
                    else:
                        key, name = match.groups()
                        if key in scenes:
                            self.issue("duplicate-entity", "白板场景编号重复。", path, key)
                        if self.names.get(key) != name:
                            self.issue("name-mismatch", "白板场景名与总索引不一致。", path, key)
                        scenes[key] = (name, content)
                self.check_links(path, content)
        for edge in edges:
            if edge.get("fromNode") not in ids or edge.get("toNode") not in ids:
                self.issue("canvas-endpoint", "Canvas连线引用了不存在的节点。", path, edge["id"])
        self.boards[path] = scenes
        return scenes

    def run(self):
        actual = {p.name for p in self.root.iterdir()}
        expected = FOLDERS | {"模组总索引.md"}
        for name in sorted(actual ^ expected):
            self.issue("root-layout", "根目录缺少规定项或含额外项。", self.root / name)
        for name in FOLDERS:
            if not (self.root / name).is_dir():
                self.issue("root-layout", "规定分类须为文件夹。", self.root / name)
        for path in self.files:
            if path.suffix == ".md" and (path.parent == self.root or any(path.is_relative_to(self.root / p) for p in ("npc", "怪物", "带团文档"))):
                self.docs[path.resolve()] = path.read_text(encoding="utf-8-sig")
        index = self.root / "模组总索引.md"
        text = self.docs.get(index, "")
        if not text:
            self.issue("index-file", "缺少可读取的模组总索引。", index)
        present = {title for title, _ in headings(text, 2)}
        for title in sorted(INDEX_SECTIONS - present):
            self.issue("index-section", "总索引缺少管理章节。", index, title)
        for row in index_rows(text):
            entity_id, name = row.get("编号", ""), row.get("名称", "")
            if not re.fullmatch(r"[SNM]\d{3,}", entity_id):
                continue
            if entity_id in self.names:
                self.issue("index-duplicate", "索引实体编号重复。", index, entity_id)
            self.names[entity_id] = name
            if not name or name in PLACEHOLDERS:
                self.issue("index-name", "索引名称缺失或未替换。", index, entity_id)
            if entity_id.startswith("N"):
                forms = [clean(x) for x in re.split(r"[；;]", row.get("已确认形态", "")) if clean(x)]
                self.forms[entity_id] = forms
                if not forms or any(x in {"未确认", "未说明", "待补", "形态名"} for x in forms):
                    self.issue("index-forms", "NPC索引须记录实际已确认形态。", index, entity_id)
        for prefix, marker in (("N", "原文无NPC"), ("M", "原文无怪物")):
            if not any(k.startswith(prefix) for k in self.names) and marker not in text:
                self.issue("index-coverage", "索引未记录实体，也未确认原文无对应实体。", index, prefix)
        for path, content in self.docs.items():
            h1 = headings(content, 1)
            if len(h1) != 1:
                self.issue("document-title", "文档须有一个具名H1。", path)
            elif self.root.name not in h1[0][0]:
                self.issue("document-title", "H1须包含模组名。", path)
            self.check_links(path, content)
        npcs = self.bundle("npc", ["NPC人设", "NPC数据", "NPC任务与地点"], "N")
        monsters = self.bundle("怪物", ["怪物数据", "怪物描述"], "M")
        for path in [p for p in npcs if p.stem.endswith("NPC人设")]:
            for entity_id, (name, body) in self.entries[path].items():
                forms = {}
                for title, paragraph in headings(body, 3):
                    start = f"{entity_id} {name} · 客观外貌 · "
                    if title.startswith(start):
                        form = title[len(start):].strip()
                        if form in forms:
                            self.issue("npc-form-duplicate", "同一形态外貌小节重复。", path, entity_id + " " + form)
                        forms[form] = paragraph
                        if not has_quote(paragraph):
                            self.issue("npc-appearance-text", "形态外貌缺少独立可朗读正文。", path, entity_id + " " + form)
                if set(forms) != set(self.forms.get(entity_id, [])):
                    self.issue("npc-form-coverage", "外貌形态与索引不一致。", path, entity_id)
                for purpose in ("初见", "互动"):
                    if not any(purpose in title and has_quote(paragraph) for title, paragraph in headings(body, 3)):
                        self.issue("npc-introduction", "缺少可朗读的" + purpose + "描写。", path, entity_id)
        chapter_files = [p.resolve() for p in self.files if p.is_relative_to(self.root / "带团文档")]
        chapter_sets = {}
        for path in chapter_files:
            role = "text" if path.name.endswith("流程（文字）.md") else "cg" if path.name.endswith("主持CG.md") else "canvas" if path.name.endswith("流程（白板）.canvas") else None
            if role is None:
                self.issue("chapter-file", "章节含额外文件或文件类别无法辨认。", path)
                continue
            chapter_sets.setdefault(path.parent, {}).setdefault(role, []).append(path)
        all_scenes = set()
        for directory, roles in chapter_sets.items():
            if any(len(roles.get(role, [])) != 1 for role in ("text", "cg", "canvas")):
                self.issue("chapter-bundle", "每章须各有一份文字流程、白板和主持CG。", directory)
                continue
            paths = {role: values[0] for role, values in roles.items()}
            scenes = {role: self.check_canvas(path) if role == "canvas" else self.parse_entities(path, self.docs[path], "S") for role, path in paths.items()}
            if not scenes["text"]:
                self.issue("scene-coverage", "章节缺少场景条目。", paths["text"])
            if not (set(scenes["text"]) == set(scenes["cg"]) == set(scenes["canvas"])):
                self.issue("scene-coverage", "文字、白板与CG的场景编号覆盖不一致。", directory)
            all_scenes.update(scenes["text"])
            for role, entries in scenes.items():
                for entity_id, (_, body) in entries.items():
                    self.require_navigation(paths[role], body, [p for r, p in paths.items() if r != role], entity_id)
                    if role == "cg" and ("触发" not in body or "主持人备注" not in body or not has_quote(body)):
                        self.issue("cg-content", "场景CG缺少触发、朗读正文或主持人备注。", paths[role], entity_id)
            for entity_id, (_, body) in scenes["text"].items():
                referenced_monsters = {anchor.split(" ", 1)[0] for target, anchor in self.links(paths["text"], body) if target in monsters}
                for monster_id in referenced_monsters:
                    self.require_navigation(paths["text"], body, monsters, monster_id)
                    for path in monsters:
                        if monster_id in self.entries[path]:
                            self.require_navigation(path, self.entries[path][monster_id][1], [paths["text"]], entity_id)
        if all_scenes != {k for k in self.names if k.startswith("S")}:
            self.issue("scene-index-coverage", "章节场景集合与总索引不一致。", index)
        if not chapter_sets:
            self.issue("chapter-bundle", "未找到带团章节三份资料。", self.root / "带团文档")
        return {"ok": not self.errors, "counts": {"NPC": sum(k.startswith("N") for k in self.names), "怪物": sum(k.startswith("M") for k in self.names), "场景": len(all_scenes), "错误": len(self.errors), "提示": len(self.warnings)}, "errors": self.errors, "warnings": self.warnings, "manual_checks": ["原文事实与来源、实际读取覆盖、规则版本及必用数值", "性格依据、纯外貌边界、秘密和形态公开条件", "场景描写充分、条件口播及玩家分支", "白板布局可读、玩家推进与资料关系区分"]}


def main():
    parser = argparse.ArgumentParser(description="只读检查五目录模组资料，不生成或修改任何文件。")
    parser.add_argument("root", type=Path, help="模组根目录")
    parser.add_argument("--json", action="store_true", help="输出JSON到终端")
    args = parser.parse_args()
    if not args.root.is_dir():
        result, code = {"ok": False, "errors": [{"code": "input", "message": "模组根目录不存在或不是文件夹。"}]}, 2
    else:
        try:
            result = Checker(args.root.resolve()).run()
            code = 0 if result["ok"] else 1
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            result, code = {"ok": False, "errors": [{"code": "read", "message": "读取或解析失败：" + str(exc)}]}, 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("结构检查通过；仍需完成原文与内容核对。" if result["ok"] else "结构检查未通过。")
        for item in result.get("errors", []) + result.get("warnings", []):
            print(f"[{item['code']}] {item.get('path', '')} {item.get('subject', '')} {item['message']}")
        if result.get("counts"):
            print(json.dumps(result["counts"], ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
