# Dify 接入说明

## 1. 将现有学生对话接到已发布应用

目标应用是 `https://udify.app/chat/VIGcPb4Rarf2iTM7`。这个地址是聊天分享页，不能当作 API 地址，也不能把链接末尾的分享码当成 API Key。需要在**该应用**的「访问 API / API Access」页面获取应用密钥和 API 服务地址。

当前适配依据为 2026-11-14 导出的 `DormCare Student Assistant (1).ifpkg`：应用模式为 `advanced-chat`，开始节点没有必填变量；文件输入开启，每轮最多 3 张图片；两个 LLM 节点的视觉输入均为 `sys.files`。此次只修改 DormCare 接入代码，没有修改或发布线上 Dify 工作流。

在项目根目录的 `.env:1` 中配置（首次克隆可从 `.env.example:1` 复制）：

```dotenv
DIFY_API_KEY=app-替换为该应用的API密钥
DIFY_API_BASE_URL=https://api.dify.ai/v1
```

保存后运行 `python3 app.py`，无需再执行 `export`。

使用 Dify Cloud 时通常使用上述 API 根地址；以应用控制台显示的地址为准。服务端自动追加 `/chat-messages`，不要填写分享页地址或重复填写 `/chat-messages`。自部署实例使用自己的 HTTPS API 根地址。本地测试允许 `localhost`、`127.0.0.1` 或 `::1` 的 HTTP 地址；请求不跟随重定向，避免将密钥发送到其他地址。

启动后，用学生账号进入原有的「我要报修」页面即可，无需嵌入另一个聊天窗口，也不需要在浏览器中配置密钥。程序在读取配置前自动加载与 `app.py:1` 同目录的 `.env:1`，不依赖当前工作目录；已存在的进程环境变量优先，不会被文件覆盖。支持单行 `KEY=VALUE`、单/双引号、注释和可选的 `export` 前缀，不执行 shell 命令或变量展开。修改文件后需重启服务。

`.env:1` 和其他 `.env.*` 本地配置已被 Git 与 Docker 构建上下文忽略，避免提交密钥或打包进镜像；仅无密钥的 `.env.example:1` 可提交。Docker 部署可通过 `docker run --env-file .env ...` 注入同一份配置。

接入链路：

```text
学生报修页面
  → POST /api/agent/message（原有登录和会话归属校验）
  → 有图片时：Dify POST /files/upload（服务端上传真实文件）
  → Dify POST /chat-messages（files 引用 upload_file_id）
  → 将 answer 转成原有 v2 受理结果 / 草稿
  → 学生核对并选择上门时间
  → POST /api/agent/confirm（原有身份、地址和附件校验）
  → 创建工单并进入原有排班流程
```

`dify_client.py:1` 实现标准库客户端；`intake_agent.py:1` 将工作流回复适配为原有受理契约。`conversation_id` 与回复语言存入服务端报修会话，刷新页面后继续使用，切换语言时建立新会话，不向前端暴露远端会话 ID。续接会话的 `query` 只发送当前轮次，不重复拼接之前的所有描述；新建会话或切换语言时补带已有学生描述。

### 环境变量

| 变量 | 用途 |
| --- | --- |
| `DIFY_API_KEY` | DormCare 调用 Dify 的应用密钥；非空时优先使用 Dify 处理学生 AI 对话 |
| `DIFY_API_BASE_URL` | API 根地址，默认 `https://api.dify.ai/v1` |
| `DIFY_TIMEOUT_SECONDS` | 请求超时秒数，默认 120，范围大于 0 且不超过 300 |
| `DIFY_INPUTS_JSON` | JSON 对象，补充工作流开始节点的自定义输入变量，默认 `{}` |
| `DORMCARE_DIFY_API_KEY` | **反向调用**密钥，仅用于 Dify 调用 DormCare 的 `/api/dify/workflow`；不是 Dify 应用密钥 |

两个方向的密钥不可混用。密钥只放服务端环境中，不放前端、Git 或分享链接中。未配置 `DIFY_API_KEY` 时，原有模型 / 规则逻辑保持不变。`/api/health` 的 `dify_enabled` 仅表示已配置密钥，不代表已通过远端连通性校验。

### 工作流输入和输出

每次调用自动生成以下 `inputs`：

- `complaint`：当前会话最近的学生描述，最多 2000 字符。
- `route`：`auto` / `self_help` / `direct_report`，按钮明确选择自助或报修时分别发送 `self_help` / `direct_report`；普通对话为 `auto`，不使用本地关键词替 Dify 决定意图。
- `lang`：`zh` / `en`；`resolved_after_check`：转报修时为 `no`，点击「已解决」为 `yes`，否则为 `unknown`。
- `location_json`：登录账号的书院、楼栋与房间；`date` / `slots_json`：已保存草稿中的日期与时间窗，没有选择时留空。

这些业务字段由服务端覆盖，不能通过 `DIFY_INPUTS_JSON` 冒充其他学生的位置。其他自定义必填变量按线上工作流的实际变量名配置。例如，工作流需要回调本项目时：

```dotenv
DORMCARE_DIFY_API_KEY=一段足够长的随机回调密钥
DIFY_INPUTS_JSON='{"dormcare_base_url":"https://你的DormCare公网域名","dormcare_api_key":"与DORMCARE_DIFY_API_KEY相同的值","emergency_phone":"学校实际紧急联系电话"}'
```

若线上工作流没有 HTTP 回调节点，则不需要上述回调参数。Dify Cloud 无法访问你电脑上的 `localhost:3000`，回调地址必须能被 Dify 服务端访问。

对 Chatflow，请在处理**当前轮次**的节点中读取 `sys.query`，并启用所需的会话记忆。Dify 会话建立后的开始节点输入不是逐轮可变的状态；后续转报修意图、语言要求和近期故障描述也会放进 `query`。不能仅依赖首次的 `complaint` / `route` 来处理所有后续消息。本接入调用的是 Chat/Chatflow API，不会调用 `/workflows/run`。

按钮不是本地强制切换工作流。服务端将按钮转换为一条学生消息，写入历史并发送到同一个 Dify 会话：

| 按钮 | 发送的当前轮次意图 |
| --- | --- |
| 仍需师傅，生成工单 | 问题仍未解决，仍需师傅上门，请生成报修工单草稿。 |
| 直接报修 | 我想直接报修，请安排师傅上门并生成报修工单草稿。 |
| 自助排查 | 我想先尝试安全的自助排查。 |
| 已解决，无需报修 | 问题已经解决，无需师傅上门，不需要生成工单。 |

启用 Dify 后，后续追问、自助建议、结束受理或准备草稿均按工作流回复处理；点击报修按钮也不会覆盖 Dify 的追问。按钮等待期间显示加载状态并防止重复发送。

兼容以下回复形式：

- 普通文本：原样显示回复，不凭按钮动作自动伪造草稿或声称已创建工单。如需打开确认表单，Dify 应返回结构化草稿状态。
- Markdown 回复：当前回答和助手历史消息支持段落、标题、列表、加粗、行内代码、HTTP(S) 链接与 `![说明](图片地址)`。参考图自适应页面宽度，可点击打开原图；保留图片 URL 的签名参数，不代理或改写 Dify 文件地址。原始 HTML 会转义，非 HTTP(S) 链接不会变成可点击链接或图片；用户输入仍按纯文本显示。签名链接失效时需由 Dify 重新提供可访问的图片地址。
- JSON 或带说明文字 / Markdown 代码块的 JSON：识别 `reply` / `message`、`route`、`draft` / `ticket_payload`、`knowledge.steps`、`emergency` 和 `safety_warning`。`stage` / `state` 可返回 `follow_up`、`advice`、`no_solution`、`route_choice`、`draft` 或 `resolved`；`follow_up` 或 `complete: false` 保持未完成状态，保留 Dify 的追问。`stage: draft`、`complete: true` 或 `route: direct_report` 在非追问、非显式未完成时进入草稿阶段，仅校验草稿字段合法性，不再用原始描述字数阻拦。
- 最新工作流的扁平 JSON：识别 `status: "draft"`、`title`、`description`、`category`、`urgency`、`is_public_area`、`college`、`building` 和 `location`。`category` 按系统维修目录中的中文名称精确映射成 ID，`urgency` 将“紧急 / 较急 / 一般 / 不急”映射为 `urgent / high / normal / low`。这是转换 AI 输出值，不是再用故障关键词分类；未知类别为 `other`，未知紧急度为 `normal`，缺少描述则不开放确认。
- 私人宿舍的身份和地址仍以登录账号为准，附件 ID 仍以服务端会话为准。`is_public_area: false` 时使用账号宿舍；`is_public_area: true` 时自动勾选“公共区域报修”，书院和楼栋默认使用账号资料，不采用工作流返回的 `college`、`building`，仅将 `location` 预填至“公共区域位置”。学生仍可修改公共区域的书院、楼栋和位置；恢复已保存草稿时保留其选择，实际提交保留原有地址校验。

最新包中 `Template 2` 的实际输出结构如下，不需要把工作流改成旧版嵌套 `draft`：

```json
{
  "status": "draft",
  "title": "宿舍照明故障",
  "category": "灯具 / 照明",
  "urgency": "一般",
  "description": "宿舍灯具无法点亮，需要师傅检修。",
  "is_public_area": false,
  "college": "",
  "building": "",
  "location": ""
}
```

包内“请问是哪件设施出了什么问题？可以简单描述一下现象。”及“好的，问题解决了就好！如果之后还有其他设施问题，随时来找我。”两个固定模板分别适配为追问、结束状态；其余咨询文本保持原样。旧版结构化 `stage` / `state` 契约继续兼容。

本地规则识别到紧急风险时直接返回安全警告和紧急草稿，不等待外部工作流，该路径不会上传图片。无图片的远端故障仍可回退原有模型或本地规则；带图片的请求一旦上传或对话失败，明确返回 `source: "dify_error"`、`ready: false`，不以本地关键词草稿假装完成图片分析。不把密钥和远端错误正文返回给学生。

### 图片传递

1. 选择图片后，浏览器经 `/api/uploads` 将 JPG/PNG 存到本系统，维持登录身份、文件签名、每张 5 MB 的校验。
2. 发起 AI 对话时，服务端再次检查附件归属与用途，读取真实图片，以 multipart 请求上传 Dify `/files/upload`；不使用需要登录的本地预览 URL，也不要求 Dify 访问 `localhost`。
3. 上传使用与对话相同的 `user: dormcare-<账号ID>`；取得文件 ID 后，将 `{"type":"image","transfer_method":"local_file","upload_file_id":"..."}` 放进 `/chat-messages` 的 `files` 数组，供工作流的 `sys.files` 使用。
4. 同一会话缓存已上传的文件 ID；后续消息继续引用当前附件，刷新页面无需重新上传。缓存按应用密钥、API 地址和账号隔离，不把远端文件 ID 返回浏览器。移除图片后，不再附加到后续消息，但不会抹除 Dify 已有的会话记忆。
5. 页面从 `/api/config` 获取 AI 图片数量上限；当前工作流为 3 张，前后端均拒绝超量，不静默丢弃第 4、5 张。原手工报修、完工证据接口的 5 张上限不变。
6. 对话成功且携带图片时，返回 `image_delivery: {"status":"sent","count":N}`，页面显示“本轮已发送 N 张图片至 Dify”；失败时明确提示，图片仍保留在本系统供重试或手工报修。

该包中的参数提取器 `Parameter Extractor` 没有开启视觉，图片理解发生在两个视觉 LLM 节点；上传成功不代表每个分支都会直接分析图片。纯图片直接进入报修提取分支时，仍可能需要学生补充故障现象，不能只凭静态图片认定功能损坏。

上线前应告知使用者：对话文字、账号位置、服务端用户标识和选中的图片会交由 Dify 处理。2026-11-14 已用无个人信息的程序生成测试图实测上传与对话，线上工作流正确识别“左侧红色圆形、右侧蓝色正方形”；测试未修改 Dify 配置、未创建工单。

### 验证

```bash
python3 tests/test_dify.py -v
node --test tests/test_report_actions.js
python3 tests/test_multimodal.py
```

第一组覆盖请求鉴权、会话续接、语言切换、JSON 草稿转换、紧急兜底、错误降级，以及模拟 Dify 的完整「登录 → 对话 → 确认工单」链路，不消耗真实模型额度。真实线上联调仍需要该应用的有效 API Key 和必填变量配置。

## 2. 本地工作流模板（可选）

如果需要从本仓库模板重新搭建，可导入 `dify/dormcare-repair-intake.ifpkg`。它是真正的 ZIP 压缩包，包内固定包含 `manifest.yaml` 和 `app.yaml`。`dify/app.yaml:1` 是未压缩的源文件，便于修改后重新打包；它不代表线上分享应用的当前节点配置。

工作流图已经展开为：

1. 用户描述故障
2. 大模型识别是否为紧急情况
3. 紧急 → 返回维修电话，结束
4. 非紧急 → 自助排查还是直接报修
5. 自助排查 → 调用知识库 → 输出排查建议
6. 判断是否解决
7. 已解决 → 结束
8. 未解决 → 补齐报修信息
9. 生成报修草稿，返回接口 JSON

导入后在 LLM 节点选择实际模型，并将 `DormCare 地址`、`DormCare API Key` 配成工作流变量。`DormCare API Key` 不要写进前端代码或公开仓库。

## 3. 启动 DormCare 反向回调接口

```bash
export DORMCARE_DIFY_API_KEY='replace-with-a-long-random-key'
python app.py
```

公网部署时，将 `DormCare 地址` 设置为实际的 HTTPS 地址，例如 `https://dormcare.example.com`。

## 4. 反向回调接口契约

`POST /api/dify/workflow` 支持 `X-DormCare-Key` 或 `Authorization: Bearer ...`。请求示例：

```json
{
  "complaint": "房间插座冒烟并且有焦味",
  "route": "auto",
  "lang": "zh",
  "location": {
    "college": "逸夫书院",
    "block": "东座 1 栋",
    "dorm": "A-301",
    "public_area": false
  },
  "date": "2026-11-06",
  "slots": [[9, 12]]
}
```

接口只生成 JSON，不会自动落库创建工单。返回的 `ticket_payload` 可直接嵌入现有项目的 `/api/tickets` 或 `/api/agent/confirm` 流程；真正创建工单仍应由已登录学生会话完成，以保留项目的身份和房间权限校验。

服务端会再次执行紧急性兜底：即使 Dify 的 LLM 判断为非紧急，只要规则检测到漏电、触电、火花、冒烟、燃气、进水接近插座、人员被困等情况，也会强制返回 `route: direct_report` 和 `priority: urgent`。
