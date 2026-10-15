import os, json, base64, mimetypes
from urllib.parse import quote
import requests
from flask import Flask, request, redirect, url_for, render_template_string, flash, send_from_directory

app = Flask(__name__)
app.secret_key = os.environ.get("ADMIN_SECRET", "change-me")

ROOT = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(ROOT, "metadata.json")
STICKERS = os.path.join(ROOT, "stickers")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # owner/repo
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

def load_meta():
    with open(META, "r", encoding="utf-8") as f:
        return json.load(f)

def sticker_list(meta):
    if isinstance(meta, list):
        return meta
    if isinstance(meta, dict) and isinstance(meta.get("stickers"), list):
        return meta["stickers"]
    return [v for v in meta.values() if isinstance(v, dict)]

def get_filename(item):
    for k in ("filename","file","path"):
        if item.get(k):
            return os.path.basename(str(item[k]))
    return ""

def authed():
    if not ADMIN_PASSWORD:
        return True
    return request.cookies.get("salt_admin") == ADMIN_PASSWORD

def gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gh_get(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{quote(path)}"
    r = requests.get(url, headers=gh_headers(), params={"ref": GITHUB_BRANCH}, timeout=30)
    return r

def gh_put(path, content_bytes, message, sha=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{quote(path)}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    return requests.put(url, headers=gh_headers(), json=payload, timeout=30)

def gh_delete(path, message):
    r = gh_get(path)
    if r.status_code != 200:
        return r
    sha = r.json()["sha"]
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{quote(path)}"
    payload = {"message": message, "sha": sha, "branch": GITHUB_BRANCH}
    return requests.delete(url, headers=gh_headers(), json=payload, timeout=30)

def save_meta_to_github(meta, message):
    raw = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if not (GITHUB_TOKEN and GITHUB_REPO):
        with open(META, "wb") as f:
            f.write(raw)
        return True, "已保存到当前运行实例（未配置 GitHub，同步可能在重启后丢失）"
    r = gh_get("metadata.json")
    sha = r.json().get("sha") if r.status_code == 200 else None
    w = gh_put("metadata.json", raw, message, sha=sha)
    if w.status_code in (200,201):
        with open(META, "wb") as f:
            f.write(raw)
        return True, "已提交到 GitHub"
    return False, f"GitHub 保存失败：{w.status_code} {w.text[:200]}"

LOGIN_HTML = """
<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Salt Stickers Admin</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:480px;margin:60px auto;padding:20px}input,button{font-size:16px;padding:12px;width:100%;box-sizing:border-box;margin:8px 0}</style>
<h2>Salt Stickers Admin</h2>
<form method=post><input type=password name=password placeholder="管理员密码"><button>进入</button></form>
"""

PAGE_HTML = """
<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Salt Stickers Admin</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;margin:20px;background:#fafafa;color:#222}
.wrap{max-width:980px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{background:white;border:1px solid #ddd;border-radius:14px;padding:12px}.card img{width:100%;height:180px;object-fit:contain;background:#f4f4f4;border-radius:10px}
input,textarea,button{font:inherit;padding:8px;box-sizing:border-box}input,textarea{width:100%;margin:5px 0}textarea{min-height:70px}
.row{display:flex;gap:8px}.row>*{flex:1}button{border:0;border-radius:8px;background:#111;color:#fff}.danger{background:#8b1e1e}
.add{background:white;border:1px solid #ddd;border-radius:14px;padding:14px;margin-bottom:18px}.msg{background:#fff4c7;padding:10px;border-radius:8px;margin:10px 0}
small{color:#666}
</style>
<div class=wrap>
<h1>Salt Stickers</h1>
{% with messages = get_flashed_messages() %}{% if messages %}{% for m in messages %}<div class=msg>{{m}}</div>{% endfor %}{% endif %}{% endwith %}
<div class=add>
<h3>新增表情包</h3>
<form method=post action="{{url_for('add')}}" enctype=multipart/form-data>
<input type=file name=image accept="image/*" required>
<div class=row><input name=id placeholder="ID，例如 029"><input name=title placeholder="标题"></div>
<textarea name=description placeholder="备注 / 适用语境"></textarea>
<input name=tags placeholder="标签，用逗号分隔">
<button>上传并提交</button>
</form>
</div>
<div class=grid>
{% for s in stickers %}
<div class=card>
<img src="{{url_for('sticker_file', filename=s._filename)}}">
<form method=post action="{{url_for('edit', sid=s._sid)}}">
<small>ID: {{s._sid}} · {{s._filename}}</small>
<input name=title value="{{s._title}}">
<textarea name=description>{{s._description}}</textarea>
<input name=tags value="{{s._tags}}">
<div class=row><button>保存修改</button></form>
<form method=post action="{{url_for('delete', sid=s._sid)}}" onsubmit="return confirm('确定删除？')"><button class=danger>删除</button></form></div>
</div>
{% endfor %}
</div>
</div>
"""

@app.route("/admin/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        if request.form.get("password","") == ADMIN_PASSWORD:
            resp = redirect(url_for("admin"))
            resp.set_cookie("salt_admin", ADMIN_PASSWORD, httponly=True, samesite="Lax")
            return resp
        flash("密码不对")
    return render_template_string(LOGIN_HTML)

@app.route("/admin")
def admin():
    if not authed():
        return redirect(url_for("login"))
    meta = load_meta()
    out = []
    for item in sticker_list(meta):
        sid = str(item.get("id") or item.get("number") or item.get("index") or "")
        fn = get_filename(item)
        title = item.get("title") or item.get("name") or item.get("label") or ""
        desc = item.get("description") or item.get("desc") or item.get("note") or item.get("caption") or ""
        tags = item.get("tags") or item.get("keywords") or item.get("semantic_tags") or []
        if isinstance(tags, list): tags = ", ".join(map(str,tags))
        out.append({"_sid":sid,"_filename":fn,"_title":title,"_description":desc,"_tags":tags})
    return render_template_string(PAGE_HTML, stickers=out)

@app.route("/stickers/<path:filename>")
def sticker_file(filename):
    return send_from_directory(STICKERS, filename)

def find_item(meta, sid):
    for item in sticker_list(meta):
        vals = [str(item.get(k,"")) for k in ("id","number","index")]
        if sid in vals:
            return item
    return None

@app.post("/admin/edit/<sid>")
def edit(sid):
    if not authed(): return redirect(url_for("login"))
    meta = load_meta()
    item = find_item(meta, sid)
    if not item:
        flash("没找到这张"); return redirect(url_for("admin"))
    # preserve schema
    for k in ("title","name","label"):
        if k in item: item[k] = request.form.get("title",""); break
    else: item["title"] = request.form.get("title","")
    for k in ("description","desc","note","caption"):
        if k in item: item[k] = request.form.get("description",""); break
    else: item["description"] = request.form.get("description","")
    tags = [x.strip() for x in request.form.get("tags","").replace("，",",").split(",") if x.strip()]
    for k in ("tags","keywords","semantic_tags"):
        if k in item: item[k] = tags; break
    else: item["tags"] = tags
    ok,msg=save_meta_to_github(meta, f"Update sticker {sid}")
    flash(msg); return redirect(url_for("admin"))

@app.post("/admin/add")
def add():
    if not authed(): return redirect(url_for("login"))
    f = request.files.get("image")
    sid = request.form.get("id","").strip()
    if not f or not sid:
        flash("图片和 ID 都要填"); return redirect(url_for("admin"))
    ext = os.path.splitext(f.filename or "")[1].lower() or ".jpg"
    filename = f"{sid}{ext}"
    raw = f.read()
    meta = load_meta()
    items = sticker_list(meta)
    if find_item(meta, sid):
        flash("这个 ID 已存在"); return redirect(url_for("admin"))
    sample = items[0] if items else {}
    item = {
        "id": sid,
        "filename": filename,
        "title": request.form.get("title","").strip(),
        "description": request.form.get("description","").strip(),
        "tags": [x.strip() for x in request.form.get("tags","").replace("，",",").split(",") if x.strip()]
    }
    items.append(item)
    os.makedirs(STICKERS, exist_ok=True)
    with open(os.path.join(STICKERS, filename), "wb") as g: g.write(raw)

    if GITHUB_TOKEN and GITHUB_REPO:
        r = gh_put(f"stickers/{filename}", raw, f"Add sticker {sid}")
        if r.status_code not in (200,201):
            flash(f"图片上传 GitHub 失败：{r.status_code}"); return redirect(url_for("admin"))
    ok,msg=save_meta_to_github(meta, f"Register sticker {sid}")
    flash(msg); return redirect(url_for("admin"))

@app.post("/admin/delete/<sid>")
def delete(sid):
    if not authed(): return redirect(url_for("login"))
    meta = load_meta()
    items = sticker_list(meta)
    item = find_item(meta, sid)
    if not item:
        flash("没找到这张"); return redirect(url_for("admin"))
    fn = get_filename(item)
    items.remove(item)
    if GITHUB_TOKEN and GITHUB_REPO and fn:
        r = gh_delete(f"stickers/{fn}", f"Delete sticker {sid}")
        if r.status_code not in (200,204):
            flash(f"删除 GitHub 图片失败：{r.status_code}"); return redirect(url_for("admin"))
    local = os.path.join(STICKERS, fn)
    if fn and os.path.exists(local):
        os.remove(local)
    ok,msg=save_meta_to_github(meta, f"Remove sticker {sid}")
    flash(msg); return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8001")))
