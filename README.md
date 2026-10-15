# Salt Stickers MCP

一个私人抽象表情包 MCP，包含 28 张 JPG/GIF 表情。

## 提供的工具

- `search_stickers(intent, limit)`：按自然语言语气搜索候选表情。
- `pick_sticker(intent)`：让 ChatGPT 根据语境直接挑一张并返回图片。
- `send_sticker(sticker_id)`：按三位 ID 精确返回一张图片。

`metadata.json` 里是每张表情的第一版语义标签。以后想改某张图的理解，只需要改对应的 `caption` / `tags` / `tone`，不必动 Python。

## 部署到 Render（最省事）

1. 把这个项目上传到一个 GitHub 仓库。
2. 登录 Render，选择 **New → Blueprint**（或 New Web Service）。
3. 连接刚才的 GitHub 仓库。
4. Render 会读取根目录的 `render.yaml` / `Dockerfile` 并部署。
5. 部署成功后会得到一个地址，例如：

   `https://salt-stickers.onrender.com`

6. 在 ChatGPT 的“添加 MCP / 自定义连接器”入口中，填写：

   `https://salt-stickers.onrender.com/mcp`

这个 MCP 本身不需要账号权限，也不会读写你的其他数据；它只会把项目目录里的表情图片作为工具结果返回。

## 本地运行（可选）

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python server.py
```

默认监听 `0.0.0.0:8000`，MCP 地址通常为 `http://localhost:8000/mcp`。

## 修改表情语义

打开 `metadata.json`。例如：

```json
{
  "id": "015",
  "file": "015.gif",
  "caption": "我真没招了。。",
  "tags": ["没招", "无语", "投降", "摆烂"],
  "tone": "一种被折腾到彻底没招的无语。"
}
```

可以随便添加自己的抽象标签；搜索器会同时看 caption、tags 和 tone。


## 表情包后台管理

部署后可打开：

`https://你的服务地址/admin`

后台可以：
- 查看全部表情包、备注和标签
- 修改备注 / 标签
- 上传新表情包
- 删除表情包
- 修改会同步提交回 GitHub 仓库，避免 Render 重启后丢失

在 Render 的 Environment 中添加：

- `ADMIN_PASSWORD`：你自己设置的后台密码
- `GITHUB_TOKEN`：GitHub Personal Access Token
- `GITHUB_REPO`：仓库名，格式为 `你的用户名/仓库名`
- `GITHUB_BRANCH`：通常填 `main`

GitHub Token 只需要对这个仓库有 Contents 读写权限。不要把 Token 写进代码或提交到公开仓库。

如果暂时不配置 GitHub Token，后台仍可修改当前运行实例，但 Render 重启/重新部署后这些修改可能丢失。
