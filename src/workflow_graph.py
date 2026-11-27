"""Pure workflow graph validation helpers (zero preset/export dependencies)."""
from __future__ import annotations

import math

NODES = {"source": "音乐输入", "separation": "分离声部", "midi": "生成 MIDI", "subtitles": "导出字幕"}
LINKS = {("source", "separation"), ("separation", "midi"), ("midi", "subtitles")}


def default_graph(subtitles=False):
    names = ["source", "separation", "midi"] + (["subtitles"] if subtitles else [])
    return {
        "nodes": {name: [60 + index * 235, 150] for index, name in enumerate(names)},
        "edges": [list(pair) for pair in zip(names, names[1:])],
    }


def validate_graph(graph):
    if not isinstance(graph, dict) or set(graph) != {"nodes", "edges"}:
        raise ValueError("工作流格式无效。")
    nodes, edges = graph["nodes"], graph["edges"]
    if not isinstance(nodes, dict) or not nodes or set(nodes) - set(NODES):
        raise ValueError("工作流包含未知节点。")
    for position in nodes.values():
        if (
            not isinstance(position, list)
            or len(position) != 2
            or any(not isinstance(v, (float, int)) or not math.isfinite(v) or abs(v) > 10000 for v in position)
        ):
            raise ValueError("节点坐标无效。")
    if not isinstance(edges, list) or any(
        not isinstance(e, list) or len(e) != 2 or any(not isinstance(n, str) for n in e) for e in edges
    ):
        raise ValueError("工作流连接无效。")
    pairs = [tuple(e) for e in edges]
    if any(p not in LINKS or any(n not in nodes for n in p) for p in pairs) or len(set(pairs)) != len(pairs):
        raise ValueError("连接顺序应为：音乐输入 → 分离声部 → MIDI → 字幕。")
    names = ["source", "separation", "midi", "subtitles"][: len(nodes)]
    if set(nodes) != set(names) or set(pairs) != set(zip(names, names[1:])) or len(nodes) < 2:
        raise ValueError("请连接音乐输入和分离节点；后续节点需接入同一条流程。")
    return "subtitles" if "subtitles" in nodes else "midi" if "midi" in nodes else "separation"
