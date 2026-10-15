from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from starlette.middleware.wsgi import WSGIMiddleware

from admin import app as admin_app

ROOT = Path(__file__).resolve().parent
STICKER_DIR = ROOT / "stickers"
METADATA_PATH = ROOT / "metadata.json"

SYNONYMS = {
    "无语": ["没招", "雷人", "听不懂", "问号", "没办法", "流汗"],
    "生气": ["可恶", "恼火", "不爽", "奶凶"],
    "害怕": ["好可怕", "惊恐", "吓到", "弱小无助"],
    "困": ["睡觉", "水饺", "晚安", "犯困"],
    "想念": ["想你", "黏人", "撒娇"],
    "想你": ["想念", "黏人", "撒娇"],
    "震惊": ["惊呆", "雷人", "不可思议"],
    "疑惑": ["问号", "困惑", "看不懂", "没懂"],
    "不懂": ["听不懂", "问号", "困惑"],
    "摆烂": ["无力", "没招", "懒", "疲惫"],
    "累": ["无力", "疲惫", "没劲"],
    "贴贴": ["抱抱", "天下第一好", "陪伴", "靠近"],
    "夸我": ["帅", "自恋", "臭美"],
    "查岗": ["在干嘛", "报备", "控制欲", "呼吸也要说"],
    "工作": ["电脑", "上班", "加班", "打字"],
}


def _load() -> list[dict[str, Any]]:
    with METADATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _by_id() -> dict[str, dict[str, Any]]:
    return {str(x["id"]).zfill(3): x for x in _load()}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _expanded_terms(query: str) -> set[str]:
    q = _norm(query)
    terms = {q}
    for n in (2, 3, 4):
        for i in range(max(0, len(q) - n + 1)):
            terms.add(q[i:i + n])
    for key, values in SYNONYMS.items():
        if key in q or any(v in q for v in values):
            terms.add(key)
            terms.update(values)
    return {t for t in terms if t}


def _score(item: dict[str, Any], query: str) -> float:
    q = _norm(query)
    terms = _expanded_terms(query)
    caption = _norm(item.get("caption", ""))
    tags = [_norm(x) for x in item.get("tags", [])]
    tone = _norm(item.get("tone", ""))
    haystack = "|".join([caption, *tags, tone])
    score = 12.0 if q and q in haystack else 0.0
    for term in terms:
        if len(term) < 2:
            continue
        if term in caption:
            score += 5.0
        if any(term == tag for tag in tags):
            score += 4.5
        elif any(term in tag or tag in term for tag in tags):
            score += 2.5
        if term in tone:
            score += 1.2
    return score


def _rank(query: str, limit: int = 5) -> list[dict[str, Any]]:
    stickers = _load()
    by_id = {str(x["id"]).zfill(3): x for x in stickers}
    limit = max(1, min(int(limit), 10))
    ranked = sorted(((x, _score(x, query)) for x in stickers), key=lambda p: (p[1], p[0]["id"]), reverse=True)
    positives = [x for x, score in ranked if score > 0]
    if not positives:
        fallback = ["015", "024", "011", "026", "004", "007"]
        positives = [by_id[x] for x in fallback if x in by_id]
    return positives[:limit]


def _text_summary(item: dict[str, Any]) -> str:
    return f"表情 {item['id']}：{item.get('caption','')}\n语气：{item.get('tone','')}\n标签：{'、'.join(item.get('tags', []))}"


def _image_content(item: dict[str, Any]) -> ImageContent:
    path = STICKER_DIR / item["file"]
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return ImageContent(type="image", data=data, mimeType=mime)


mcp = FastMCP(
    "Salt Stickers",
    instructions=(
        "这是用户的私人抽象表情包库。正常聊天中，当一张表情比纯文字更贴合语气时可以调用。"
        "优先根据上下文中的情绪、关系语气和梗感选择，不要机械按关键词。"
        "表情可用于吐槽、阴阳怪气、撒娇、无语、摆烂、贴贴等轻松场景。"
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
)


@mcp.tool()
def search_stickers(intent: str, limit: int = 5) -> str:
    """按自然语言语气/场景搜索表情包。"""
    payload = [{"id": x["id"], "caption": x.get("caption", ""), "tone": x.get("tone", ""), "tags": x.get("tags", [])} for x in _rank(intent, limit)]
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def pick_sticker(intent: str) -> list[TextContent | ImageContent]:
    """根据自然语言语气直接挑一张最合适的表情并返回图片。"""
    item = _rank(intent, 1)[0]
    return [TextContent(type="text", text=_text_summary(item)), _image_content(item)]


@mcp.tool()
def send_sticker(sticker_id: str) -> list[TextContent | ImageContent]:
    """按三位 ID 返回指定表情图片，例如 015、020、027。"""
    sid = str(sticker_id).strip().zfill(3)
    by_id = _by_id()
    item = by_id.get(sid)
    if not item:
        return [TextContent(type="text", text=f"没有表情 {sid}。有效 ID：{', '.join(sorted(by_id))}")]
    return [TextContent(type="text", text=_text_summary(item)), _image_content(item)]


# One public Render port serves BOTH:
#   /mcp        -> Streamable HTTP MCP
#   /admin      -> sticker manager
#   /stickers/* -> admin previews
starlette_app = mcp.streamable_http_app()
starlette_app.mount("/", WSGIMiddleware(admin_app))

if __name__ == "__main__":
    uvicorn.run(starlette_app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
