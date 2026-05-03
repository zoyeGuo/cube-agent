# Python FastAPI Bridge

这是一个最小可运行的 Python FastAPI bridge，用作桌面数字人前端与 Hermes 之间的中间层。

当前版本目标：

- 先能跑
- 先能被 Postman 测通
- 结构清晰，便于后续把 mock 替换成真实 Hermes 调用

## 目录结构

```text
bridge/
├── app/
│   ├── adapters/
│   │   └── hermes.py
│   ├── routers/
│   │   ├── chat.py
│   │   └── health.py
│   ├── schemas/
│   │   └── chat.py
│   ├── services/
│   │   └── chat_service.py
│   └── main.py
├── README.md
└── requirements.txt
```

## 环境要求

- Python 3.10+

## 安装依赖

```bash
pip install -r requirements.txt
```

## 启动服务

在 `bridge` 目录下执行：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

启动成功后，服务会监听：

```text
http://127.0.0.1:8000
```

## 接口说明

### GET /health

用于健康检查。

响应示例：

```json
{
  "ok": true
}
```

### POST /chat

当前为 mock 实现，但已经通过 service 和 Hermes adapter 分层，后续可以切换为真实 Hermes。

请求体：

```json
{
  "message": "你好",
  "session_id": "local-test"
}
```

响应示例：

```json
{
  "reply": "你好，我是秘书助手。",
  "state": "done",
  "session_id": "local-test",
  "source": "mock"
}
```

## Postman 测试方法

### 1. 测试 GET /health

- Method: `GET`
- URL: `http://127.0.0.1:8000/health`

预期结果：

- HTTP `200 OK`
- 返回：

```json
{
  "ok": true
}
```

### 2. 测试 POST /chat 正常请求

- Method: `POST`
- URL: `http://127.0.0.1:8000/chat`
- Headers:
  - `Content-Type: application/json`
- Body 选择 `raw` + `JSON`

请求示例：

```json
{
  "message": "你好",
  "session_id": "local-test"
}
```

预期结果：

- HTTP `200 OK`
- 返回包含以下字段：
  - `reply`
  - `state`
  - `session_id`
  - `source`

响应示例：

```json
{
  "reply": "你好，我是秘书助手。",
  "state": "done",
  "session_id": "local-test",
  "source": "mock"
}
```

### 3. 测试 POST /chat 参数缺失

- Method: `POST`
- URL: `http://127.0.0.1:8000/chat`
- Headers:
  - `Content-Type: application/json`
- Body 选择 `raw` + `JSON`

请求示例：

```json
{
  "session_id": "local-test"
}
```

预期结果：

- HTTP `422 Unprocessable Entity`
- 说明 Pydantic 参数校验生效

### 4. 测试跨域

当前已开启基础 CORS：

- `allow_origins=["*"]`
- `allow_methods=["*"]`
- `allow_headers=["*"]`

如果后续前端在浏览器中调用该服务，可以据此继续收敛域名配置。

## 后续接入真实 Hermes 的修改点

后续如果要把 `/chat` 接到真实 Hermes，建议只改以下位置：

1. 在 `app/adapters/hermes.py` 中新增真实的 `HermesClient` 实现，例如 `RealHermesClient`
2. 在 `RealHermesClient.generate_reply()` 中封装真实的 Hermes HTTP/RPC 调用
3. 在 `app/services/chat_service.py` 中将默认注入的 `MockHermesClient` 替换为 `RealHermesClient`
4. 如 Hermes 返回字段不一致，在 service 层做统一映射，保证 router 和 schema 不需要改动

这样可以保持接口层稳定，尽量减少对前端联调的影响。
