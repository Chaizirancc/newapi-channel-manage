#!/usr/bin/env python3
"""
new-api 渠道管理助手（OpenAI 兼容类型模板）

用法:
  python manage_channel.py <admin_base> <user> <pw> <upstream_key> <upstream_base_url> <gateway_base> <gateway_token> <model_ids> [prefix]

示例:
  python manage_channel.py http://<server>:3000 root '<pw>' 'eyJ...' \
      https://api.upstream-example.com https://gateway.example.com/v1 'sk-xxx' \
      'MiniMaxAI/MiniMax-M3,Some/Other-Model' gmi

参数说明:
  admin_base         new-api 管理接口地址（建议直连 :3000 端口，绕 caddy 307 丢 body 问题）
  user / pw          new-api 管理后台账号（登录拿 access_token，非 sk- 模型 key）
  upstream_key       上游提供商的 API key
  upstream_base_url  上游 API 根地址（OpenAI 类型**不能带 /v1**，new-api 会自动补）
  gateway_base       网关对外地址，例如 https://gateway.example.com/v1（用于端到端实测）
  gateway_token      网关模型调用 key（sk- 开头）
  model_ids          上游真实模型 id，多个用英文逗号分隔，例如 MiniMaxAI/MiniMax-M3,Gemini-2.0-flash
  prefix             [可选] 网关侧模型名前缀；默认取第一个 model_id 第一个 '/' 前的部分小写化

行为:
  1. 登录拿 access_token（注意:密码错误时返回 HTTP 200 + success:false，需检查 data.access_token）
  2. 列出已有渠道；若同名渠道已存在则跳过创建（幂等）
  3. 创建 OpenAI 类型渠道（嵌套 {"mode":"single","channel":{...}}，base_url 不带 /v1，
     models / model_mapping 均为字符串；一次支持多个模型）
  4. 经网关流式实测第一个模型，打印首字延迟与输出

注意（踩坑总结，详见 references/api_notes.md）:
  - 单用户会话上限=1，建渠道前需退出浏览器后台，否则登录 409 AUTH_SESSION_LIMIT
  - upstream_base_url 必须人工确认，不要靠脚本猜（旧版启发式猜域名会建错渠道）
"""
import sys, json, time, urllib.request, urllib.error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁用 urllib 自动重定向：301/302/303 会自动把 POST 转成 GET 并丢 body。
    统一改为手动跟随，保持 method 与 body 不变。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def req(base, method, path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    url = base.rstrip("/") + path
    for _ in range(5):
        r = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            resp = _opener.open(r, timeout=30)
            return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                loc = e.headers.get("Location")
                if loc:
                    url = loc if loc.startswith("http") else (base.rstrip("/") + loc)
                    continue
            return e.code, e.read().decode()
    raise RuntimeError("重定向次数过多")


def usage():
    print(__doc__)
    sys.exit(1)


def main():
    if len(sys.argv) < 9:
        print("参数不足，需要 8 个必选参数。\n")
        usage()
    base, user, pw, up_key, base_url, gw_base, gw_token, model_ids_arg = sys.argv[1:9]
    model_ids = [m.strip() for m in model_ids_arg.split(",") if m.strip()]
    if not model_ids:
        print("model_ids 为空\n")
        usage()
    prefix = sys.argv[9] if len(sys.argv) > 9 else model_ids[0].split("/")[0].lower()
    gw_models = ["[%s]%s" % (prefix, m) for m in model_ids]
    mapping = {gw: real for gw, real in zip(gw_models, model_ids)}
    channel_name = prefix + "-serving"

    # 1) 登录。注意：密码错误时返回 HTTP 200 + success:false，必须检查 data.access_token
    st, body = req(base, "POST", "/api/user/login", {"username": user, "password": pw})
    tok = (json.loads(body).get("data") or {}).get("access_token")
    if not tok:
        print("登录失败: HTTP %s | %s" % (st, body[:200]))
        if st == 409:
            print("  -> 409 通常是浏览器后台占用会话，请先退出 new-api 网页后台再试")
        return
    print("登录成功，access_token 已获取")

    # 2) 列出渠道 + 幂等检查（同名已存在则跳过创建）
    st, body = req(base, "GET", "/api/channel?p=1&page_size=100", token=tok)
    items = json.loads(body).get("data", {}).get("items", [])
    print("现有渠道数:", len(items))
    exists = [c for c in items if c.get("name") == channel_name]
    for c in items:
        print("  id=%s name=%s type=%s base=%s" % (c.get("id"), c.get("name"), c.get("type"), c.get("base_url")))
    if exists:
        print("\n渠道 %r 已存在 (id=%s)，跳过创建。如需修改请用 PUT /api/channel（body 平铺带 id）。"
              % (channel_name, exists[0].get("id")))
        return

    # 3) 创建渠道（嵌套 + 字符串字段 + base_url 不带 /v1 + 多模型）
    payload = {
        "mode": "single",
        "channel": {
            "name": channel_name,
            "type": 1,
            "key": up_key,
            "base_url": base_url,
            "models": ",".join(gw_models),
            "model_mapping": json.dumps(mapping),
            "group": "default", "status": 1, "priority": 0, "weight": 0,
            "test_model": model_ids[0], "auto_ban": 1
        }
    }
    print("\n创建渠道 %r，模型 %d 个，base_url=%s" % (channel_name, len(model_ids), base_url))
    st, body = req(base, "POST", "/api/channel", payload, tok)
    print("CREATE:", st, body[:200])
    if st != 200 or not json.loads(body).get("success"):
        print("创建失败，请对照 references/api_notes.md 排查")
        return

    # 4) 经网关实测第一个模型
    data = json.dumps({"model": gw_models[0], "messages": [{"role": "user", "content": "用一句话介绍你自己，不超过30字。"}],
                       "stream": True, "max_tokens": 120}).encode()
    r = urllib.request.Request(gw_base.rstrip("/") + "/chat/completions", data=data,
        headers={"Authorization": "Bearer " + gw_token, "Content-Type": "application/json"}, method="POST")
    t0 = time.time(); first = None; out = 0
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            for line in resp:
                line = line.decode().strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except Exception:
                    continue
                if first is None and obj.get("choices"):
                    first = time.time() - t0
                for ch in obj.get("choices", []):
                    d = (ch.get("delta") or {}).get("content")
                    if d:
                        out += len(d)
        print("\n网关实测 %s -> TTFT=%dms 输出字符=%d" % (gw_models[0], int((first or 0) * 1000), out))
    except urllib.error.HTTPError as e:
        print("\n网关实测失败:", e.code, e.read().decode()[:300])


if __name__ == "__main__":
    main()
