# newapi-channel-manage

new-api（one-api fork）**渠道运维技能**：通过管理 REST API 增删改查渠道，并做端到端验证。把全部踩坑经验固化成一个自包含的 skill，可直接交给任何 AI 助手使用，或人工照着操作。

> 核心价值：new-api 管理 API 有一堆非直观行为（登录失败返回 200、创建必须嵌套 body、PUT 不带 `/:id`、base_url 不能带 `/v1`、反代 307 丢 body……），照常规写法几乎必然踩坑。本技能全部实测记录并配有开箱即用的脚本。

## 功能

| 能力 | 说明 |
|---|---|
| ➕ 创建渠道 | OpenAI 兼容类型，一次支持多个模型，自动生成 `model_mapping` |
| ✏️ 修改渠道 | 改 base_url / 上游 key（key 轮换）/ models / status 禁用启用 |
| 🚫 删除渠道 | `DELETE /api/channel/<id>`（文档中标注了未实测的边界） |
| 📋 列出渠道 | 查看现有渠道的 type / base_url / 模型格式，作为新建模板 |
| 🧪 端到端实测 | 建完/改完渠道经网关流式调用，确认真的通 |
| 🩺 全渠道体检 | 一键对全部渠道逐个实测，输出健康清单（只读，安全） |
| 🔍 故障排查 | 渠道不通时自顶向下的排查路径（网关 → 渠道 → 代理层 mihomo → 日志/DB） |

## 目录结构

```
newapi-channel-manage/
├── SKILL.md                    # 技能主文档（给 AI 助手读：10 条 gotcha + 7 步工作流 + 排查 + 安全）
├── README.md                   # 本文件（给人读）
├── LICENSE                     # MIT
├── scripts/
│   ├── manage_channel.py       # 登录→列渠道→建渠道（幂等）→网关实测
│   └── healthcheck.py          # 全渠道健康检查（只读）
└── references/
    └── api_notes.md            # 错误→根因→修复对照表 + 可直接照抄的请求形状
```

## 快速开始

环境：Python 3（仅标准库，无第三方依赖）。

### 1. 创建渠道

```bash
python scripts/manage_channel.py \
  http://<server>:3000 \          # new-api 管理接口（直连端口，绕 caddy 307）
  root '<管理密码>' \              # 管理后台账号
  '<上游key>' \                   # 上游提供商 API key
  https://api.example.com \       # 上游根地址，不能带 /v1
  https://gateway.example.com/v1 \  # 网关地址
  'sk-网关key' \                  # 网关模型 key
  'Provider/Model-A,Provider/Model-B'  # 模型 id，多个用逗号分隔
```

脚本会自动：登录（密码错误是 HTTP 200 不会误判）→ 列渠道 → 同名已存在则跳过（幂等）→ 创建 → 经网关实测首字延迟。

### 2. 全渠道健康检查

```bash
python scripts/healthcheck.py \
  http://<server>:3000 root '<管理密码>' \
  https://gateway.example.com/v1 'sk-网关key' --timeout 90
```

输出每渠道 OK/FAIL + TTFT，只读不修改任何配置。NVIDIA 等冷启动渠道建议 `--timeout 90+`。

### 3. 修改 / 禁用 / 删除渠道

见 `SKILL.md` Workflow 4-5，或 `references/api_notes.md` 的请求形状（PUT 平铺带 id；禁用用 `status:0`；删除 `DELETE /api/channel/<id>`）。

## 关键约定（速览）

- **登录失败是 HTTP 200** + `success:false`，别只看状态码
- 创建渠道 body 必须嵌套：`{"mode":"single","channel":{...}}`
- 更新渠道用 `PUT /api/channel`（**不带 `/:id`**），body 平铺带 `id`
- OpenAI 类型 `base_url` **不能带 `/v1`**（new-api 自动补）
- `models` / `model_mapping` 都是**字符串**，不是数组/对象
- 单用户会话上限 = 1：浏览器挂着后台时脚本登录会 409，先退出
- 管理接口建议直连 new-api 端口（`:3000`），走 caddy 域名会被 307 补斜杠丢 body

## 如何作为 AI skill 使用

- **方式一（推荐）**：把整个目录放进 AI 助手的 skills 目录，助手会按 `SKILL.md` 自动加载。
- **方式二**：把 `SKILL.md` 整段贴给任意 AI 助手（Claude / ChatGPT 等）当上下文。
- 所有凭据运行时通过命令行参数传入，仓库内**不含任何真实密钥**。

## 安全提醒

- ⚠️ 本仓库只有占位符，**没有任何真实凭据**。请勿把含密码/key 的文件（如交接文档）提交进任何 git 仓库。
- 管理 token 是控制面凭据（能增删改渠道），与模型 key 分开保管。
- 删除渠道不可恢复，删除前建议先禁用（`status:0`）观察。

## License

[MIT](LICENSE)
