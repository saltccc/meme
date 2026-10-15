import os, json, base64, re
from urllib.parse import quote
import requests
from flask import Flask, request, redirect, url_for, render_template_string, flash, send_from_directory

app = Flask(__name__)
app.secret_key = os.environ.get("ADMIN_SECRET", "change-me")

ROOT = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(ROOT, "metadata.json")
STICKERS = os.path.join(ROOT, "stickers")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "saltccc/meme")
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

def get_id(item):
    return str(item.get("id") or item.get("number") or item.get("index") or "").zfill(3)

def get_filename(item):
    for k in ("file","filename","path"):
        if item.get(k):
            return os.path.basename(str(item[k]))
    return ""

def get_description(item):
    # Prefer the richer semantic description. If absent, fall back gracefully.
    return str(item.get("tone") or item.get("description") or item.get("desc") or item.get("note") or item.get("caption") or "")

def get_tags(item):
    v = item.get("tags") or item.get("keywords") or item.get("semantic_tags") or []
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)

def set_description(item, value):
    # Keep compatibility with the existing schema while only exposing one description field in UI.
    if "tone" in item:
        item["tone"] = value
    elif "description" in item:
        item["description"] = value
    elif "desc" in item:
        item["desc"] = value
    elif "note" in item:
        item["note"] = value
    else:
        item["tone"] = value
    # caption/title is not exposed anymore; leave existing value intact.

def set_tags(item, raw):
    values = [x.strip() for x in re.split(r"[,，]", raw) if x.strip()]
    for k in ("tags","keywords","semantic_tags"):
        if k in item:
            item[k] = values
            return
    item["tags"] = values

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
    return requests.get(url, headers=gh_headers(), params={"ref": GITHUB_BRANCH}, timeout=30)

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
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{quote(path)}"
    return requests.delete(url, headers=gh_headers(), json={
        "message": message, "sha": r.json()["sha"], "branch": GITHUB_BRANCH
    }, timeout=30)

def save_meta(meta, message):
    raw = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if GITHUB_TOKEN and GITHUB_REPO:
        r = gh_get("metadata.json")
        sha = r.json().get("sha") if r.status_code == 200 else None
        w = gh_put("metadata.json", raw, message, sha)
        if w.status_code not in (200, 201):
            return False, f"GitHub 保存失败：{w.status_code}"
    with open(META, "wb") as f:
        f.write(raw)
    return True, "已保存" + ("并同步到 GitHub" if GITHUB_TOKEN else "（当前实例）")

LOGIN = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font-family:-apple-system,sans-serif;max-width:420px;margin:60px auto;padding:20px}input,button{font:inherit;width:100%;box-sizing:border-box;padding:12px;margin:7px 0}</style>
<h2>Salt Stickers</h2><form method=post><input type=password name=password placeholder="管理员密码"><button>进入</button></form>"""

PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Salt Stickers</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;margin:18px;background:#fafafa;color:#222}
.wrap{max-width:1000px;margin:auto}.savebar{position:sticky;top:10px;z-index:20;display:flex;justify-content:flex-end;margin:0 0 14px;pointer-events:none}.savebar button{pointer-events:auto;padding:11px 20px;box-shadow:0 4px 18px #0002}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card,.add{background:#fff;border:1px solid #ddd;border-radius:14px;padding:12px}.add{margin-bottom:18px}
.card img{width:100%;height:180px;object-fit:contain;background:#f4f4f4;border-radius:10px}
input,textarea,button{font:inherit;box-sizing:border-box}input,textarea{width:100%;padding:9px;margin:5px 0}textarea{min-height:88px}
.row{display:flex;gap:8px}.row>*{flex:1}button{padding:9px;border:0;border-radius:8px;background:#111;color:#fff}.danger{background:#8b1e1e}
small{color:#666}.msg{padding:10px;background:#fff4c7;border-radius:8px;margin:8px 0}
label{font-size:13px;color:#555;display:block;margin-top:7px}
</style><div class=wrap>
<h1>Salt Stickers</h1>
{% with ms=get_flashed_messages() %}{% for m in ms %}<div class=msg>{{m}}</div>{% endfor %}{% endwith %}
<div class=add><h3>批量新增表情包</h3><form method=post action="{{url_for('add')}}" enctype=multipart/form-data>
<input type=file name=images accept="image/*" multiple required>
<small>可以一次选很多张。系统会自动接着现有编号分配 ID，并标记为“待小机识别”。</small>
<button>批量上传</button></form></div>
<form method=post action="{{url_for('save_all')}}" id="all-stickers"><div class=savebar><button type=submit>保存修改</button></div><div class=grid>{% for s in stickers %}<div class=card>
<img src="{{url_for('sticker_file',filename=s.fn)}}"><small>ID {{s.id}} · {{s.fn}}</small>
<input type=hidden name="sid" value="{{s.id}}">
<label>描述内容</label><textarea name="description_{{s.id}}">{{s.description}}</textarea>
<label>标签</label><input name="tags_{{s.id}}" value="{{s.tags}}">
<button class=danger type=submit formaction="{{url_for('delete',sid=s.id)}}" formmethod=post onclick="return confirm('确定删除？')">删除</button>
</div>{% endfor %}</div></form></div>"""

@app.route("/admin/login", methods=["GET","POST"])
def login():
    if request.method == "POST" and request.form.get("password","") == ADMIN_PASSWORD:
        resp = redirect(url_for("admin"))
        resp.set_cookie("salt_admin", ADMIN_PASSWORD, httponly=True, samesite="Lax")
        return resp
    return render_template_string(LOGIN)

@app.route("/admin")
def admin():
    if not authed(): return redirect(url_for("login"))
    out=[]
    for x in sticker_list(load_meta()):
        out.append({"id":get_id(x),"fn":get_filename(x),"description":get_description(x),"tags":get_tags(x)})
    return render_template_string(PAGE,stickers=out)

@app.route("/stickers/<path:filename>")
def sticker_file(filename):
    return send_from_directory(STICKERS, filename)

def find(meta, sid):
    sid=str(sid).zfill(3)
    return next((x for x in sticker_list(meta) if get_id(x)==sid),None)

@app.post("/admin/save-all")
def save_all():
    if not authed(): return redirect(url_for("login"))
    meta=load_meta()
    changed=0
    for sid in request.form.getlist("sid"):
        item=find(meta,sid)
        if not item:
            continue
        description=request.form.get(f"description_{sid}","").strip()
        tags=request.form.get(f"tags_{sid}","")
        if description != get_description(item) or tags != get_tags(item):
            set_description(item,description)
            set_tags(item,tags)
            changed += 1
    if changed:
        ok,msg=save_meta(meta,f"Update {changed} sticker(s)")
        flash(msg if ok else msg)
    else:
        flash("没有需要保存的修改")
    return redirect(url_for("admin"))


@app.post("/admin/edit/<sid>")
def edit(sid):
    if not authed(): return redirect(url_for("login"))
    meta=load_meta(); item=find(meta,sid)
    if not item: flash("没找到"); return redirect(url_for("admin"))
    set_description(item,request.form.get("description","").strip())
    set_tags(item,request.form.get("tags",""))
    _,msg=save_meta(meta,f"Update sticker {sid}"); flash(msg)
    return redirect(url_for("admin"))

@app.post("/admin/add")
def add():
    if not authed(): return redirect(url_for("login"))
    files=[f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        flash("至少选择一张图片")
        return redirect(url_for("admin"))
    meta=load_meta()
    used={int(get_id(x)) for x in sticker_list(meta) if get_id(x).isdigit()}
    next_id=max(used, default=0)+1
    added=[]
    os.makedirs(STICKERS,exist_ok=True)
    for f in files:
        while next_id in used:
            next_id += 1
        sid=str(next_id).zfill(3)
        ext=os.path.splitext(f.filename or "")[1].lower() or ".jpg"
        fn=f"{sid}{ext}"
        raw=f.read()
        with open(os.path.join(STICKERS,fn),"wb") as g:
            g.write(raw)
        if GITHUB_TOKEN and GITHUB_REPO:
            r=gh_put(f"stickers/{fn}",raw,f"Add sticker {sid}")
            if r.status_code not in (200,201):
                flash(f"{sid} 图片上传 GitHub 失败，已停止")
                break
        sticker_list(meta).append({
            "id":sid,
            "file":fn,
            "caption":"",
            "tone":"",
            "tags":[],
            "pending_review":True,
        })
        added.append(sid)
        used.add(next_id)
        next_id += 1
    if added:
        ok,msg=save_meta(meta,f"Register {len(added)} sticker(s) for review")
        flash(f"已批量添加 {len(added)} 张（{added[0]}–{added[-1]}），等待小机识别。{msg if not ok else ''}")
    return redirect(url_for("admin"))


@app.post("/admin/delete/<sid>")
def delete(sid):
    if not authed(): return redirect(url_for("login"))
    meta=load_meta(); item=find(meta,sid)
    if not item: flash("没找到"); return redirect(url_for("admin"))
    fn=get_filename(item); sticker_list(meta).remove(item)
    if GITHUB_TOKEN and GITHUB_REPO and fn:
        gh_delete(f"stickers/{fn}",f"Delete sticker {sid}")
    p=os.path.join(STICKERS,fn)
    if fn and os.path.exists(p): os.remove(p)
    _,msg=save_meta(meta,f"Remove sticker {sid}"); flash(msg)
    return redirect(url_for("admin"))
