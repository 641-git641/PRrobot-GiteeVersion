# Gitee Pull Request Review Bot

独立挂载的 Gitee Pull Request AI 评审服务，使用 DeepSeek API 生成评审与修改建议。

`robot/` 不属于 Maven modules、前端工程或业务服务。删除整个 `robot/` 目录不会影响 `bh` 的编译、启动和运行；机器人只通过 Gitee Webhook 与 Gitee API 访问仓库。

## 能力边界

- 监听 Pull Request 新建、更新、重新打开和转为 ready 事件。
- 获取 PR 元信息与 Diff，调用 DeepSeek Chat Completions API。
- 校验结构化评审结果，并在 Gitee PR 下发布一条 Markdown 总结评论。
- 同一个仓库、PR、head SHA 只评审一次；失败任务按有限次数重试。
- 只读审查：不执行 PR 代码，不 push，不修改文件，不批准，不拒绝，不合并。

## 效果

<img width="1363" height="1184" alt="image" src="https://github.com/user-attachments/assets/1627f0c0-a4b2-4ff2-902e-41ebb8dd06e2" />


## 本地运行

Python 3.11+：

```bash
cd robot
python -m venv .venv
# Windows PowerShell
.venv\\Scripts\\Activate.ps1
# macOS/Linux
# source .venv/bin/activate
python -m pip install -e ".[dev]"
copy .env.example .env  # Windows；macOS/Linux 使用 cp
```

编辑 `.env`，至少填写：

```text
GITEE_API_TOKEN=服务器端 Token
GITEE_WEBHOOK_SECRET=Webhook 共享密钥
GITEE_REPO_ALLOWLIST=liu-huangmin/bjbh
DEEPSEEK_API_KEY=服务器端 DeepSeek Key
DEEPSEEK_MODEL=你的模型 ID
```

启动：

```bash
uvicorn reviewbot.main:create_app --factory --host 127.0.0.1 --port 8090
```

检查：

```text
GET http://127.0.0.1:8090/healthz
GET http://127.0.0.1:8090/readyz
```

## Docker 挂载

```bash
docker compose up --build -d
```

服务只需要挂载自己的 `robot/data` 持久化目录。部署到公网时，将 Gitee Webhook 指向：

```text
https://<your-host>/webhook/gitee
```

在 Gitee 仓库 WebHook 中启用 Pull Request 相关事件，并把 WebHook 密钥设置为 `GITEE_WEBHOOK_SECRET`。先使用 Gitee 的测试推送确认实际 payload，再核对仓库名、PR 序号和提交 SHA。

## 鉴权说明

Gitee API v5 的 Swagger 将 `access_token` 列为接口参数，因此默认 `GITEE_AUTH_MODE=query` 以获得兼容性；所有请求必须使用 HTTPS，代码不会记录 URL、参数或 Token。若当前 Gitee 部署确认支持请求头鉴权，可改为 `GITEE_AUTH_MODE=header`。

DeepSeek Key 与 Gitee Token 只存在服务端环境，不写入仓库，不发送给模型。模型只接收 PR 元信息、审查规则和受大小限制的 Diff。

## 自定义审查规则

默认读取 `ROBOT_REVIEW_RULE_FILE`（默认 `review-rules.md`）。部署到其他项目时，可以把其他规则文件挂载到容器并通过环境变量切换，机器人本身不依赖 `bh` 的 Java、Vue 或数据库代码。

## 验证

```bash
pytest -q
```


测试使用内存 HTTP transport，不访问真实 Gitee 或 DeepSeek。生产验证应创建一个无敏感内容的测试 PR，确认新 PR 只产生一条带 `bh-ai-review:<headSha>` 标记的评论。
