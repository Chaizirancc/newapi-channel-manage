# new-api Channel Admin API — Error Reference

Collected from real debugging against a new-api (Calium-Ion/new-api) instance.

## Error → root cause → fix

| Symptom | Root cause | Fix |
|---|---|---|
| `200 {"message":"Username or password is incorrect, or user has been banned","success":false}` on login | Wrong password or banned user — **login failure is HTTP 200**, not 4xx | Check for `data.access_token` / `success`, never trust status 200 alone |
| `409 {"code":"AUTH_SESSION_LIMIT","message":"Conflict","success":false}` on `/api/user/login` | Another session for this user already exists (e.g. web dashboard open). Limit = 1 per user. | Ask user to log out of the browser dashboard, then retry. |
| `401 {"code":"AUTH_UNAUTHORIZED",...}` on `/api/channel*` | Using the model-serving `sk-` token (only works for `/v1/*`), or admin token expired. | Use the token from `/api/user/login` (Bearer). Re-login if expired. |
| `{"message":"channel cannot be empty"}` on `POST /api/channel` | Channel fields sent FLAT at top level; inner `Channel` is nil → `Key==""` in `validateChannel`. | Wrap: `{"mode":"single","channel":{...}}`. |
| `307` → `/api/channel/` (public domain) then empty body | caddy trailing-slash redirect drops body. | Call new-api directly at `http://<server>:<port>/api/...` (bypass caddy). |
| `404` + `{"error":{"type":"bad_response_status_code"}}` on a chat call through the gateway | Upstream rejected the request: either (a) `base_url` includes `/v1` → `host/v1/v1/chat/completions`, or (b) wrong upstream model id. | Set `base_url` WITHOUT `/v1`; verify the real model id via upstream `/v1/models`. |
| `PUT /api/channel/6` → `Invalid URL (PUT /api/channel/6)` | Update route is not registered with `/:id`. | Use `PUT /api/channel` (no `/:id`); put `id` in the flat body. |
| `PUT /api/channel` with `{"channel":{...}}` → `record not found` | Update expects a FLAT body, not the nested `channel` envelope. | Use flat `{"id":6,"base_url":"..."}`. |
| `POST /api/channel` with `mode:"single"` + `id:6` → `不支持的添加模式` | Create handler only adds; supplying an id doesn't switch it to update. | Use `PUT /api/channel` (flat body) to update. |

## Working request shapes (urllib / curl)

### Login
```
POST /api/user/login
{"username":"root","password":"<pw>"}
→ 200: {"data":{"access_token":"<tok>","session":{...}}}
```

### List
```
GET /api/channel?p=1&page_size=100   (Authorization: Bearer <tok>)
```

### Create (OpenAI type, nested)
```
POST /api/channel
{
  "mode": "single",
  "channel": {
    "name": "gmi-serving",
    "type": 1,
    "key": "<upstream_key>",
    "base_url": "https://api.gmi-serving.com",      # NO /v1
    "models": "[gmi]MiniMaxAI/MiniMax-M3",          # string, comma-separated
    "model_mapping": "{\"[gmi]MiniMaxAI/MiniMax-M3\":\"MiniMaxAI/MiniMax-M3\"}",  # JSON string
    "group": "default", "status": 1, "priority": 0, "weight": 0,
    "test_model": "MiniMaxAI/MiniMax-M3", "auto_ban": 1
  }
}
```

### Update (flat, no /:id)
```
PUT /api/channel
{"id": 6, "base_url": "https://api.gmi-serving.com"}
```
`status` is also a PUT field: `{"id": 6, "status": 0}` disables a channel, `{"id": 6, "status": 1}` re-enables it (useful to freeze an expiring-key channel instead of deleting).

### Delete (id in URL, unlike update)
```
DELETE /api/channel/6        (Authorization: Bearer <tok>)
```
Route verified by an unauthenticated probe returning 401 (auth precedes routing) rather than 404. Real deletion not yet exercised on this instance — probe first with `DELETE /api/channel/999999` to confirm the route before deleting a real id.

### End-to-end test (gateway, model-serving token)
```
POST <gateway>/v1/chat/completions
Authorization: Bearer <sk-token>
{"model":"[gmi]MiniMaxAI/MiniMax-M3","messages":[{"role":"user","content":"hi"}],"stream":true,"max_tokens":50}
```

## Field notes
- `type`: integer channel type. `1` = OpenAI-compatible. Copy from an existing same-provider channel.
- `models` / `model_mapping`: both STRINGS. A `[prefix]` in the model name is stripped via `model_mapping` before forwarding upstream.
- `base_url`: root of the upstream API for OpenAI type (new-api appends `/v1/chat/completions`). **Never guess it** — confirm from the provider's docs or an existing channel; a guessed domain creates a silently broken channel.
- `status`: `1` = enabled, `0` = disabled. Updatable via PUT.
- new-api process typically has `HTTP_PROXY=http://127.0.0.1:7890` so upstream calls egress through the local clash/mihomo proxy; ensure the upstream host is reachable via that proxy (or via the server's own route).
