from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Flask, flash, redirect, render_template_string, request, send_from_directory, session, url_for

ROOT = Path(__file__).resolve().parent
META = ROOT / "metadata.json"
STICKERS_DIR = ROOT / "stickers"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()  # owner/repo
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip() or "main"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "").strip() or ADMIN_PASSWORD or "salt-stickers-local-dev"

app = Flask(__name__)
app.secret_key = ADMIN_SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


def load_meta() -> list[dict]:
    with META.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("metadata.json 必须是数组")
    return data


def save_local(meta: list[dict]) -> bytes:
    raw = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    META.write_bytes(raw)
    return raw


def authed() -> bool:
    return (not ADMIN_PASSWORD) or bool(session.get("ok"))


def gh_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def gh_url(path: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{quote(path, safe='/')}"


def gh_get(path: str):
    return requests.get(gh_url(path), headers=gh_headers(), params={"ref": GITHUB_BRANCH}, timeout=30)


def gh_put(path: str, content: bytes, message: str, sha: str | None = None):
    payload = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    return requests.put(gh_url(path), headers=gh_headers(), json=payload, timeout=30)


def gh_delete(path: str, message: str):
    existing = gh_get(path)
    if existing.status_code != 200:
        return existing
    payload = {
        "message": message,
        "sha": existing.json()["sha"],
        "branch": GITHUB_BRANCH,
    }
    return requests.delete(gh_url(path), headers=gh_headers(), json=payload, timeout=30)


def sync_metadata(meta: list[dict], message: str) -> tuple[bool, str]:
    raw = save_local(meta)
    if not (GITHUB_TOKEN and GITHUB_REPO):
        return True, "已保存到当前实例；尚未配置 GitHub 同步，重新部署后可能恢复旧版本。"

    current = gh_get("metadata.json")
    sha = current.json().get("sha") if current.status_code == 200 else None
    result = gh_put("metadata.json", raw, message, sha)
    if result.status_code in (200, 201):
        return True, "已保存，并同步提交到 GitHub。"
    return False, f"本地已保存，但 GitHub 同步失败：HTTP {result.status_code}"


def find_item(meta: list[dict], sid: str) -> dict | None:
    sid = str(sid).strip().zfill(3)
    return next((x for x in meta if str(x.get("id", "")).zfill(3) == sid), None)


LOGIN_HTML = """
<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Salt Stickers Admin</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:480px;margin:60px auto;padding:20px}input,button{font-size:16px;padding:12px;width:100%;box-sizing:border-box;margin:8px 0}button{background:#111;color:white;border:0;border-radius:10px}</style>
<h2>Salt Stickers Admin</h2>
<form method=post><input type=password name=password placeholder="管理员密码"><button>进入</button></form>
"""

PAGE_HTML = """
<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Salt Stickers Admin</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;margin:18px;background:#f7f7f7;color:#222}.wrap{max-width:980px;margin:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}.card,.add{background:#fff;border:1px solid #ddd;border-radius:14px;padding:12px}
.card img{width:100%;height:180px;object-fit:contain;background:#f2f2f2;border-radius:10px}.add{margin-bottom:18px}
input,textarea,button{font:inherit;padding:9px;box-sizing:border-box}input,textarea{width:100%;margin:5px 0}textarea{min-height:72px}.row{display:flex;gap:8px}.row>*{flex:1}
button{border:0;border-radius:9px;background:#111;color:#fff}.danger{background:#8b1e1e}.msg{background:#fff1bd;padding:10px;border-radius:8px;margin:10px 0}small{color:#666}
</style><div class=wrap><h1>Salt Stickers</h1>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=msg>{{m}}</div>{% endfor %}{% endwith %}
<div class=add><h3>新增表情包</h3><form method=post action="{{url_for('add')}}" enctype=multipart/form-data>
<input type=file name=image accept="image/*" required><div class=row><input name=id placeholder="ID，例如 029" required><input name=caption placeholder="备注 / 这张图是什么意思"></div>
<input name=tone placeholder="语气，例如：无语但不凶"><input name=tags placeholder="标签，用逗号分隔"><button>上传并提交</button></form></div>
<div class=grid>{% for s in stickers %}<div class=card><img src="{{url_for('sticker_file', filename=s.file)}}">
<small>ID: {{s.id}} · {{s.file}}</small><form method=post action="{{url_for('edit', sid=s.id)}}"><textarea name=caption>{{s.caption}}</textarea><input name=tone value="{{s.tone}}"><input name=tags value="{{s.tags|join(', ')}}"><button>保存修改</button></form>
<form method=post action="{{url_for('delete', sid=s.id)}}" onsubmit="return confirm('确定删除这张表情？')"><button class=danger>删除</button></form></div>{% endfor %}</div></div>
"""


@app.get("/")
def home():
    return redirect(url_for("admin"))


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password", "") == ADMIN_PASSWORD:
            session["ok"] = True
            return redirect(url_for("admin"))
        flash("密码不对。")
    return render_template_string(LOGIN_HTML)


@app.get("/admin")
def admin():
    if not authed():
        return redirect(url_for("login"))
    return render_template_string(PAGE_HTML, stickers=load_meta())


@app.get("/stickers/<path:filename>")
def sticker_file(filename: str):
    return send_from_directory(STICKERS_DIR, filename)


@app.post("/admin/edit/<sid>")
def edit(sid: str):
    if not authed():
        return redirect(url_for("login"))
    meta = load_meta()
    item = find_item(meta, sid)
    if not item:
        flash("没找到这张表情。")
        return redirect(url_for("admin"))
    item["caption"] = request.form.get("caption", "").strip()
    item["tone"] = request.form.get("tone", "").strip()
    item["tags"] = [x.strip() for x in request.form.get("tags", "").replace("，", ",").split(",") if x.strip()]
    _, msg = sync_metadata(meta, f"Update sticker {item['id']}")
    flash(msg)
    return redirect(url_for("admin"))


@app.post("/admin/add")
def add():
    if not authed():
        return redirect(url_for("login"))
    upload = request.files.get("image")
    sid = request.form.get("id", "").strip().zfill(3)
    if not upload or not sid.isdigit():
        flash("图片和数字 ID 都要填。")
        return redirect(url_for("admin"))

    meta = load_meta()
    if find_item(meta, sid):
        flash("这个 ID 已经存在。")
        return redirect(url_for("admin"))

    ext = Path(upload.filename or "").suffix.lower() or ".jpg"
    filename = f"{sid}{ext}"
    raw = upload.read()

    if GITHUB_TOKEN and GITHUB_REPO:
        result = gh_put(f"stickers/{filename}", raw, f"Add sticker {sid}")
        if result.status_code not in (200, 201):
            flash(f"图片提交到 GitHub 失败：HTTP {result.status_code}")
            return redirect(url_for("admin"))

    STICKERS_DIR.mkdir(exist_ok=True)
    (STICKERS_DIR / filename).write_bytes(raw)
    meta.append({
        "id": sid,
        "file": filename,
        "caption": request.form.get("caption", "").strip() or f"表情 {sid}",
        "tone": request.form.get("tone", "").strip(),
        "tags": [x.strip() for x in request.form.get("tags", "").replace("，", ",").split(",") if x.strip()],
    })
    meta.sort(key=lambda x: str(x.get("id", "")))
    _, msg = sync_metadata(meta, f"Register sticker {sid}")
    flash(msg)
    return redirect(url_for("admin"))


@app.post("/admin/delete/<sid>")
def delete(sid: str):
    if not authed():
        return redirect(url_for("login"))
    meta = load_meta()
    item = find_item(meta, sid)
    if not item:
        flash("没找到这张表情。")
        return redirect(url_for("admin"))

    filename = item.get("file", "")
    if GITHUB_TOKEN and GITHUB_REPO and filename:
        result = gh_delete(f"stickers/{filename}", f"Delete sticker {item['id']}")
        if result.status_code not in (200, 204):
            flash(f"GitHub 图片删除失败：HTTP {result.status_code}")
            return redirect(url_for("admin"))

    meta.remove(item)
    local = STICKERS_DIR / filename
    if filename and local.exists():
        local.unlink()
    _, msg = sync_metadata(meta, f"Remove sticker {item['id']}")
    flash(msg)
    return redirect(url_for("admin"))
