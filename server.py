from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.wsgi import WSGIMiddleware

from admin import app as admin_app

ROOT = Path(__file__).resolve().parent
STICKER_DIR = ROOT / "stickers"
METADATA_PATH = ROOT / "metadata.json"

GITHUB_REPO = os.environ.get("GITHUB_REPO", "saltccc/meme")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
STICKER_CDN_BASE = os.environ.get(
    "STICKER_CDN_BASE",
    f"https://cdn.jsdelivr.net/gh/{GITHUB_REPO}@{GITHUB_BRANCH}/stickers",
).rstrip("/")

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

def _sticker_url(item: dict[str, Any]) -> str:
    return f"{STICKER_CDN_BASE}/{item['file']}"

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

# --- Minimal MCP App UI experiment ---
# One fixed sticker first. Do not add dynamic selection until this renders.
APP_MIME = "text/html;profile=mcp-app"
WIDGET_URI = "ui://salt-stickers/sticker-v1.html"

@mcp.resource(WIDGET_URI, mime_type=APP_MIME)
def sticker_widget() -> str:
    # FastMCP's resource decorator builds the MCP contents envelope itself.
    # Returning an envelope here double-wraps it and makes ChatGPT display JSON
    # as text instead of mounting the HTML widget.
    return (ROOT / "sticker-widget.html").read_text(encoding="utf-8")

@mcp.tool(
    name="show_test_sticker",
    description="Use this only to test whether the fixed sticker UI renders in ChatGPT.",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    meta={
        "ui": {"resourceUri": WIDGET_URI},
        "openai/outputTemplate": WIDGET_URI,
        "openai/visibility": ["model", "app"],
    },
)
def show_test_sticker() -> dict[str, Any]:
    """固定显示 015，仅用于确认 MCP App UI 图片链路。"""
    item = _by_id()["015"]
    return {
        "structuredContent": {"imageUrl": _sticker_url(item)},
        "content": [{"type": "text", "text": "Displaying the fixed test sticker."}],
    }

@mcp.tool()
def search_stickers(intent: str, limit: int = 5) -> str:
    """按自然语言语气/场景搜索表情包。"""
    payload = [{"id": x["id"], "caption": x.get("caption", ""), "tone": x.get("tone", ""), "tags": x.get("tags", [])} for x in _rank(intent, limit)]
    return json.dumps(payload, ensure_ascii=False, indent=2)

@mcp.tool()
def pick_sticker(intent: str) -> str:
    """根据自然语言语气挑一张最合适的表情，并返回公开图片 URL。"""
    item = _rank(intent, 1)[0]
    return f"{_text_summary(item)}\n图片URL：{_sticker_url(item)}"

@mcp.tool()
def send_sticker(sticker_id: str) -> str:
    """按三位 ID 返回指定表情的公开图片 URL，例如 015、020、027。"""
    sid = str(sticker_id).strip().zfill(3)
    by_id = _by_id()
    item = by_id.get(sid)
    if not item:
        return f"没有表情 {sid}。有效 ID：{', '.join(sorted(by_id))}"
    return f"{_text_summary(item)}\n图片URL：{_sticker_url(item)}"

starlette_app = mcp.streamable_http_app()
starlette_app.mount("/", WSGIMiddleware(admin_app))

if __name__ == "__main__":
    uvicorn.run(starlette_app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
