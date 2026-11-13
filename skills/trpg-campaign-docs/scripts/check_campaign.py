#!/usr/bin/env python3
"""只读检查带团文档的结构、数值保真、术语一致、字段完整与引用闭环。仅依赖标准库。"""

import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from terms import MARKDOWN_LINK, WIKI_LINK, prose_spans

FOLDERS = {"带团文档", "怪物", "模组", "资料附件", "npc"}
INDEX_SECTIONS = {"备团入口", "规则与模组概况", "章节及实体索引", "资料读取清单", "待确认事项", "完成情况与修改记录"}
ENTITY = re.compile(r"^([SNM]\d{3,})(?:\s+(.+))?$")
RULE_FIELDS = {
    "DC": ("dc", "difficulty class", "检定难度", "难度等级"),
    "AC": ("ac", "armor class", "护甲等级"),
    "HP": ("hp", "hit points", "生命值", "耐久值"),
    "CR": ("cr", "challenge rating", "挑战等级"),
    "SAN": ("san", "sanity", "理智值"),
    "STR": ("str", "strength", "力量"),
    "DEX": ("dex", "dexterity", "敏捷"),
    "CON": ("con", "constitution", "体质"),
    "INT": ("int", "intelligence", "智力"),
    "WIS": ("wis", "wisdom", "感知"),
    "CHA": ("cha", "charisma", "魅力"),
    "POW": ("pow", "power", "意志"),
    "SIZ": ("siz", "size", "体型"),
    "APP": ("app", "appearance", "外貌"),
    "EDU": ("edu", "education", "教育"),
    "速度": ("speed", "mov", "移动速度", "移动力", "速度"),
}
RULE_UNITS = {
    "尺": ("英尺", "尺", "feet", "ft"),
    "米": ("米", "meters"),
    "磅": ("磅", "pounds", "lbs", "lb"),
    "金币": ("金币", "gp"),
    "银币": ("银币", "sp"),
    "铜币": ("铜币", "cp"),
    "次": ("次",),
    "回合": ("回合",),
    "轮": ("轮",),
    "分钟": ("分钟",),
    "小时": ("小时",),
    "天": ("天",),
    "级": ("级",),
    "环": ("环",),
    "点伤害": ("点伤害",),
}
FIELD_NAMES = {alias: name for name, aliases in RULE_FIELDS.items() for alias in aliases}
UNIT_NAMES = {alias: name for name, aliases in RULE_UNITS.items() for alias in aliases}
VALUE = r"[+-]?\d+(?:[./]\d+)?"
UNIT = "|".join(re.escape(unit) for unit in sorted(UNIT_NAMES, key=len, reverse=True))
GAME_NUMBER = re.compile(
    r"(?<![a-z0-9_])(?:"
    r"(?P<dice>(?:\d+[ \t]*)?d[ \t]*\d+(?:[ \t]*[+-][ \t]*\d+)?)"
    rf"|(?P<field>{'|'.join(FIELD_NAMES)})[ \t]*[:=]?[ \t]*(?P<value>{VALUE})(?:[ \t]*(?P<field_unit>{UNIT}|%))?"
    rf"|(?P<quantity>{VALUE})[ \t]*(?P<unit>{UNIT})(?:[ \t]*/[ \t]*(?P<period>天|日|回合|轮))?"
    r")(?![a-z0-9_]|[./]\d)"
)
NPC_FIELDS = ("动机", "秘密", "口头禅", "弱点", "外貌", "背景")
NPC_SCENES = ("谈判", "战斗", "欺骗", "求助", "闲聊")
SCENE_SKIP = ("跳过", "不适用", "无此场景", "无战斗", "平民", "不生成")
TBD = ("待补", "待确认")

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
        self.terms, self.new_numbers = [], []
        self.allow_loss = False
        self.files = [p for p in root.rglob("*") if p.is_file() and ".backup" not in p.parts]

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
        raw = [m["target"] for m in WIKI_LINK.finditer(content)]
        raw += [
            re.sub(r"\\([()])", r"\1", m["target"])
            for m in MARKDOWN_LINK.finditer(content)
            if not (m.start() and content[m.start() - 1] == "!")
        ]
        return [value for target in raw if (value := self.resolve_link(path, target)) is not None]

    @staticmethod
    def slug(title):
        return re.sub(r"[^\w\- ]", "", title.casefold()).replace(" ", "-")

    def check_links(self, path, text):
        links = self.links(path, text)
        for target, anchor in links:
            if anchor and target.suffix == ".md":
                content = self.docs[target] if target in self.docs else target.read_text(encoding="utf-8-sig")
                titles = {title for title, _ in headings(content)}
                block_ids = set(re.findall(r"(?m)(?<!\S)(\^[A-Za-z0-9-]+)[ \t]*$", visible(content)))
                if anchor not in block_ids and anchor not in titles and anchor not in {self.slug(t) for t in titles}:
                    self.issue("link-anchor", "标题或块锚点无法定位。", path, anchor)
        return links

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

    def produced(self):
        """逐份返回加工产物的文字，排除原文底稿、台账与备份。

        台账与术语表本身就写着旧写法，不能算残留。
        """
        raw = (self.root / "模组" / "原文").resolve()
        skip = {self.root / "模组" / "术语台账.json", self.root / "模组" / "术语表.md"}
        for path in self.files:
            if path.suffix not in {".md", ".canvas"}:
                continue
            if path.resolve().is_relative_to(raw) or path.resolve() in skip or path.is_relative_to(self.root / "资料附件" / "备份"):
                continue
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                continue
            if path.suffix == ".canvas":
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                nodes = data.get("nodes") if isinstance(data, dict) else None
                if isinstance(nodes, list):
                    for node in nodes:
                        if isinstance(node, dict) and node.get("type") == "text" and isinstance(node.get("text"), str):
                            yield node["text"]
            else:
                yield text

    def check_assets(self):
        raw_dir = self.root / "模组" / "原文"
        for name in ("00-全文.md", "segments.json"):
            if not (raw_dir / name).is_file():
                self.issue("raw-missing", "缺少原文底稿，先运行 intake.py。", raw_dir / name)
        if not (raw_dir / "01-分段清单.md").is_file():
            self.issue("raw-missing", "缺少分段清单；模型直读时须按规则补齐。", raw_dir / "01-分段清单.md", warning=True)
        full = raw_dir / "00-全文.md"
        if full.is_file():
            text = full.read_text(encoding="utf-8-sig")
            heads = [line.strip() for line in re.findall(r"(?m)^#{1,6} .+$", text)]
            broken = [
                head
                for head in heads
                if re.search(r"[\u0080-\u00ff\ufffd]", head) or re.search(r"[：:]\s*$", head)
            ]
            if broken:
                self.issue(
                    "raw-heading",
                    "原文标题疑似被截断或含乱码，分段可能不可靠；核对后手工补全标题。",
                    full,
                    broken[0][:40],
                    warning=True,
                )
        body = self.root / "模组" / "正文.md"
        if not body.is_file():
            self.issue("body-missing", "未生成 模组/正文.md，术语统一缺少工作底稿。", body, warning=True)
        ledger = self.root / "模组" / "术语台账.json"
        if ledger.is_file():
            try:
                self.terms = json.loads(ledger.read_text(encoding="utf-8-sig")).get("terms", [])
            except (OSError, json.JSONDecodeError):
                self.issue("ledger-broken", "术语台账无法解析。", ledger)
        else:
            self.issue("ledger-missing", "缺少术语台账；即使没有术语争议也建议建空台账。", ledger, warning=True)
        if self.terms and not (self.root / "模组" / "术语表.md").is_file():
            self.issue("ledger-export", "有术语登记但未导出术语表，运行 terms.py export。", self.root / "模组" / "术语表.md", warning=True)

    def check_numbers(self, raw, source_text, products):
        base = Counter(self.game_numbers(source_text))
        made = Counter(token for text in products for token in self.game_numbers(text))
        lost = [(k, v, made.get(k, 0)) for k, v in base.items() if made.get(k, 0) < v]
        for token, want, got in sorted(lost, key=lambda item: (-item[1], item[0]))[:20]:
            self.issue(
                "numeric-loss",
                f"核对范围内的规则数值未完整保留（原文 {want} 次，产物 {got} 次）。",
                raw,
                token,
                warning=self.allow_loss,
            )
        self.new_numbers = sorted(
            [(k, count) for k, count in made.items() if k not in base],
            key=lambda item: (-item[1], item[0]),
        )

    @staticmethod
    def game_numbers(text):
        """按规则含义归一化骰式、属性和带单位的数量。"""
        text = re.sub(r"<!--.*?-->", "", visible(text), flags=re.S)
        text = WIKI_LINK.sub(lambda m: m["alias"] or "", text)
        text = MARKDOWN_LINK.sub(lambda m: m["label"], text)
        text = unicodedata.normalize("NFKC", text).casefold().translate(str.maketrans("", "", "*\x60"))
        text = text.replace("−", "-")
        text = re.sub(r"https?://[^\s<>]+", "", text)
        text = re.sub(r"\d{1,4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?", "", text)
        for match in GAME_NUMBER.finditer(text):
            if dice := match["dice"]:
                dice = re.sub(r"\s+", "", dice)
                yield "1" + dice if dice.startswith("d") else dice
            elif field := match["field"]:
                unit = match["field_unit"] or ""
                yield FIELD_NAMES[field] + " " + match["value"] + UNIT_NAMES.get(unit, unit)
            else:
                period = match["period"]
                quantity = match["quantity"] + UNIT_NAMES[match["unit"]]
                yield f"{quantity}/{period.replace('日', '天')}" if period else quantity

    def check_volume(self):
        """正文与索引的体积守卫：正文漏抄、索引膨胀，都是可机械发现的。"""
        raw = self.root / "模组" / "原文" / "00-全文.md"
        body = self.root / "模组" / "正文.md"
        if raw.is_file() and body.is_file():
            raw_chars = len(clean(raw.read_text(encoding="utf-8-sig")))
            body_chars = len(clean(body.read_text(encoding="utf-8-sig")))
            if raw_chars > 2000 and body_chars < raw_chars * 0.8:
                self.issue(
                    "body-short",
                    f"模组/正文.md 只有原文的 {body_chars * 100 // raw_chars}%，可能漏抄或漏抽。",
                    body,
                    warning=True,
                )
        index = self.root / "模组总索引.md"
        if index.is_file():
            size = index.stat().st_size
            if size > 80000:
                self.issue(
                    "index-bloat",
                    f"总索引 {size // 1024}KB，导航文件过大；逐文件明细应放 模组/加工进度.json。",
                    index,
                    warning=True,
                )
        if self.new_numbers:
            text = index.read_text(encoding="utf-8-sig") if index.is_file() else ""
            if "新增数值" not in text:
                self.issue("new-number-log", "产物含新增游戏数值，但总索引没有【新增数值】章节。", index, warning=True)

    def check_terms(self):
        texts = [text[start:end] for text in self.produced() for start, end in prose_spans(text)]
        ledger = self.root / "模组" / "术语台账.json"
        for term in self.terms:
            if term.get("from") and any(term["from"] in text for text in texts):
                self.issue("term-leftover", "台账登记的旧写法仍残留在产物中。", ledger, term["from"])
            if term.get("to") and not any(term["to"] in text for text in texts):
                self.issue("term-unused", "统一写法未出现，确认是否漏替换或登错。", ledger, term["to"], warning=True)

    def check_npc_fields(self, path, entries):
        for entity_id, (name, body) in entries.items():
            for field in NPC_FIELDS:
                if not re.search(r"(?m)^\s*[-*]?\s*\**\s*" + field + r"\s*\**\s*[:：]", body):
                    self.issue("npc-field", "NPC 人设缺少字段：" + field, path, entity_id + " " + name)
            scenes = [s for s in NPC_SCENES if re.search(r"(?m)^\s*[-*]\s*\**\s*" + s, body)]
            if len(scenes) < 3 and not any(mark in body for mark in SCENE_SKIP):
                self.issue("npc-dialogue", f"台词场景只有 {len(scenes)} 种，且未说明跳过原因。", path, entity_id + " " + name)

    def check_readability(self):
        for path in self.files:
            if path.suffix != ".md" or not path.name.endswith("主持CG.md"):
                continue
            text = self.docs.get(path.resolve()) or path.read_text(encoding="utf-8-sig")
            quoted = "\n".join(re.findall(r"(?m)^\s*>\s?(.*)$", text))
            quoted = re.sub(r"\[![^\]]+\][^\n]*", "", quoted)
            for sentence in re.findall(r"[^。！？!?\n]+[。！？!?]?", quoted):
                clean = re.sub(r"[*_`#>]", "", sentence).strip()
                if len(clean) > 40:
                    self.issue("cg-long-sentence", "朗读句超过 40 字，念下来会赶。", path, clean[:30], warning=True)
                    break

    def check_dialogue_fill(self):
        """台词全填【待补】时提醒：这个功能等于没做。"""
        path = next((p for p in self.files if p.suffix == ".md" and p.stem.endswith("NPC人设")), None)
        if path is None:
            return
        text = path.read_text(encoding="utf-8-sig")
        entries = re.findall(r"(?m)^\s*[-*]\s*\**\s*(?:谈判|战斗|欺骗|求助|闲聊)\s*\**\s*[:：]\s*(.+)$", text)
        if len(entries) < 3:
            return
        empty = [line for line in entries if any(mark in line for mark in TBD)]
        if len(empty) / len(entries) > 0.8:
            self.issue(
                "dialogue-empty",
                f"{len(empty)}/{len(entries)} 条台词是【待补】；有动机与身份依据时应写一句并标【演绎】。",
                path,
                warning=True,
            )


    def run_chapter(self, chapter, source_ranges):
        """只校验指定章节及它引用的数据条目。行号以原文底稿为准。"""
        chapters = self.root / "带团文档"
        directory = (chapters / chapter).resolve()
        if Path(chapter).name != chapter or directory.parent != chapters or not directory.is_dir():
            raise ValueError("--chapter 须填写带团文档下已有的章节文件夹名。")
        raw = self.root / "模组" / "原文" / "00-全文.md"
        lines = raw.read_text(encoding="utf-8-sig").splitlines()
        ranges = []
        for value in source_ranges:
            match = re.fullmatch(r"(\d+)-(\d+)", value)
            if match is None:
                raise ValueError("--source-lines 须使用起-止的格式，例如 12-30。")
            start, end = map(int, match.groups())
            if not 1 <= start <= end <= len(lines):
                raise ValueError(f"原文行范围 {value} 越界，底稿共 {len(lines)} 行。")
            ranges.append((start, end))
        parts = []
        last = 0
        for start, end in sorted(ranges):
            if start <= last:
                raise ValueError("--source-lines 的各段行范围须互不重叠。")
            parts.append("\n".join(lines[start - 1:end]))
            last = end

        index = self.root / "模组总索引.md"
        self.names = {row["编号"]: row["名称"] for row in index_rows(index.read_text(encoding="utf-8-sig"))}
        files = [p.resolve() for p in self.files if p.parent == directory]
        self.docs = {p: p.read_text(encoding="utf-8-sig") for p in files if p.suffix == ".md"}
        linked = set()
        for path, content in self.docs.items():
            for target, anchor in self.check_links(path, content):
                if anchor and (
                    target.parent == self.root / "npc" and target.name.endswith("NPC数据.md")
                    or target.parent == self.root / "怪物" and target.name.endswith("怪物数据.md")
                ):
                    linked.add((target, anchor))
        entries = {}
        for target, anchor in sorted(linked):
            for title, text in headings(target.read_text(encoding="utf-8-sig"), 2):
                if anchor in {title, self.slug(title)} and ENTITY.fullmatch(title):
                    entries[target, title] = text
        self.check_numbers(raw, "\n\n".join(parts), [*self.docs.values(), *entries.values()])
        scenes = self.check_chapters(files)
        for scene in sorted(scenes):
            if scene not in self.names:
                self.issue("scene-index-coverage", "本章场景未登记在总索引。", index, scene)
        return self.report(scenes, chapter=chapter)

    def run(self):
        actual = {p.name for p in self.root.iterdir()}
        expected = FOLDERS | {"模组总索引.md"}
        for name in sorted(actual ^ expected):
            self.issue("root-layout", "根目录缺少规定项或含额外项。", self.root / name)
        for name in FOLDERS:
            if not (self.root / name).is_dir():
                self.issue("root-layout", "规定分类须为文件夹。", self.root / name)
        self.check_assets()
        for path in self.files:
            if path.suffix == ".md" and (path.parent == self.root or any(path.is_relative_to(self.root / p) for p in ("npc", "怪物", "带团文档"))):
                self.docs[path.resolve()] = path.read_text(encoding="utf-8-sig")
        index = self.root / "模组总索引.md"
        raw = self.root / "模组" / "原文" / "00-全文.md"
        if raw.is_file():
            self.check_numbers(raw, raw.read_text(encoding="utf-8-sig"), (text for path, text in self.docs.items() if path != index))
        self.check_terms()
        self.check_volume()
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
            self.check_npc_fields(path, self.entries.get(path, {}))
        self.check_readability()
        self.check_dialogue_fill()
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
        all_scenes = self.check_chapters(chapter_files, monsters)
        if all_scenes != {k for k in self.names if k.startswith("S")}:
            self.issue("scene-index-coverage", "章节场景集合与总索引不一致。", index)
        return self.report(all_scenes)

    def check_chapters(self, chapter_files, monsters=()):
        chapter_sets = {}
        for path in chapter_files:
            role = "text" if path.name.endswith("流程（文字）.md") else "cg" if path.name.endswith("主持CG.md") else "canvas" if path.name.endswith("流程（白板）.canvas") else None
            if role is None:
                self.issue("chapter-file", "章节含额外文件或文件类别无法辨认。", path)
                continue
            chapter_sets.setdefault(path.parent, {}).setdefault(role, []).append(path)
        all_scenes = set()
        for directory, roles in chapter_sets.items():
            if any(len(roles.get(role, [])) != 1 for role in ("text", "cg")) or len(roles.get("canvas", [])) > 1:
                self.issue("chapter-bundle", "每章须各有一份文字流程和主持CG，可另附一份白板。", directory)
                continue
            paths = {role: values[0] for role, values in roles.items()}
            scenes = {role: self.check_canvas(path) if role == "canvas" else self.parse_entities(path, self.docs[path], "S") for role, path in paths.items()}
            if not scenes["text"]:
                self.issue("scene-coverage", "章节缺少场景条目。", paths["text"])
            if any(set(entries) != set(scenes["text"]) for entries in scenes.values()):
                self.issue("scene-coverage", "文字流程、主持CG与现有白板的场景编号覆盖不一致。", directory)
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
        if not chapter_sets:
            self.issue("chapter-bundle", "未找到章节文字流程与主持CG。", self.root / "带团文档")
        return all_scenes

    def report(self, scenes, chapter=None):
        counts = {"场景": len(scenes), "错误": len(self.errors), "提示": len(self.warnings)}
        if chapter is None:
            counts.update({
                "NPC": sum(k.startswith("N") for k in self.names),
                "怪物": sum(k.startswith("M") for k in self.names),
                "术语": len(self.terms),
            })
        return {
            "ok": not self.errors,
            "scope": chapter or "全模组",
            "counts": counts,
            "new_numbers": [{"数值": k, "新增次数": v} for k, v in self.new_numbers[:30]],
            "errors": self.errors,
            "warnings": self.warnings,
            "manual_checks": [
                "原文事实与来源、实际读取覆盖、规则版本及必用数值",
                "补写是否停在可观察的感官层，台词是否泄露未公开信息",
                "场景描写是否够念、条件口播是否覆盖玩家可能的动作",
                "场景路线的条件与分支可读，使用白板时核对布局",
                "新增数值是否每条都能在原文或已核验外部资料找到依据",
            ],
        }


def main():
    parser = argparse.ArgumentParser(description="只读检查五目录模组资料，不生成或修改任何文件。")
    parser.add_argument("root", type=Path, help="模组根目录")
    parser.add_argument("--json", action="store_true", help="输出JSON到终端")
    parser.add_argument("--allow-numeric-loss", action="store_true", help="把数值缺失降级为提示（须在总索引写明原因）")
    parser.add_argument("--chapter", help="只检查带团文档下指定的章节文件夹")
    parser.add_argument("--source-lines", action="append", default=[], metavar="起-止", help="本章对应的 00-全文.md 行号范围，可重复")
    args = parser.parse_args()
    if not args.root.is_dir() or bool(args.chapter) != bool(args.source_lines):
        message = "模组根目录不存在或不是文件夹。" if not args.root.is_dir() else "--chapter 与 --source-lines 须配套使用。"
        result, code = {"ok": False, "errors": [{"code": "input", "message": message}]}, 2
    else:
        try:
            checker = Checker(args.root.resolve())
            checker.allow_loss = args.allow_numeric_loss
            result = checker.run_chapter(args.chapter, args.source_lines) if args.chapter else checker.run()
            code = 0 if result["ok"] else 1
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            result, code = {"ok": False, "errors": [{"code": "read", "message": "读取或解析失败：" + str(exc)}]}, 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        scope = result.get("scope", "模组")
        print(f"{scope}检查通过，仍需完成原文与内容核对。" if result["ok"] else f"{scope}检查未通过。")
        for item in result.get("errors", []) + result.get("warnings", []):
            print(f"[{item['code']}] {item.get('path', '')} {item.get('subject', '')} {item['message']}")
        if result.get("counts"):
            print(json.dumps(result["counts"], ensure_ascii=False))
        if result.get("new_numbers"):
            print("新增数值（需登记进总索引【新增数值】并写来源）：")
            for item in result["new_numbers"][:15]:
                print(f"  {item['数值']} ×{item['新增次数']}")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
