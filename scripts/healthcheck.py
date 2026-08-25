#!/usr/bin/env python3
"""
new-api 全渠道健康检查（只读，安全）

用法:
  python healthcheck.py <admin_base> <user> <pw> <gateway_base> <gateway_token> [--timeout N]

示例:
  python healthcheck.py http://<server>:3000 root '<pw>' \
      https://gateway.example.com/v1 'sk-xxx' --timeout 60

行为:
  1. 登录拿 access_token（需退出浏览器后台，单会话限制 409）
  2. 列出全部渠道；对每个渠道取 models 字段第一个网关模型名
  3. 经网关逐个流式实测（短 prompt、max_tokens=30），记录 TTFT 与结果
  4. 输出健康清单表格 + 汇总

输出示例:
  [OK]  id=1  gemini-main      [gemini]gemini-3.5-flash          TTFT=762ms
  [FAIL] id=6  gmi-serving     [gmi]MiniMaxAI/MiniMax-M3          HTTP 503 上游过载
  汇总: 5/6 通过

注意:
  - 只读操作，不会修改任何渠道
  - 每个渠道 1 次请求，耗时取决于上游；--timeout 控制单请求超时（默认 60s）
  - 冷启动渠道（如 NVIDIA）偶发 30-40s 首字，超时设为 90s 以上可减少误报
"""
import sys, json, time, urllib.request, urllib.error, argparse


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def req(base, method, path, payload=None, token=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    url = base.rstrip("/") + path
    for _ in range(5):
        r = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            resp = _opener.open(r, timeout=timeout)
            return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                loc = e.headers.get("Location")
                if loc:
                    url = loc if loc.startswith("http") else (base.rstrip("/") + loc)
                    continue
            return e.code, e.read().decode()
        except Exception as e:  # 网络层错误（超时/拒绝连接等）
            return -1, repr(e)
    raise RuntimeError("重定向次数过多")


def chat_test(gw_base, gw_token, model, timeout):
    """经网关流式实测单个模型，返回 (ok, detail)。ok=True 表示流式正常返回。"""
    data = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "回复OK"}],
                       "stream": True, "max_tokens": 30, "temperature": 0.1}).encode()
    r = urllib.request.Request(gw_base.rstrip("/") + "/chat/completions", data=data,
        headers={"Authorization": "Bearer " + gw_token, "Content-Type": "application/json"}, method="POST")
    t0 = time.time(); first = None; got_content = False
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            for line in resp:
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except Exception:
                    continue
                if obj.get("error"):
                    return False, "upstream error: " + json.dumps(obj["error"], ensure_ascii=False)[:160]
                if first is None and obj.get("choices"):
                    first = time.time() - t0
                for ch in obj.get("choices", []):
                    d = (ch.get("delta") or {}).get("content")
                    if d:
                        got_content = True
        if got_content:
            return True, "TTFT=%dms" % int((first or 0) * 1000)
        return False, "empty stream"
    except urllib.error.HTTPError as e:
        return False, "HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:160])
    except Exception as e:
        return False, repr(e)[:160]


def main():
    ap = argparse.ArgumentParser(description="new-api 全渠道健康检查（只读）")
    ap.add_argument("admin_base", help="new-api 管理接口，建议 http://<host>:3000")
    ap.add_argument("user")
    ap.add_argument("pw")
    ap.add_argument("gateway_base", help="网关 /v1 地址，如 https://gateway.example.com/v1")
    ap.add_argument("gateway_token", help="网关模型 key（sk- 开头）")
    ap.add_argument("--timeout", type=int, default=60, help="单请求超时秒数（默认 60，NVIDIA 冷启动建议 90+）")
    args = ap.parse_args()

    # 登录
    st, body = req(args.admin_base, "POST", "/api/user/login",
                   {"username": args.user, "password": args.pw})
    tok = (json.loads(body).get("data") or {}).get("access_token")
    if not tok:
        print("登录失败: HTTP %s | %s" % (st, body[:200]))
        if st == 409:
            print("  -> 409 通常是浏览器后台占用会话，请先退出 new-api 网页后台再试")
        sys.exit(1)
    print("登录成功\n")

    # 列渠道
    st, body = req(args.admin_base, "GET", "/api/channel?p=1&page_size=100", token=tok)
    items = json.loads(body).get("data", {}).get("items", [])
    if not items:
        print("没有渠道"); return
    print("渠道总数: %d\n" % len(items))

    results = []
    for c in items:
        name = c.get("name"); cid = c.get("id"); ctype = c.get("type")
        models = [m.strip() for m in (c.get("models") or "").split(",") if m.strip()]
        if not models:
            results.append((cid, name, ctype, "(无模型)", False, "no models configured"))
            continue
        model = models[0]  # 第一个网关模型名（[前缀]xxx）
        ok, detail = chat_test(args.gateway_base, args.gateway_token, model, args.timeout)
        results.append((cid, name, ctype, model, ok, detail))
        tag = "OK  " if ok else "FAIL"
        print("[%s] id=%-3s %-20s %-45s %s" % (tag, cid, name, model, detail))

    ok_n = sum(1 for r in results if r[4])
    print("\n汇总: %d/%d 渠道通过" % (ok_n, len(results)))
    if ok_n < len(results):
        print("失败渠道请对照 references/api_notes.md 排查（404=base_url/模型名；503=上游过载；超时=代理或上游慢）")


if __name__ == "__main__":
    main()
