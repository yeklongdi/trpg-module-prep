#!/usr/bin/env python3
"""将章内 流程（白板）.canvas 与 流程（文字）.md / 主持CG.md 双向链接，并支持从文字路线生成白板。

默认仅预演输出将修改的内容，加 --write 后先备份至 模组/.backup/ 再落盘。仅依赖标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

from terms import WIKI_LINK, MARKDOWN_LINK, read_text

ENTITY_HEADING = re.compile(r"^([S]\d{3,})(?:\s+(.+))?$")
MERMAID_EDGE = re.compile(
    r"""(?mx)^\s*
    (?P<src>S\d{3,})(?:\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}))?
    \s*-{1,2}>\s*
    (?:\|(?P<label>[^|\n]+)\|\s*)?
    (?P<dst>S\d{3,})(?:\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}))?
    """
)
DETAIL_MARKER = re.compile(r"[（(](S\d{3,})[）)]|^(S\d{3,})\s*[·・]")

DEFAULT_CARD_WIDTH = 560
DEFAULT_CARD_HEIGHT = 400
HORIZONTAL_GAP = 180
COLUMN_STEP = DEFAULT_CARD_WIDTH + HORIZONTAL_GAP
ROW_STEP = DEFAULT_CARD_HEIGHT + 160
HEADER_HEIGHT = 190
DETAIL_SHORT_LABEL = "区域细则"


def clean_heading(value: str) -> str:
    return value.strip().strip("【】").replace("**", "").replace("`", "").strip()


def strip_code_fences(text: str) -> str:
    lines, fence, front = [], None, text.startswith("---\n") or text.startswith("---\r\n")
    for idx, line in enumerate(text.splitlines()):
        if front:
            if idx and line.strip() == "---":
                front = False
            lines.append("")
            continue
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            lines.append("")
        else:
            lines.append("" if fence else line)
    return "\n".join(lines)


def extract_h1(text: str) -> str:
    masked = strip_code_fences(text)
    match = re.search(r"(?m)^ {0,3}# +(.+?)[ \t]*#*[ \t]*$", masked)
    return clean_heading(match.group(1)) if match else ""


def extract_h2_scenes(text: str) -> list[dict]:
    masked = strip_code_fences(text)
    matches = list(re.finditer(r"(?m)^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", masked))
    scenes = []
    for idx, match in enumerate(matches):
        if len(match.group(1)) != 2:
            continue
        title = clean_heading(match.group(2))
        ent = ENTITY_HEADING.fullmatch(title)
        if not ent or not ent.group(2):
            continue
        sid, sname = ent.groups()
        stop = next((n.start() for n in matches[idx + 1 :] if len(n.group(1)) <= 2), len(masked))
        body = masked[match.end() : stop].strip()
        scenes.append({"id": sid, "name": sname, "title": f"{sid} {sname}", "body": body})
    return scenes


def parse_route_edges(text: str, scene_ids: list[str]) -> list[tuple[str, str, str]]:
    """从 ## 场景路线（Mermaid 或 Markdown 缩进大纲）抽取有向转场边。"""
    known = set(scene_ids)
    headings = list(re.finditer(r"(?m)^ {0,3}#{2}[ \t]+(.+?)[ \t]*$", text))
    route_block = text
    for idx, match in enumerate(headings):
        if "场景路线" in clean_heading(match.group(1)):
            stop = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
            route_block = text[match.end() : stop]
            break

    edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for match in MERMAID_EDGE.finditer(route_block):
        src, label, dst = match.group("src"), (match.group("label") or "").strip(), match.group("dst")
        if src in known and dst in known and (src, dst, label) not in seen:
            seen.add((src, dst, label))
            edges.append((src, dst, label))

    if not edges:
        stack: list[tuple[int, str]] = []
        for raw_line in route_block.splitlines():
            bullet = re.match(r"^(\s*)[-*+]\s+(.+)$", raw_line)
            if not bullet:
                continue
            indent = len(bullet.group(1).expandtabs(2))
            item = clean_heading(bullet.group(2))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            arrow = re.search(r"(.+?)(?:->|→)\s*(S\d{3,})\b", item)
            if arrow and stack:
                label = arrow.group(1).strip(" ：:-")
                dst = arrow.group(2)
                src = stack[-1][1]
                if src in known and dst in known and (src, dst, label) not in seen:
                    seen.add((src, dst, label))
                    edges.append((src, dst, label))
                stack.append((indent, dst))
                continue
            lead = re.match(r"^(S\d{3,})\b", item)
            if lead and lead.group(1) in known:
                sid = lead.group(1)
                if stack and stack[-1][1] != sid and (stack[-1][1], sid, "") not in seen:
                    seen.add((stack[-1][1], sid, ""))
                    edges.append((stack[-1][1], sid, ""))
                stack.append((indent, sid))

    if not edges and len(scene_ids) >= 2:
        for i in range(len(scene_ids) - 1):
            edges.append((scene_ids[i], scene_ids[i + 1], ""))
    return edges


def summarize_scene_body(body: str) -> str:
    """从流程正文提取不编造事实的简明速览要点，供新建白板使用。"""
    bullets: list[str] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("#", ">", "!", "---", "**导航", "- **同行导航", "- **原文页码")):
            continue
        if stripped in {"", "---"}:
            continue
        plain = MARKDOWN_LINK.sub(r"\1", WIKI_LINK.sub(lambda m: m.group("alias") or m.group("target"), stripped))
        plain = plain.lstrip("-* ").strip()
        if not plain or plain.startswith("白板导航"):
            continue
        if len(plain) > 120:
            plain = plain[:117] + "…"
        bullets.append(f"- {plain}")
        if len(bullets) >= 3:
            break
    return "\n".join(bullets) if bullets else "- 详见《流程（文字）》对应场景正文。"


def build_scene_jump_line(sid: str, title: str, text_file: Path, cg_file: Path | None, has_cg_scene: bool) -> str:
    parts = [f"[[{text_file.stem}#{title}|跳转文字流程（{sid}）]]"]
    if cg_file and has_cg_scene:
        parts.append(f"[[{cg_file.stem}#{title}|主持CG]]")
    return "## 👉 " + " ｜ ".join(parts)


def build_detail_jump_line(sid: str, title: str, text_file: Path, cg_file: Path | None, has_cg_scene: bool) -> str:
    parts = [f"[[{text_file.stem}#{title}|👉 跳至《流程（文字）》{sid} 小节]]"]
    if cg_file and has_cg_scene:
        parts.append(f"[[{cg_file.stem}#{title}|主持CG]]")
    return "- **直达正文**：" + " ｜ ".join(parts)


def build_header_jump_line(text_file: Path, cg_file: Path | None, index_file: Path | None) -> str:
    parts = [f"[[{text_file.stem}|打开本章《流程（文字）》]]"]
    if cg_file:
        parts.append(f"[[{cg_file.stem}|打开本章《主持CG》]]")
    if index_file and index_file.is_file():
        parts.append(f"[[{index_file.stem}|返回《模组总索引》]]")
    return "## 👉 " + " ｜ ".join(parts)


def _is_legacy_scene_nav_line(line: str, text_file: Path, cg_file: Path | None) -> bool:
    s = line.strip()
    if s.startswith("## 👉 "):
        return True
    if re.match(r"^(?:[-*]\s*)?\*\*导航\*\*\s*[:：]", s):
        return True
    if s.startswith("[[") or s.startswith("["):
        targets = [m.group("target") for m in WIKI_LINK.finditer(s)]
        targets += [m.group("target") for m in MARKDOWN_LINK.finditer(s)]
        stems = {text_file.stem, text_file.name}
        if cg_file:
            stems |= {cg_file.stem, cg_file.name}
        leftover = WIKI_LINK.sub("", MARKDOWN_LINK.sub("", s)).strip(" ｜|·/")
        if targets and not leftover and all(any(st in t for st in stems) for t in targets):
            return True
    return False


def update_scene_card_text(
    content: str,
    sid: str,
    title: str,
    text_file: Path,
    cg_file: Path | None,
    has_cg_scene: bool,
) -> str:
    lines = content.splitlines()
    body_lines = [
        line for line in lines[1:]
        if not _is_legacy_scene_nav_line(line, text_file, cg_file)
    ]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    jump_line = build_scene_jump_line(sid, title, text_file, cg_file, has_cg_scene)
    rebuilt = [f"# {title}", jump_line] + body_lines
    return "\n".join(rebuilt).rstrip()


def update_detail_card_text(
    content: str,
    sid: str,
    title: str,
    text_file: Path,
    cg_file: Path | None,
    has_cg_scene: bool,
) -> str:
    lines = content.splitlines()
    if not lines:
        return content
    first = lines[0]
    rest = [line for line in lines[1:] if not line.strip().startswith("- **直达正文**：")]
    while rest and not rest[0].strip():
        rest.pop(0)
    jump_line = build_detail_jump_line(sid, title, text_file, cg_file, has_cg_scene)
    return "\n".join([first, jump_line] + rest).rstrip()


def update_header_card_text(content: str, text_file: Path, cg_file: Path | None, index_file: Path | None) -> str:
    lines = content.splitlines()
    if not lines:
        return content
    first = lines[0]
    jump_line = build_header_jump_line(text_file, cg_file, index_file)
    rest = []
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("## 👉 "):
            continue
        if re.match(r"^(?:[-*]\s*)?\*\*核心文档\*\*\s*[:：]", s):
            continue
        rest.append(line)
    while rest and not rest[0].strip():
        rest.pop(0)
    return "\n".join([first, jump_line] + rest).rstrip()


def create_canvas_from_flow(
    chapter_dir: Path,
    text_file: Path,
    cg_file: Path | None,
    index_file: Path | None,
    scenes: list[dict],
    cg_scene_ids: set[str],
    edges: list[tuple[str, str, str]],
) -> dict:
    scene_map = {s["id"]: s for s in scenes}
    scene_ids = [s["id"] for s in scenes]
    indegree = {sid: 0 for sid in scene_ids}
    adj: dict[str, list[str]] = {sid: [] for sid in scene_ids}
    for src, dst, _ in edges:
        if src in adj and dst in indegree and src != dst:
            adj[src].append(dst)
            indegree[dst] += 1

    depth: dict[str, int] = {}
    queue = deque([sid for sid in scene_ids if indegree[sid] == 0] or scene_ids[:1])
    for sid in queue:
        depth[sid] = 0
    while queue:
        cur = queue.popleft()
        for nxt in adj[cur]:
            if nxt not in depth or (depth[nxt] < depth[cur] + 1 and depth[cur] + 1 <= len(scene_ids)):
                depth[nxt] = depth[cur] + 1
                queue.append(nxt)
    for idx, sid in enumerate(scene_ids):
        depth.setdefault(sid, idx)

    rows: dict[int, list[str]] = {}
    for sid in scene_ids:
        rows.setdefault(depth[sid], []).append(sid)

    center_x = DEFAULT_CARD_WIDTH // 2
    positions: dict[str, tuple[int, int]] = {}
    for row_idx, r_sids in sorted(rows.items()):
        total_w = len(r_sids) * DEFAULT_CARD_WIDTH + max(0, len(r_sids) - 1) * HORIZONTAL_GAP
        left_edge = center_x - total_w // 2
        for col_idx, sid in enumerate(r_sids):
            x = left_edge + col_idx * COLUMN_STEP
            y = row_idx * ROW_STEP
            positions[sid] = (x, y)

    header_title = extract_h1(read_text(text_file)) or chapter_dir.name
    header_width = 880
    header_x = center_x - header_width // 2
    nodes: list[dict] = [
        {
            "id": "chapter_header",
            "type": "text",
            "x": header_x,
            "y": -290,
            "width": header_width,
            "height": HEADER_HEIGHT,
            "color": "1",
            "text": "\n".join(
                [
                    f"# {header_title}",
                    build_header_jump_line(text_file, cg_file, index_file),
                    "- **跳转提示**：悬停各卡片顶部大号双链点击右侧跳转图标（或 `Ctrl+左键`），即可直接跳至《流程（文字）》对应场景小节。",
                ]
            ),
        }
    ]

    for s in scenes:
        sid = s["id"]
        x, y = positions[sid]
        jump_line = build_scene_jump_line(sid, s["title"], text_file, cg_file, sid in cg_scene_ids)
        summary = summarize_scene_body(s["body"])
        nodes.append(
            {
                "id": f"node_{sid}",
                "type": "text",
                "x": x,
                "y": y,
                "width": DEFAULT_CARD_WIDTH,
                "height": DEFAULT_CARD_HEIGHT,
                "color": "4",
                "text": f"# {s['title']}\n{jump_line}\n{summary}",
            }
        )

    canvas_edges: list[dict] = []
    if rows.get(0):
        for idx, sid in enumerate(rows[0], 1):
            canvas_edges.append(
                {
                    "id": f"edge_header_{idx}_{sid}",
                    "fromNode": "chapter_header",
                    "fromSide": "bottom",
                    "toNode": f"node_{sid}",
                    "toSide": "top",
                    "label": "开场" if idx == 1 and len(rows[0]) == 1 else "",
                }
            )

    pair_counts: dict[tuple[str, str], int] = {}
    for idx, (src, dst, label) in enumerate(edges, 1):
        count = pair_counts.get((src, dst), 0)
        pair_counts[(src, dst)] = count + 1
        from_side, to_side = ("left", "left") if count > 0 else ("bottom", "top")
        edge_obj = {
            "id": f"edge_route_{idx}_{src}_{dst}",
            "fromNode": f"node_{src}",
            "fromSide": from_side,
            "toNode": f"node_{dst}",
            "toSide": to_side,
        }
        if label:
            edge_obj["label"] = label
        canvas_edges.append(edge_obj)

    return {"nodes": nodes, "edges": canvas_edges}


def tidy_existing_canvas(data: dict, scene_nodes: dict[str, dict], detail_pairs: list[tuple[dict, dict]]) -> bool:
    changed = False
    for node in scene_nodes.values():
        if isinstance(node.get("width"), (int, float)) and node["width"] < DEFAULT_CARD_WIDTH:
            node["width"] = DEFAULT_CARD_WIDTH
            changed = True
        if isinstance(node.get("height"), (int, float)) and node["height"] < DEFAULT_CARD_HEIGHT:
            node["height"] = DEFAULT_CARD_HEIGHT
            changed = True

    all_nodes = [n for n in data.get("nodes", []) if isinstance(n, dict)]
    pair_ids = set()
    for main_node, detail_node in detail_pairs:
        pair_ids.add((main_node["id"], detail_node["id"]))
        if isinstance(detail_node.get("width"), (int, float)) and detail_node["width"] < DEFAULT_CARD_WIDTH:
            detail_node["width"] = DEFAULT_CARD_WIDTH
            changed = True
        if isinstance(detail_node.get("height"), (int, float)) and detail_node["height"] < DEFAULT_CARD_HEIGHT:
            detail_node["height"] = DEFAULT_CARD_HEIGHT
            changed = True
        right_edge = main_node["x"] + main_node["width"]
        if detail_node["x"] >= main_node["x"] and detail_node["x"] - right_edge < 160:
            candidate_x = right_edge + 160
            dy_top, dy_bot = detail_node["y"], detail_node["y"] + detail_node["height"]
            collision = any(
                other is not detail_node
                and other is not main_node
                and isinstance(other.get("x"), (int, float))
                and isinstance(other.get("width"), (int, float))
                and not (candidate_x + detail_node["width"] <= other["x"] or candidate_x >= other["x"] + other["width"])
                and not (dy_bot <= other.get("y", 0) or dy_top >= other.get("y", 0) + other.get("height", 0))
                for other in all_nodes
            )
            if not collision:
                detail_node["x"] = candidate_x
                changed = True

    for edge in data.get("edges", []):
        if (edge.get("fromNode"), edge.get("toNode")) in pair_ids and edge.get("label") == "区域与战术展开":
            edge["label"] = DETAIL_SHORT_LABEL
            changed = True
    return changed


def link_markdown_back_to_canvas(text: str, canvas_file: Path, scene_ids: list[str]) -> tuple[str, int]:
    """在章节《流程（文字）》或《主持CG》的各 S 小节补入回指白板的双链（如尚缺失）。"""
    canvas_target_names = {canvas_file.name, canvas_file.stem}

    def has_canvas_link(chunk: str) -> bool:
        for m in WIKI_LINK.finditer(chunk):
            name = m.group("target").split("#", 1)[0].strip()
            if name in canvas_target_names:
                return True
        for m in MARKDOWN_LINK.finditer(chunk):
            target = m.group("target").split("#", 1)[0].strip()
            if Path(target).name == canvas_file.name or canvas_file.stem in target:
                return True
        return False

    matches = list(re.finditer(r"(?m)^ {0,3}(#{2})[ \t]+(.+?)[ \t]*#*[ \t]*$", text))
    edits: list[tuple[int, int, str]] = []
    known = set(scene_ids)
    canvas_link = f"[[{canvas_file.stem}|流程（白板）]]"

    for idx, match in enumerate(matches):
        title = clean_heading(match.group(2))
        ent = ENTITY_HEADING.fullmatch(title)
        if not ent or ent.group(1) not in known:
            continue
        start = match.end()
        stop = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        section = text[start:stop]
        if has_canvas_link(section):
            continue

        nav_match = re.search(r"(?m)^(?P<line>(?:[-*]\s*)?\*\*(?:同行导航|导航)\*\*\s*[:：][^\n]*)$", section)
        if nav_match:
            line = nav_match.group("line")
            new_line = f"{line} ｜ {canvas_link}"
            new_section = section[: nav_match.start()] + new_line + section[nav_match.end() :]
        else:
            sep = re.search(r"(?m)^---\s*$", section)
            ins = f"\n**白板导航**：{canvas_link}\n"
            if sep:
                new_section = section[: sep.start()].rstrip() + "\n" + ins + "\n" + section[sep.start() :]
            else:
                new_section = section.rstrip() + "\n" + ins
        edits.append((start, stop, new_section))

    if not edits:
        return text, 0

    pieces, cursor = [], 0
    for start, stop, replacement in edits:
        pieces.append(text[cursor:start])
        pieces.append(replacement)
        cursor = stop
    pieces.append(text[cursor:])
    return "".join(pieces), len(edits)


def process_chapter(
    root: Path,
    chapter_dir: Path,
    create: bool,
    tidy_layout: bool,
) -> tuple[dict[Path, str], dict]:
    md_files = [p for p in chapter_dir.iterdir() if p.is_file()]
    texts = [p for p in md_files if p.name.endswith("流程（文字）.md")]
    cgs = [p for p in md_files if p.name.endswith("主持CG.md")]
    canvases = [p for p in md_files if p.name.endswith("流程（白板）.canvas")]

    if len(texts) != 1:
        raise ValueError(f"章节缺少唯一的《流程（文字）.md》：{chapter_dir.name}")
    text_file = texts[0]
    cg_file = cgs[0] if len(cgs) == 1 else None
    index_file = root / "模组总索引.md"

    text_content = read_text(text_file)
    scenes = extract_h2_scenes(text_content)
    if not scenes:
        raise ValueError(f"《{text_file.name}》未找到任何 Sxxx 场景标题。")
    scene_by_id = {s["id"]: s for s in scenes}
    scene_ids = [s["id"] for s in scenes]

    cg_content = read_text(cg_file) if cg_file else ""
    cg_scene_ids = {s["id"] for s in extract_h2_scenes(cg_content)} if cg_file else set()

    pending_writes: dict[Path, str] = {}
    summary = {"chapter": chapter_dir.name, "scenes": len(scenes), "canvas_action": "skipped", "linked_scenes": 0, "backlinks_added": 0}

    if canvases:
        canvas_file = canvases[0]
        raw_canvas = read_text(canvas_file)
        data = json.loads(raw_canvas)
        summary["canvas_action"] = "updated"
    elif create:
        canvas_name = text_file.name[:-len("流程（文字）.md")] + "流程（白板）.canvas"
        canvas_file = chapter_dir / canvas_name
        edges = parse_route_edges(text_content, scene_ids)
        data = create_canvas_from_flow(chapter_dir, text_file, cg_file, index_file, scenes, cg_scene_ids, edges)
        summary["canvas_action"] = "created"
    else:
        return {}, summary

    nodes_by_id = {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and n.get("id")}
    scene_nodes: dict[str, dict] = {}
    detail_pairs: list[tuple[dict, dict]] = []

    for node in data.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "text" or not isinstance(node.get("text"), str):
            continue
        lines = node["text"].splitlines()
        first = clean_heading(lines[0].lstrip("# ")) if lines else ""
        ent = ENTITY_HEADING.fullmatch(first)
        if ent and ent.group(1) in scene_by_id:
            sid = ent.group(1)
            scene_nodes[sid] = node
            title = scene_by_id[sid]["title"]
            new_text = update_scene_card_text(node["text"], sid, title, text_file, cg_file, sid in cg_scene_ids)
            if new_text != node["text"]:
                node["text"] = new_text
            summary["linked_scenes"] += 1
        elif node.get("id", "").endswith("_header"):
            new_text = update_header_card_text(node["text"], text_file, cg_file, index_file)
            if new_text != node["text"]:
                node["text"] = new_text

    edge_targets_by_src: dict[str, list[str]] = {}
    for edge in data.get("edges", []):
        if isinstance(edge, dict):
            edge_targets_by_src.setdefault(edge.get("fromNode", ""), []).append(edge.get("toNode", ""))

    scene_id_by_node_id = {node["id"]: sid for sid, node in scene_nodes.items()}
    matched_details: dict[str, str] = {}
    for sid, node in scene_nodes.items():
        for target_id in edge_targets_by_src.get(node["id"], []):
            target_node = nodes_by_id.get(target_id)
            if not target_node or target_node.get("type") != "text" or target_id in scene_id_by_node_id:
                continue
            first = target_node.get("text", "").splitlines()[0] if target_node.get("text") else ""
            if DETAIL_MARKER.search(clean_heading(first.lstrip("# "))) or target_id.endswith("_detail"):
                matched_details[target_id] = sid
                detail_pairs.append((node, target_node))

    for node in data.get("nodes", []):
        if node.get("type") != "text" or node.get("id") in scene_id_by_node_id:
            continue
        nid = node.get("id", "")
        sid = matched_details.get(nid)
        if not sid and isinstance(node.get("text"), str) and node["text"].splitlines():
            m = DETAIL_MARKER.search(clean_heading(node["text"].splitlines()[0].lstrip("# ")))
            if m:
                cand = m.group(1) or m.group(2)
                if cand in scene_by_id:
                    sid = cand
        if sid:
            title = scene_by_id[sid]["title"]
            new_text = update_detail_card_text(node["text"], sid, title, text_file, cg_file, sid in cg_scene_ids)
            if new_text != node["text"]:
                node["text"] = new_text

    if tidy_layout:
        tidy_existing_canvas(data, scene_nodes, detail_pairs)

    new_canvas_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if summary["canvas_action"] == "created" or new_canvas_text != read_text(canvas_file):
        pending_writes[canvas_file] = new_canvas_text

    new_text_flow, text_backlinks = link_markdown_back_to_canvas(text_content, canvas_file, scene_ids)
    if text_backlinks:
        pending_writes[text_file] = new_text_flow
        summary["backlinks_added"] += text_backlinks

    if cg_file:
        new_cg_flow, cg_backlinks = link_markdown_back_to_canvas(cg_content, canvas_file, sorted(cg_scene_ids))
        if cg_backlinks:
            pending_writes[cg_file] = new_cg_flow
            summary["backlinks_added"] += cg_backlinks

    return pending_writes, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="将各章白板（.canvas）与《流程（文字）》双向互链，或按文字路线新建防遮挡白板。")
    parser.add_argument("root", type=Path, help="模组根目录")
    parser.add_argument("--chapter", default="", help="只处理 带团文档 下指定的章节文件夹")
    parser.add_argument("--create", action="store_true", help="章节尚无白板时，根据《流程（文字）》自动生成 .canvas")
    parser.add_argument("--tidy-layout", action="store_true", help="整理过窄卡片尺寸、左右展开卡间距与过长固定边标签")
    parser.add_argument("--write", action="store_true", help="真正落盘（落盘前先备份至 模组/.backup/）；默认仅预演")
    parser.add_argument("--json", action="store_true", help="输出 JSON 到终端")
    args = parser.parse_args()

    root = args.root.resolve()
    chapters_root = root / "带团文档"
    if not root.is_dir() or not chapters_root.is_dir():
        msg = "模组根目录或 带团文档 不存在。"
        if args.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2))
        else:
            print("[input] " + msg)
        return 2

    if args.chapter:
        target_dir = (chapters_root / args.chapter).resolve()
        if Path(args.chapter).name != args.chapter or target_dir.parent != chapters_root or not target_dir.is_dir():
            msg = "--chapter 须填写 带团文档 下已有的章节文件夹名。"
            if args.json:
                print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2))
            else:
                print("[input] " + msg)
            return 2
        chapter_dirs = [target_dir]
    else:
        chapter_dirs = sorted(p for p in chapters_root.iterdir() if p.is_dir())

    if not chapter_dirs:
        msg = "带团文档 下未找到任何章节文件夹。"
        if args.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2))
        else:
            print("[input] " + msg)
        return 2

    all_writes: dict[Path, str] = {}
    summaries: list[dict] = []
    try:
        for ch_dir in chapter_dirs:
            writes, summary = process_chapter(root, ch_dir, args.create, args.tidy_layout)
            all_writes.update(writes)
            summaries.append(summary)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print("[error] " + str(exc))
        return 2

    changed_rel = [p.relative_to(root).as_posix() for p in sorted(all_writes)]
    if args.write and all_writes:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_dir = root / "模组" / ".backup" / stamp
        for target in sorted(all_writes):
            if target.is_file():
                backup = backup_dir / target.relative_to(root)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            target.write_text(all_writes[target], encoding="utf-8")

    payload = {
        "ok": True,
        "written": bool(args.write and all_writes),
        "changed_files": changed_rel,
        "chapters": summaries,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        action = "已落盘" if args.write else "预演（加 --write 落盘）"
        print(f"{action}：共 {len(changed_rel)} 个待更新文件。")
        for rel in changed_rel:
            print(f"  {rel}")
        for item in summaries:
            print(f"  - {item['chapter']}：白板={item['canvas_action']}，场景链接={item['linked_scenes']}，回链补齐={item['backlinks_added']}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
