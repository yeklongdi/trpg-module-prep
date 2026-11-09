#!/usr/bin/env python3
"""模组原文抽取与版面清洗：输出干净全文、分段清单与分段数据，落到 模组/原文/。

文本、DOCX、Markdown 只用标准库解析；PDF 版面还原优先使用 PyMuPDF，
缺少时明确报错并列出需要手工喂入的页码，不静默产出错乱正文。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

TEXT_SUFFIX = {".md", ".markdown", ".txt"}
DOCX_SUFFIX = {".docx"}
PDF_SUFFIX = {".pdf"}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

PAGE_NUMBER = re.compile(
    r"^\s*(?:[-—–·]\s*)?(?:第\s*\d{1,4}\s*页|\d{1,4}\s*/\s*\d{1,4}|page\s*\d{1,4}|\d{1,4})\s*(?:[-—–·])?\s*$",
    re.I,
)
CHAPTER_PATTERNS = (
    re.compile(r"^\s{0,3}#{1,6}\s+\S"),
    re.compile(r"^\s*\*\*[^*\n]{2,60}\*\*\s*$"),
    re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]{1,4}\s*[章节回幕部]"),
    re.compile(r"^\s*(?:chapter|part|appendix)\s+[\divxlcdm]+", re.I),
    re.compile(r"^\s*(?:附录|场景\s*\d+|scene\s*\d+)", re.I),
    re.compile(r"^\s*(?:序章|楔子|尾声|后记|前言|引言)\s*$"),
)
STAT_BLOCK = re.compile(
    r"(?i)\b(?:armor class|hit points|challenge|proficiency|saving throws?|damage|"
    r"legendary actions?)\b|护甲等级|生命值|挑战等级|豁免|属性值|伤害|传奇动作"
)
LIST_LIKE = re.compile(r"^\s*(?:[-*+·•▪‣]\s|\d+[.)、]\s|>\s|\|)")
TERMINAL = "。！？；：”』」）)…!?.;:"


def _cjk_heavy(text: str) -> bool:
    if not text:
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk >= max(1, len(text) // 3)


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法以 UTF-8 或 GB18030 解码：" + path.name)


def read_docx(path: Path) -> str:
    """按段落与表格还原 DOCX，标题样式转成 Markdown 标题。"""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(W + "body")
    lines: list[str] = []
    if body is None:
        return ""
    for block in body:
        if block.tag == W + "p":
            lines.extend(_docx_paragraph(block))
        elif block.tag == W + "tbl":
            lines.extend(_docx_table(block))
    return "\n".join(lines)


def _docx_paragraph(paragraph: ET.Element) -> list[str]:
    style = ""
    prop = paragraph.find(W + "pPr")
    if prop is not None:
        node = prop.find(W + "pStyle")
        if node is not None:
            style = node.get(W + "val", "")
    runs: list[list] = []
    for run in paragraph.iter(W + "r"):
        text, bold, italic = _run_parts(run)
        if not text:
            continue
        if runs and runs[-1][1] == bold and runs[-1][2] == italic:
            runs[-1][0] += text
        else:
            runs.append([text, bold, italic])
    text = "".join(_decorate(*item) for item in runs)
    text = text.replace("\u00a0", " ").strip()
    if not text:
        return []
    level = 0
    match = re.search(r"(?:heading|标题)\s*(\d)", style, re.I)
    if match:
        level = max(1, min(6, int(match.group(1))))
    elif style.lower() in {"title", "标题"}:
        level = 1
    if level:
        return ["#" * level + " " + text.replace("**", "").replace("*", "")]
    return [text]


def _run_parts(run: ET.Element) -> tuple[str, bool, bool]:
    parts: list[str] = []
    for node in run:
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag == W + "br":
            parts.append("\n")
    text = "".join(parts)
    bold = italic = False
    prop = run.find(W + "rPr")
    if prop is not None:
        bold = prop.find(W + "b") is not None
        italic = prop.find(W + "i") is not None
    return text, bold, italic


def _decorate(text: str, bold: bool, italic: bool) -> str:
    if not text.strip():
        return text
    inner = text
    leading = inner[: len(inner) - len(inner.lstrip())]
    trailing = inner[len(inner.rstrip()) :]
    core = inner.strip()
    if italic:
        core = "*" + core + "*"
    if bold:
        core = "**" + core + "**"
    return leading + core + trailing


def _docx_table(table: ET.Element) -> list[str]:
    rows: list[str] = []
    for row in table.iter(W + "tr"):
        cells = []
        for cell in row.iter(W + "tc"):
            text = " ".join(
                "".join(_decorate(*_run_parts(run)) for run in para.iter(W + "r")).strip()
                for para in cell.iter(W + "p")
            )
            cells.append(re.sub(r"\s+", " ", text).strip())
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    if len(rows) >= 2:
        header, rest = rows[0], rows[1:]
        width = header.count("|") - 1
        return [header, "| " + " | ".join(["---"] * max(1, width)) + " |"] + rest
    return rows


def read_pdf(path: Path) -> tuple[str, dict]:
    """用 PyMuPDF 还原版面：分栏排序、页眉页脚过滤、页码剔除。"""
    try:
        import fitz  # type: ignore
    except ImportError:
        raise MissingPdfBackend(
            "缺少 PyMuPDF，无法还原 PDF 版面。可执行 python -m pip install pymupdf，"
            "或让模型直接读取 PDF 后把正文写成 模组/原文/00-全文.md 再按 references 的降级路径补齐分段数据。"
        )
    document = fitz.open(str(path))
    pages = []
    for page in document:
        blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
        pages.append((page.rect.width, page.rect.height, blocks))
    drop = _repeated_margins(pages)
    chunks: list[str] = []
    ocr_pages: list[int] = []
    for number, (width, height, blocks) in enumerate(pages, 1):
        ordered = _order_blocks(blocks, width, height)
        lines = []
        for block in ordered:
            if _in_margin(block, height):
                text = _block_text(block).strip()
                if _margin_key(text) in drop:
                    continue
            lines.extend(line for line in _block_text(block).splitlines() if line.strip())
        text = "\n".join(line for line in lines if not PAGE_NUMBER.match(line))
        if len(text.strip()) < 20:
            ocr_pages.append(number)
        chunks.append(text)
    document.close()
    info = {
        "pages": len(pages),
        "ocr_pages": ocr_pages,
        "backend": "pymupdf",
        "dropped_margins": sorted(drop),
    }
    return "\n\n".join(chunks), info


class MissingPdfBackend(Exception):
    """PDF 解析后端缺失。"""


def _block_text(block: dict) -> str:
    lines = []
    for line in block.get("lines", []):
        spans = "".join(span.get("text", "") for span in line.get("spans", []))
        if spans.strip():
            lines.append(spans.rstrip())
    return "\n".join(lines)


def _in_margin(block: dict, height: float) -> bool:
    _, y0, _, y1 = block["bbox"]
    return y1 < height * 0.08 or y0 > height * 0.92


def _margin_key(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", "", text))


def _repeated_margins(pages) -> set:
    counter: dict[str, int] = {}
    for _, height, blocks in pages:
        seen = set()
        for block in blocks:
            if _in_margin(block, height):
                key = _margin_key(_block_text(block))
                if key:
                    seen.add(key)
        for key in seen:
            counter[key] = counter.get(key, 0) + 1
    threshold = max(2, int(len(pages) * 0.3))
    return {key for key, count in counter.items() if count >= threshold and len(key) >= 2}


def _order_blocks(blocks, width: float, height: float):
    mid = width / 2
    left = [b for b in blocks if b["bbox"][2] <= mid + 2]
    right = [b for b in blocks if b["bbox"][0] >= mid - 2]
    if len(left) >= 2 and len(right) >= 2 and len(left) + len(right) >= len(blocks) - 2:
        return sorted(blocks, key=lambda b: (0 if b["bbox"][2] <= mid + 2 else 1, b["bbox"][1], b["bbox"][0]))
    band = max(8.0, height / 60)
    return sorted(blocks, key=lambda b: (round(b["bbox"][1] / band), b["bbox"][0]))


def merge_wrapped(lines: list[str]) -> list[str]:
    """合并被版面折断的段行，保留列表、标题、数据块与引用。"""
    result: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            result.append("")
            continue
        if not result or not result[-1].strip():
            result.append(line)
            continue
        previous = result[-1]
        if _keep_break(previous, line):
            result.append(line)
            continue
        joiner = "" if (_cjk_heavy(previous) or _cjk_heavy(line)) else " "
        result[-1] = previous + joiner + line.strip()
    compact: list[str] = []
    for line in result:
        if line.strip() or (compact and compact[-1].strip()):
            compact.append(line)
    return compact


def _keep_break(previous: str, line: str) -> bool:
    if _heading_text(line) or LIST_LIKE.match(line) or line.lstrip().startswith(("```", "~~~")):
        return True
    if _heading_text(previous) or LIST_LIKE.match(previous) or previous.lstrip().startswith(("```", "~~~")):
        return True
    if STAT_BLOCK.search(previous) or STAT_BLOCK.search(line):
        return True
    if re.search(r"\d$", previous) and re.match(r"^[\d+\-]", line):
        return True
    return previous[-1] in TERMINAL


def _heading_text(line: str) -> str:
    """返回标题文本；不是标题时返回空串。"""
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return ""
    match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", stripped)
    if match:
        return match.group(1).strip()
    match = re.match(r"^\*\*([^*\n]{2,60})\*\*$", stripped)
    if match:
        return match.group(1).strip()
    if any(pattern.match(stripped) for pattern in CHAPTER_PATTERNS[2:]):
        return stripped
    return ""


def split_chapters(lines: list[str]) -> list[dict]:
    chapters: list[dict] = []
    current = {"title": "全文", "start": 0, "lines": []}
    for index, line in enumerate(lines):
        title = _heading_text(line)
        if title:
            if current["lines"] or chapters:
                chapters.append(current)
            heading = line.strip()
            if not re.match(r"^#{1,6}\s", heading):
                heading = "## " + title
            current = {"title": title, "start": index, "lines": [heading]}
            continue
        current["lines"].append(line)
    chapters.append(current)
    if len(chapters) == 1 and not chapters[0]["lines"]:
        return []
    return chapters


def normalize(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    lines = [line.rstrip() for line in text.split("\n")]
    merged = merge_wrapped(lines)
    while merged and not merged[0].strip():
        merged.pop(0)
    while merged and not merged[-1].strip():
        merged.pop()
    return merged


def collect(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    files = []
    for item in sorted(path.rglob("*")):
        if item.is_file() and item.suffix.lower() in TEXT_SUFFIX | DOCX_SUFFIX | PDF_SUFFIX:
            files.append(item)
    return files


def extract(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIX:
        return read_text_file(path), {"backend": "plain", "pages": None, "ocr_pages": []}
    if suffix in DOCX_SUFFIX:
        return read_docx(path), {"backend": "docx", "pages": None, "ocr_pages": []}
    return read_pdf(path)


def run(source: Path, target: Path, name: str) -> tuple[dict, int]:
    files = collect(source)
    if not files:
        return {"ok": False, "errors": [{"code": "input", "message": "没有找到可解析的 PDF、DOCX、TXT 或 Markdown。"}]}, 2
    raw_dir = target / "模组" / "原文"
    raw_dir.mkdir(parents=True, exist_ok=True)
    bodies: list[str] = []
    sources: list[dict] = []
    failures: list[dict] = []
    for path in files:
        try:
            text, info = extract(path)
        except MissingPdfBackend as exc:
            failures.append({"code": "pdf-backend", "message": str(exc), "path": path.name})
            continue
        except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
            failures.append({"code": "read", "message": "读取失败：" + str(exc), "path": path.name})
            continue
        info.update({"path": path.name, "chars": len(text.strip())})
        sources.append(info)
        if len(files) > 1:
            bodies.append("## 来源：" + path.stem)
        bodies.append(text)
    if not bodies:
        return {"ok": False, "errors": failures or [{"code": "read", "message": "全部文件解析失败。"}]}, 3

    lines = normalize("\n\n".join(bodies))
    chapters = split_chapters(lines)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = [
        "<!-- 原文底稿，由 intake.py 生成，只读；加工产物不得写回本目录 -->",
        f"<!-- 模组：{name} · 生成：{stamp} -->",
        "",
    ]
    body_lines: list[str] = []
    segments: list[dict] = []
    for index, chapter in enumerate(chapters, 1):
        start = len(body_lines) + len(header) + 1
        body_lines.append("")
        body_lines.extend(chapter["lines"])
        end = len(body_lines) + len(header)
        content = "\n".join(chapter["lines"]).strip()
        segments.append(
            {
                "index": index,
                "title": chapter["title"],
                "chars": len(content),
                "start_line": start,
                "end_line": end,
                "numbers": _numbers(content),
            }
        )
    full = "\n".join(header + body_lines).replace("\n\n\n", "\n\n").strip() + "\n"
    (raw_dir / "00-全文.md").write_text(full, encoding="utf-8")
    (raw_dir / "segments.json").write_text(
        json.dumps({"module": name, "generated": stamp, "sources": sources, "segments": segments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (raw_dir / "01-分段清单.md").write_text(_segment_table(name, stamp, sources, segments, failures), encoding="utf-8")

    result = {
        "ok": not failures,
        "module": name,
        "output": str(raw_dir),
        "full_text": str(raw_dir / "00-全文.md"),
        "sources": sources,
        "segments": [{"index": s["index"], "title": s["title"], "chars": s["chars"]} for s in segments],
        "counts": {"文件": len(sources), "章节": len(segments), "字数": sum(s["chars"] for s in segments)},
        "errors": failures,
        "warnings": [
            "第 %s 页几乎无文本，可能需 OCR。" % "、".join(str(n) for n in info.get("ocr_pages") or [])
            for info in sources
            if info.get("ocr_pages")
        ],
    }
    return result, (0 if not failures else 3)


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[dD]\d+)?(?:[+-]\d+)?", text)


def _segment_table(name, stamp, sources, segments, failures) -> str:
    lines = [
        "# 分段清单",
        "",
        f"- 模组：{name}",
        f"- 生成：{stamp}",
        "",
        "## 来源",
        "",
        "| 文件 | 方式 | 页数 | 字数 | 需 OCR 页 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for info in sources:
        pages = info.get("pages")
        ocr = "、".join(str(n) for n in info.get("ocr_pages") or []) or "-"
        lines.append(f"| {info['path']} | {info.get('backend', '-')} | {pages if pages else '-'} | {info.get('chars', '-')} | {ocr} |")
    lines += ["", "## 章节", "", "| 序号 | 标题 | 字数 | 行号 |", "| --- | --- | --- | --- |"]
    for segment in segments:
        title = segment["title"].replace("|", "／")
        lines.append(f"| {segment['index']} | {title} | {segment['chars']} | {segment['start_line']}–{segment['end_line']} |")
    if failures:
        lines += ["", "## 未解析", ""]
        lines += [f"- {item['path']}：{item['message']}" for item in failures]
    lines += ["", "原文底稿只读；术语统一写进 模组/正文.md，其余加工落在带团文档、npc、怪物。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="抽取模组原文并清理版面，产出原文底稿与分段清单。")
    parser.add_argument("source", type=Path, help="模组原文文件或目录")
    parser.add_argument("target", type=Path, help="模组根目录（将创建 模组/原文/）")
    parser.add_argument("--name", default="", help="模组名，默认取输出目录名")
    parser.add_argument("--json", action="store_true", help="输出 JSON 到终端")
    args = parser.parse_args()
    if not args.source.exists():
        print("[input] 原文路径不存在：" + str(args.source))
        return 2
    name = args.name or args.target.name or args.source.stem
    try:
        result, code = run(args.source, args.target, name)
    except (OSError, ValueError) as exc:
        print("[read] 抽取失败：" + str(exc))
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("counts"):
            print(f"已生成原文底稿：{result['full_text']}")
            print(json.dumps(result["counts"], ensure_ascii=False))
            for segment in result.get("segments", []):
                print(f"  {segment['index']:02d} {segment['title']}（{segment['chars']} 字）")
        for item in result.get("errors", []) + result.get("warnings", []):
            print(f"[{item.get('code', 'warn')}] {item.get('message', item)}")
        if code == 0:
            print("原文底稿完成；下一步建术语台账再精修。")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
