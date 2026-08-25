---
name: newapi-channel-manage
description: Manage channels on a new-api (one-api fork) gateway via its admin REST API — add, update, disable/delete, and test OpenAI-compatible (and other type) channels. This skill should be used when a user wants to add a new upstream API provider/channel to their new-api gateway, fix a channel's base_url/key/models, or verify a channel works end-to-end. It captures non-obvious API gotchas (login failures return HTTP 200, nested request body, session-limit login, base_url must omit /v1, avoiding the caddy trailing-slash 307) that otherwise cause silent failures.
agent_created: true
---

# new-api Channel Management via Admin API

## Overview

This skill drives the new-api (QuantumNous/new-api) admin REST API to create, update, and verify **channels** (upstream API providers) on a user's gateway. It exists because the API has several non-obvious behaviors that cause silent failures ("channel cannot be empty", 404, "record not found") if the wrong request shape is used.

Scope: OpenAI-compatible channels (type `1`) are the common case; other channel types follow the same envelope but differ in `base_url`/`key` semantics. Always confirm the channel `type` integer from an existing channel of the same provider before creating.

## Critical gotchas (read before any call)

1. **Login is required for admin endpoints.** The model-serving token (`sk-...`) only calls `/v1/*`. To manage channels, log in with **username + password** at `POST /api/user/login`; the response `data.access_token` is the Bearer token for `/api/channel*`.
2. **A failed login is HTTP 200, not 4xx.** Wrong password or banned user returns `200 {"message":"Username or password is incorrect, or user has been banned","success":false}` — there is NO `data.access_token`. Never treat HTTP 200 as success; check for `data.access_token` / `success`.
3. **`409 AUTH_SESSION_LIMIT` means the session quota is FULL, not "1 device only".** new-api (v1.0.0-rc.25) allows **50 active login sessions per user** (`common.UserSessionActiveLimit`). Sessions **never auto-expire or get cleaned** — every successful script/browser login creates one and it stays `active` forever. When script logins pile up (e.g. repeated login-per-request automation), the quota fills and any new login returns `409 {"code":"AUTH_SESSION_LIMIT","message":"Conflict","success":false}`. Fix: revoke stale sessions in the DB and restart new-api (clears the session cache):
   ```sql
   UPDATE user_sessions SET status='revoked', revoked_at=strftime('%s','now'), revoked_reason='manual_cleanup'
   WHERE status='active' AND created_at < strftime('%s','2026-08-24 00:00:00');
   ```
   Then `docker restart new-api`. Reuse one access token across API calls instead of logging in per request — it's valid ~15 min and avoids refilling the quota.
4. **Create uses a NESTED body.** `POST /api/channel` must be `{"mode":"single","channel":{...field...}}`. Sending channel fields flat at the top level yields `{"message":"channel cannot be empty"}` (the inner `Channel` is nil → `Key==""`).
5. **`models` and `model_mapping` are STRINGS**, not arrays/objects:
   - `models`: comma-separated, e.g. `"[gmi]MiniMaxAI/MiniMax-M3"`.
   - `model_mapping`: a JSON-encoded STRING, e.g. `"{\"[gmi]MiniMaxAI/MiniMax-M3\":\"MiniMaxAI/MiniMax-M3\"}"`. The left key is the gateway-facing model name; the right value is what gets sent upstream. The `[prefix]` is stripped and forwarded to the upstream as the bare model id.
6. **Update uses `PUT /api/channel` (NO `/:id`)** with a FLAT body `{"id":<n>,"base_url":"..."}`. Nested `{"channel":{...}}` returns `record not found`. The `id` lives in the body, not the URL.
7. **`base_url` must NOT include `/v1`** for OpenAI-type channels. new-api appends `/v1/chat/completions` itself; including `/v1` yields `https://host/v1/v1/chat/completions` → upstream 404. Existing channels (e.g. nvidia) use `https://integrate.api.nvidia.com` (no `/v1`).
8. **Avoid the caddy trailing-slash 307.** Hitting the public domain, `POST /api/channel` is 307-redirected to `/api/channel/`, which can drop the body. Most reliable: call new-api directly at `http://<server>:3000/api/...` (or whatever port new-api listens on), bypassing the reverse proxy. The token is valid on any interface.
9. **Verify the actual upstream model id** before trusting the user's spelling. `GET https://<upstream-base>/v1/models` with the upstream key returns the real `id`s. A wrong id produces a 404 from the upstream (surfaced by new-api as `bad_response_status_code`), not from new-api itself.
10. **Delete uses `DELETE /api/channel/<id>`** (id in the URL, unlike update). Route existence was confirmed by an unauthenticated probe returning 401 (auth runs before routing) rather than 404; real deletion has not been exercised on this instance — before deleting, probe with a non-existent id (`DELETE /api/channel/999999`) to confirm the route. To disable instead of delete, `PUT /api/channel` with `{"id":<n>,"status":0}` (1 = enabled, 0 = disabled).

## Workflow

### 1. Authenticate
`POST /api/user/login` with `{"username":<user>,"password":<pw>}`. Parse `data.access_token`. Reuse this token for all subsequent calls in the same session (it expires in ~15 min; if expired, log in again — but beware: **every successful login creates a persistent active session** (quota 50); call `/api/user/logout` when done, or periodically clean stale sessions as in gotcha 3.

### 2. Inspect existing channels (templates + correct type)
`GET /api/channel?p=1&page_size=100`. Note the `type`, `base_url` (no `/v1`), and `model_mapping` format of a same-provider channel to copy. `GET /api/channel/<id>` returns the full record.

### 3. Create a channel
`POST /api/channel` (direct to `:3000`), body:
```json
{"mode":"single","channel":{"name":"<name>","type":1,"key":"<upstream_key>","base_url":"https://<host>","models":"[<prefix>]<UpstreamModelId>","model_mapping":"{\"[<prefix>]<UpstreamModelId>\":\"<UpstreamModelId>\"}","group":"default","status":1,"priority":0,"weight":0,"test_model":"<UpstreamModelId>","auto_ban":1}}
```
Expect `{"success":true}`. Confirm with `GET /api/channel?p=1&page_size=100` filtering by `name`.

### 4. Fix a channel (e.g. wrong base_url / key rotation)
`PUT /api/channel` (no `/:id`), flat body `{"id":<n>,"base_url":"https://<host>"}`. Re-GET to confirm. `status` is also updatable this way: `{"id":<n>,"status":0}` disables a channel (e.g. an expiring gmi key) without deleting it. **Key rotation**: `PUT /api/channel` with `{"id":<n>,"key":"<new_upstream_key>"}` — same flat shape, just swap the field.

### 5. Delete / disable a channel
`DELETE /api/channel/<id>` (id in URL) removes it permanently. Prefer disabling via PUT `{"id":<n>,"status":0}` when the channel may be re-enabled later. Probe the route first with `DELETE /api/channel/999999` if unsure.

### 6. End-to-end test through the gateway
Call the gateway `/v1/chat/completions` with the **model-serving** `sk-` token and `model: "[<prefix>]<UpstreamModelId>"` (streaming). A 404 with `bad_response_status_code` means the upstream rejected it (wrong model id or doubled `/v1` in base_url) — not a gateway auth problem. A successful stream confirms the channel works.

### 7. Health-check every channel (read-only)
`scripts/healthcheck.py` logs in, lists all channels, and streams a tiny request through the gateway for each (first gateway model per channel). Run it after adding/editing channels or before a subscription switch to snapshot baseline health.

## Troubleshooting (channel down / slow)

Work top-down; each layer is verified by the next check.

1. **Client side first** — is the gateway itself reachable? `GET <gateway>/v1/models` with the `sk-` token (any model works). 401 → bad key. Connection refused → new-api down.
2. **new-api error body** — call the failing model through the gateway and read the error:
   - `bad_response_status_code` → the **upstream rejected** the request. Check `base_url` for a doubled `/v1`, and verify the real model id via `GET <upstream-base>/v1/models` with the upstream key.
   - `Timeout` / `Request timeout` → upstream slow or unreachable **via the proxy**; go to step 4.
   - `503` → upstream overloaded; plain retry usually works (NVIDIA is notorious).
   - `channel not enabled` / `quota not enough` → channel `status:0` or token quota exhausted — check DB (`/data/new-api/one-api.db`) or dashboard.
3. **Is the channel mapped correctly?** `GET /api/channel/<id>` — confirm `base_url` (no `/v1`), `models`/`model_mapping` (strings), `key` (not expired/rotated).
4. **Proxy layer (mihomo/clash)** — new-api egresses via `HTTP_PROXY=http://127.0.0.1:7890`. If upstream calls fail only for some providers, the proxy route is likely the cause: check `config.yaml` rules (e.g. Google-family hosts must NOT land on HK nodes; NVIDIA is in the general auto-select group and a HK node can break it). Test egress directly from the server: `curl -x http://127.0.0.1:7890 -s -o /dev/null -w '%{http_code}' https://<upstream-host>`. Confirm the listening ports with `ss -tlnp` (`7890` proxy, `9090` mihomo API, `3000` new-api, `80/443` caddy).
5. **Logs / DB** — new-api logs under `/data/new-api/logs/`; the SQLite DB `/data/new-api/one-api.db` can be read (read-only) for token/quota/channel state.

## Security notes

- **Never commit real credentials.** This skill ships with placeholders only; all secrets are passed via argv at runtime (`manage_channel.py`, `healthcheck.py`). Don't hardcode keys/passwords into the scripts or docs, and keep `HANDOVER.md`-style secret files out of any git repo.
- Admin API + model key are different tokens: the `sk-` model token only reaches `/v1/*`; the admin token (from login) reaches `/api/*`. Treat the admin token as a full control-plane credential.
- `healthcheck.py` and listing are read-only; creation/update/delete mutate state — double-check the channel `id` before PUT/DELETE.

## Scripts

`scripts/manage_channel.py` — helper that logs in (root + password), lists channels (skipping creation if a same-named channel already exists), creates an OpenAI-type channel (one or more models, comma-separated), and tests it through the gateway:
```
python manage_channel.py <admin_base> <user> <pw> <upstream_key> <upstream_base_url> <gateway_base> <gateway_token> <model_ids> [prefix]
```
`upstream_base_url` is a **required, human-confirmed** argument (must NOT include `/v1`); the script never guesses it. Adapt the payload inline for other providers/types.

`scripts/healthcheck.py` — read-only health check of all channels through the gateway (login → list → one tiny streamed test per channel → summary table):
```
python healthcheck.py <admin_base> <user> <pw> <gateway_base> <gateway_token> [--timeout N]
```
Use `--timeout 90+` when cold-start channels (e.g. NVIDIA) are present to avoid false failures.

## References

`references/api_notes.md` — full list of error messages → root cause → fix, and the exact curl/urllib request shapes that work.
