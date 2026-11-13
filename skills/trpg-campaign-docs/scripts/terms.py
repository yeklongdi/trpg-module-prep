#!/usr/bin/env python3
"""术语台账与可念性扫描。

台账负责一件事：同一个概念的多种译法，在全篇所有产物上统一成一种。
替换只作用于正文与白板文字，保护代码、链接目标、标题锚点与原文底稿；
任何会改变数字的替换直接拒绝。仅依赖标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

RAW_DIR = ("模组", "原文")
FENCE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$")
WIKI_LINK = re.compile(r"\[\[(?P<target>[^]|\n]+?)(?:\\?\|(?P<alias>[^]\n]*))?\]\]")
MARKDOWN_LINK = re.compile(r"\[(?P<label>(?:\\.|[^\\\]\n])*)\]\((?P<target>(?:\\.|[^\\()\n]|\((?:\\.|[^\\()\n])*\))*)\)")
PROTECTED_MARKDOWN = re.compile(
    r"(?m)^[ \t]{0,3}#{1,6}[ \t]+[^\n]*"
    r"|^[ \t]{0,3}\[(?!\[)[^]\n]+\]:[^\n]*"
    r"|^[ \t]*\|[ \t]*(?:\*\*|\x60)?[SNMLCQ]\d{3,}(?:\*\*|\x60)?[ \t]*\|[ \t]*[^|\n]*"
    r"|(?<!\])\]\[(?!\[)[^]\n]+\]"
    r"|\[[^]\n]+\]\[\]"
    r"|(?<!\S)\^[A-Za-z0-9-]+[ \t]*$"
    r"|<(?:(?:https?|mailto):[^>\n]+)>"
    r"|(?P<ticks>\x60+)[^\n]*?(?P=ticks)"
)
NUMBER = re.compile(r"\d+(?:[dD]\d+)?(?:[+-]\d+)?")
SENTENCE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")

TONE_RULES = (
    ("passive", re.compile(r"被[^，。！？；\n]{0,10}(?:所|给|让)[^，。！？；\n]{0,10}"), "被动套被动，现场念会绕，改主动句"),
    ("passive-adverb", re.compile(r"被[^，。！？；\n]{0,20}地"), "「被……地」不成立，改主动或状语"),
    ("long-modifier", re.compile(r"的[^，。！？；\n]{12,}的"), "长定语从句，拆成两句"),
    ("nominalize", re.compile(r"(?:进行|作出|给予|予以|加以)(?:一|一次|一个)?[^，。！？；\n]{0,8}(?:检查|处理|分析|攻击|调查|思考|讨论|评估|搜索)"), "「进行／作出＋名词」改动词"),
    ("as-role", re.compile(r"作为(?:一|一个|一名|一位)[^，。！？；\n]{0,12}"), "「作为一个……」多为直译，删掉或改「身为」"),
    ("when-clause", re.compile(r"当[^，。！？；\n]{0,20}的?时候"), "「当……的时候」改「……时」"),
    ("under-conditions", re.compile(r"在[^，。！？；\n]{0,20}的?(?:情况|基础|条件)下"), "「在……的情况下」改「若／于是」"),
    ("perspective", re.compile(r"从[^，。！？；\n]{0,12}的?(?:角度|立场|观点)(?:来看|来说|看|讲)"), "「从……的角度来看」改直陈"),
    ("filler", re.compile(r"(?:事实上|实际上|基本上|某种程度上|在一定程度上|毫无疑问地?|不得不说)"), "填充副词，删"),
    ("regarding", re.compile(r"关于[^，。！？；\n]{0,20}的?(?:问题|事情|方面)"), "「关于……的问题」改「……的事」"),
    ("one-of", re.compile(r"(?:是|属于)[^，。！？；\n]{0,12}之一"), "「……之一」改具体表述"),
    ("self-pronoun", re.compile(r"(?:你|我|他|她)(?:自己)"), "反身代词直译，按语境删"),
    ("modifier-pile", re.compile(r"[\u4e00-\u9fff]{1,4}的[\u4e00-\u9fff]{1,4}的[\u4e00-\u9fff]{1,4}的"), "三个及以上的「的」，改写"),
    ("such-as", re.compile(r"诸如[^，。！？；\n]{0,20}(?:等|之类)"), "「诸如……之类」重复，删一处"),
    ("there-be", re.compile(r"有(?:一个|一位|一些|某种)[^，。！？；\n]{0,12}(?:存在|坐落|位于)"), "「有……位于」改存现句"),
)
METAPHOR = re.compile(r"(?<![雕像图映想形石画佛偶录影肖遗好])(?:如同|仿佛|宛如|好似|好比|犹如|像)[^。！？\n]{2,40}")
CULTURE = (
    "教堂", "钟楼", "管风琴", "圣歌", "弥撒", "忏悔", "清教徒", "骑士", "男爵", "伯爵", "领主",
    "议会", "市政厅", "酒馆", "驿马车", "煤气灯", "蒸汽", "码头", "贫民窟", "庄园", "决斗",
    "猎枪", "左轮", "警长", "银行家", "债", "契约", "维多利亚", "罗马", "希腊", "圣经",
)
IDIOM = (
    "一石二鸟", "双刃剑", "潘多拉", "达摩克利斯", "阿喀琉斯", "特洛伊", "西西弗斯", "玻璃天花板",
    "房间里的大象", "犹大", "诺亚", "该隐", "巴别塔", "天鹅之歌", "红鲱鱼", "白象",
)
NUMBER_STYLES = (
    (re.compile(r"\d+[dD]\d+"), "骰式"),
    (re.compile(r"\d+\s+[dD]\s+\d+"), "骰式带空格"),
    (re.compile(r"\d+(?:英尺|尺|米|磅|金币|银币|点|名|个|轮|回合|分钟|小时|天|年)"), "数字紧贴单位"),
    (re.compile(r"\d+\s+(?:英尺|尺|米|磅|金币|银币|点|名|个|轮|回合|分钟|小时|天|年)"), "数字与单位间有空格"),
)
SKIP_DIRS = {".backup", "__pycache__", ".git"}


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法解码：" + path.name)


def ledger_path(root: Path) -> Path:
    return root / "模组" / "术语台账.json"


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "terms": [], "history": []}
    data = json.loads(read_text(path))
    data.setdefault("terms", [])
    data.setdefault("history", [])
    return data


def save_ledger(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prose_spans(text: str) -> list[tuple[int, int]]:
    """返回可替换的正文和链接显示文字的区间。"""
    blocked = [(m.start(), m.end()) for m in FENCE.finditer(text)]
    blocked.extend(m.span() for m in PROTECTED_MARKDOWN.finditer(text))
    blocked.extend((m.end("label"), m.end()) for m in MARKDOWN_LINK.finditer(text))
    for link in WIKI_LINK.finditer(text):
        if link.group("alias") is None:
            blocked.append(link.span())
        else:
            blocked.extend(((link.start(), link.start("alias")), (link.end("alias"), link.end())))
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            blocked.append((0, end + 4))
    spans, cursor = [], 0
    for start, stop in sorted(blocked):
        if start > cursor:
            spans.append((cursor, start))
        cursor = max(cursor, stop)
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return spans


def replace_prose(text: str, pattern: str, replacement: str) -> tuple[str, int]:
    count = 0
    pieces, cursor = [], 0
    for start, stop in prose_spans(text):
        chunk = text[start:stop]
        pieces.append(text[cursor:start])
        pieces.append(chunk.replace(pattern, replacement))
        count += chunk.count(pattern)
        cursor = stop
    pieces.append(text[cursor:])
    return "".join(pieces), count


def targets(root: Path) -> list[Path]:
    """所有加工产物：渲染层文件，排除原文底稿与备份。"""
    raw = root.joinpath(*RAW_DIR).resolve()
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".canvas"}:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve().is_relative_to(raw) or path == root / "模组" / "术语表.md":
            continue
        if path.is_relative_to(root / "资料附件" / "备份"):
            continue
        found.append(path)
    return sorted(found)


def cmd_add(args) -> int:
    root = Path(args.root)
    path = ledger_path(root)
    data = load_ledger(path)
    old, new = args.old.strip(), args.new.strip()
    if not old or not new:
        print("[term] 旧写法与新写法都不能为空。")
        return 2
    if old == new:
        print("[term] 旧写法与新写法相同，无需登记。")
        return 2
    if sorted(NUMBER.findall(old)) != sorted(NUMBER.findall(new)):
        print("[term] 替换改变了数字，拒绝登记：" + old + " → " + new)
        return 1
    for term in data["terms"]:
        if term["from"] == old:
            print("[term] 已存在同源写法，先 undo 或改 list 查看：" + old)
            return 1
    data["terms"].append(
        {
            "id": max([t["id"] for t in data["terms"]] or [0]) + 1,
            "group": args.group or new,
            "from": old,
            "to": new,
            "note": args.note or "",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    )
    save_ledger(path, data)
    print(f"已登记术语：{old} → {new}（组：{args.group or new}）")
    return 0


def cmd_list(args) -> int:
    data = load_ledger(ledger_path(Path(args.root)))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if not data["terms"]:
        print("术语台账为空；先 scan 找候选，再 add 登记。")
        return 0
    print("组｜旧写法 → 统一为｜登记时间")
    for term in data["terms"]:
        print(f"{term['group']}｜{term['from']} → {term['to']}｜{term['created_at']}")
    return 0


def cmd_apply(args) -> int:
    root = Path(args.root).resolve()
    path = ledger_path(root)
    data = load_ledger(path)
    if not data["terms"]:
        print("[term] 台账为空，先 add 登记术语。")
        return 2
    files = targets(root)
    if not files:
        print("[term] 没有可替换的产物；先跑 intake.py 与成稿流程。")
        return 2
    terms = sorted(data["terms"], key=lambda t: len(t["from"]), reverse=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = root / "模组" / ".backup" / stamp
    per_file, total, untouched = [], 0, []
    updated = {}
    for file in files:
        try:
            text = read_text(file)
        except ValueError as exc:
            print("[term] 跳过 " + str(exc))
            continue
        canvas = None
        if file.suffix == ".canvas":
            try:
                canvas = json.loads(text)
            except json.JSONDecodeError:
                print("[fidelity] 白板 JSON 无效，无法替换：" + str(file.relative_to(root)))
                return 1
            nodes = canvas.get("nodes") if isinstance(canvas, dict) else None
            if not isinstance(nodes, list):
                print("[fidelity] 白板缺少节点数组：" + str(file.relative_to(root)))
                return 1
            texts = [
                node for node in nodes
                if isinstance(node, dict) and node.get("type") == "text" and isinstance(node.get("text"), str)
            ]
        else:
            texts = [{"text": text}]
        hits_total = 0
        for item in texts:
            before = body = item["text"]
            for term in terms:
                body, hits = replace_prose(body, term["from"], term["to"])
                hits_total += hits
            if sorted(NUMBER.findall(body)) != sorted(NUMBER.findall(before)):
                print("[fidelity] 替换改变了数字，已终止：" + str(file.relative_to(root)))
                return 1
            item["text"] = body
        if not hits_total:
            untouched.append(file)
            continue
        updated[file] = json.dumps(canvas, ensure_ascii=False, indent=2) + "\n" if canvas is not None else texts[0]["text"]
        total += hits_total
        per_file.append({"path": file.relative_to(root).as_posix(), "count": hits_total})
    if not args.write:
        print(f"预演：{len(per_file)} 个文件将被改写，共 {total} 处；加 --write 落盘。")
        for item in per_file:
            print(f"  {item['path']} ×{item['count']}")
        if untouched:
            print(f"  未命中：{len(untouched)} 个文件保持原样")
        return 0
    for item in per_file:
        source = root / item["path"]
        backup = backup_dir / item["path"]
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)
    if not updated:
        print("没有需要替换的正文。")
        return 0
    for file, text in updated.items():
        file.write_text(text, encoding="utf-8")
    entry = {
        "id": max([h["id"] for h in data["history"]] or [0]) + 1,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "backup": str(backup_dir.relative_to(root)).replace("\\", "/"),
        "files": per_file,
        "total": total,
        "undone": False,
    }
    data["history"].append(entry)
    save_ledger(path, data)
    print(f"已改写 {len(per_file)} 个文件，共 {total} 处；记录 #{entry['id']}，可用 undo 回退。")
    return 0


def cmd_undo(args) -> int:
    root = Path(args.root)
    path = ledger_path(root)
    data = load_ledger(path)
    pending = [h for h in data["history"] if not h.get("undone")]
    if not pending:
        print("[undo] 没有可回退的记录。")
        return 0
    entry = next((h for h in reversed(pending) if h["id"] == args.id), None) if args.id else pending[-1]
    if entry is None:
        print("[undo] 没有该编号的记录：" + str(args.id))
        return 2
    backup_dir = root / entry["backup"]
    restored = 0
    for item in entry["files"]:
        backup = backup_dir / item["path"]
        if not backup.is_file():
            print("[undo] 备份缺失，跳过：" + item["path"])
            continue
        target = root / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)
        restored += 1
    entry["undone"] = True
    save_ledger(path, data)
    print(f"已回退 #{entry['id']}：恢复 {restored} 个文件。")
    return 0


def cmd_export(args) -> int:
    root = Path(args.root)
    data = load_ledger(ledger_path(root))
    out = Path(args.output) if Path(args.output).is_absolute() else root / args.output
    lines = ["# 术语表", "", "统一写法以本表为准；替换由 模组/术语台账.json 与 terms.py 执行。", ""]
    if not data["terms"]:
        lines.append("（暂无登记术语）")
    else:
        lines += ["| 概念组 | 统一为 | 曾用写法 | 说明 |", "| --- | --- | --- | --- |"]
        for term in sorted(data["terms"], key=lambda t: (t["group"], t["id"])):
            lines.append(f"| {term['group']} | {term['to']} | {term['from']} | {term.get('note', '') or '-'} |")
    if data["history"]:
        lines += ["", "## 替换记录", "", "| 编号 | 时间 | 文件数 | 处数 | 状态 |", "| --- | --- | --- | --- | --- |"]
        for entry in data["history"]:
            state = "已回退" if entry.get("undone") else "生效"
            lines.append(f"| {entry['id']} | {entry['time']} | {len(entry.get('files', []))} | {entry['total']} | {state} |")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已导出 {out}（{len(data['terms'])} 条术语，{len(data['history'])} 次替换）。")
    return 0


MDLINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def plain_md(text: str) -> str:
    """去掉链接、加粗与行内代码，只留能念的字。"""
    return re.sub(r"[*_`]", "", MDLINK.sub(r"\1", text)).strip()


def spoken_lines(lines: list[str]) -> list[tuple[int, str]]:
    """只取会被念出来的行：引用块，以及「台词」小节下的列表项。

    导航链接、来源行、主持人备注都不进可念性检查——那些不朗读。
    """
    out, dialogue = [], False
    for index, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            dialogue = "台词" in stripped
            continue
        if stripped.startswith(">"):
            out.append((index, stripped.lstrip("> ").strip()))
        elif dialogue and stripped[:2] in {"- ", "* "}:
            out.append((index, stripped[2:].strip()))
    return out


def cmd_scan(args) -> int:
    paths = [Path(p) for p in args.paths]
    files: list[Path] = []
    for target in paths:
        if target.is_dir():
            files.extend(sorted(p for p in target.rglob("*") if p.suffix in {".md", ".canvas"}))
        elif target.is_file():
            files.append(target)
    if not files:
        print("[scan] 没有找到 .md 或 .canvas 文件。")
        return 2
    findings, metaphors, culture_nouns, long_sentences = [], [], {}, []
    style_hits: dict[str, list] = defaultdict(list)
    for file in files:
        text = read_text(file)
        lines = text.splitlines()
        for lineno, line in enumerate(lines, 1):
            body = line.strip()
            if not body:
                continue
            for word in CULTURE:
                if word in body and word not in culture_nouns:
                    culture_nouns[word] = {"file": file.name, "line": lineno, "word": word}
            for pattern, name in NUMBER_STYLES:
                match = pattern.search(body)
                if match:
                    style_hits[name].append({"file": file.name, "line": lineno, "sample": match.group()})
        for lineno, spoken in spoken_lines(lines):
            clean = plain_md(spoken)
            if not clean:
                continue
            for name, pattern, hint in TONE_RULES:
                for match in pattern.finditer(clean):
                    findings.append({"file": file.name, "line": lineno, "rule": name, "hint": hint, "sample": match.group()[:40]})
            for match in METAPHOR.finditer(clean):
                before = clean[match.start() - 1] if match.start() else ""
                if len(match.group()) >= 6 and before not in "看好图想形录就":
                    metaphors.append({"file": file.name, "line": lineno, "kind": "比喻", "sample": match.group()[:40]})
            for word in IDIOM:
                if word in clean:
                    metaphors.append({"file": file.name, "line": lineno, "kind": "典故", "sample": word})
            for sentence in SENTENCE.findall(clean):
                if len(sentence.strip()) > args.max_chars:
                    long_sentences.append({"file": file.name, "line": lineno, "chars": len(sentence.strip()), "sample": sentence.strip()[:50]})
    grouped = Counter(item["rule"] for item in findings)
    result = {
        "files": len(files),
        "tone": dict(grouped),
        "long_sentences": len(long_sentences),
        "metaphors": len(metaphors),
        "culture_nouns": len(culture_nouns),
    }
    if args.json:
        print(json.dumps({"summary": result, "tone": findings[:60], "long": long_sentences[:30], "metaphors": metaphors[:30]}, ensure_ascii=False, indent=2))
        return 0
    print(f"扫描 {len(files)} 个文件（可念性阈值 {args.max_chars} 字）。")
    if grouped:
        print("\n【翻译腔候选】")
        for name, count in grouped.most_common():
            hint = next(h for n, _, h in TONE_RULES if n == name)
            print(f"- {name} ×{count}：{hint}")
            for item in [f for f in findings if f["rule"] == name][: args.limit]:
                print(f"    {item['file']} L{item['line']} {item['sample']}")
    else:
        print("\n【翻译腔候选】未命中。")
    if long_sentences:
        print(f"\n【超长句】{len(long_sentences)} 处超过 {args.max_chars} 字，念下来会赶：")
        for item in long_sentences[: args.limit * 4]:
            print(f"    {item['file']} L{item['line']} {item['chars']}字 {item['sample']}")
    if metaphors:
        print("\n【比喻与典故】")
        for item in metaphors[: args.limit * 4]:
            print(f"    {item['file']} L{item['line']} [{item['kind']}] {item['sample']}")
    if culture_nouns:
        print("\n【文化名物】默认保留，仅在会误解时加半句说明：")
        print("    " + "、".join(list(culture_nouns)[:20]))
    print("\n候选只是排查清单，不是必须改；改动后人要复核原意。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="术语台账与可念性扫描。")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="登记一条术语替换")
    add.add_argument("--root", required=True, help="模组根目录")
    add.add_argument("--group", default="", help="概念组，默认同统一写法")
    add.add_argument("--from", dest="old", required=True, help="需要替换掉的旧写法")
    add.add_argument("--to", dest="new", required=True, help="统一采用的新写法")
    add.add_argument("--note", default="")

    listing = sub.add_parser("list", help="查看台账")
    listing.add_argument("--root", required=True, help="模组根目录")
    listing.add_argument("--json", action="store_true")

    apply_cmd = sub.add_parser("apply", help="把台账套用到所有产物")
    apply_cmd.add_argument("root", type=Path, help="模组根目录")
    apply_cmd.add_argument("--write", action="store_true", help="真正落盘；默认预演")

    undo = sub.add_parser("undo", help="回退上一次替换")
    undo.add_argument("root", type=Path, help="模组根目录")
    undo.add_argument("--id", type=int, default=0)

    export = sub.add_parser("export", help="导出人读术语表")
    export.add_argument("root", type=Path, help="模组根目录")
    export.add_argument("output", help="术语表路径，相对根目录或绝对路径")

    scan = sub.add_parser("scan", help="扫描翻译腔、超长句、比喻与文化名物")
    scan.add_argument("paths", nargs="+", help="文件或目录")
    scan.add_argument("--limit", type=int, default=2, help="每类样例条数")
    scan.add_argument("--max-chars", dest="max_chars", type=int, default=40, help="句子长度上限")
    scan.add_argument("--json", action="store_true")

    args = parser.parse_args()
    handlers = {"add": cmd_add, "list": cmd_list, "apply": cmd_apply, "undo": cmd_undo, "export": cmd_export, "scan": cmd_scan}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
