# API 与 MCP 授权边界

## 产品决定

本地网页继续使用内部 `/api/v1`，不要求普通用户登录或选择权限。

外部程序只使用 `/api/integration/v1`，默认关闭。用户配置密钥后才开放：

- 读密钥：查询任务、结果、表和分 Sheet，下载托管原件并读取修订历史。
- 写密钥：拥有读能力，并可上传文件、修改事实行。
- 已发出的旧密钥保留此前的文件访问权限与原表范围。重新生成后按读写两级授权，原密钥随即失效。实际能力以 `GET /capabilities` 为准。
- `GET /tasks` 默认返回 100 条，`limit` 可设为 1–500，`offset` 从 0 开始，并支持 `status` 和 `search`。响应保留归档状态，不返回本机路径或文件系统标识。
- `GET /tasks/{id}/file` 下载托管原件。修订目录和指定版本分别使用 `GET /tasks/{id}/revisions` 与 `GET /tasks/{id}/revisions/{revision_id}`，使用 `page`、`page_size` 分页。
- `GET /tables/{id}/revisions` 分页读取表内手工或导入记录历史，可传 `row_id`。

清空或更换密钥即可撤销 HTTP API 访问。

两把密钥都必须至少 32 个字符，且不能相同；错误配置会阻止服务启动并明确说明原因。

## 为什么不用一把万能密钥

自然语言查询、报表和 ERP 读取通常只需要读。如果它们拿到万能密钥，一次错误工具调用就可能修改事实数据。读写分离让“能看”不自动等于“能改”。

## 为什么不直接保护现有 `/api/v1`

`/api/v1` 是本机网页与后端之间的内部接口。给它强制加外部密钥，会把集成复杂度暴露给普通用户，也容易让浏览器长期保存高权限密钥。

独立集成前缀可以：

- 明确哪些能力承诺给外部系统。
- 独立做兼容版本和速率限制。
- 不让内部页面接口的变化意外扩大外部权限。

## 权限矩阵

| 能力 | 读密钥 | 写密钥 |
|---|---:|---:|
| 查询能力声明 | 是 | 是 |
| 查询任务与结果 | 是 | 是 |
| 查询原始表与分 Sheet | 是 | 是 |
| 下载原件、读取修订历史 | 是 | 是 |
| 上传文件创建任务 | 否 | 是 |
| 修改事实行 | 否 | 是 |
| 修改模板 | 首版否 | 首版否 |
| 删除任务或数据 | 首版否 | 首版否 |

## 不可绕过的业务规则

- 外部行修改同样必须提交 `expected_version`。
- 外部修改同样写入 `data_row_revisions`。
- API 和 MCP 调用服务层，不直接写 ORM 对象或执行任意 SQL。
- MCP 的“自然语言查表”只能调用结构化只读查询，不接收任意 SQL。
- 写能力不因客户端声称自己是 ERP 或 MCP 而自动授予。

## v0.5.0 上传重复确认契约（2026-09-28）

内部 `POST /api/v1/tasks`、外部 `POST /api/integration/v1/tasks` 的表单，以及本机 `POST /api/v1/tasks/native-import` JSON 和 MCP `create_task_from_file`，统一接受 `duplicate_policy`，取值 `reject`（默认）或 `allow`。当前开发版默认不再静默创建重复任务；旧客户端收到重复拒绝后应让用户决定，不应自动用 `allow` 重试。

`template_mode` 只接受 `smart`、`manual` 与兼容的 `invoice`、`delivery`；`manual` 必须指定 `template_id`。未知模式或缺模板的手动请求返回422，避免实际走智能提取却形成不同判重键。内置模式与显式指定同一内置模板使用相同的规范提交意图。

同 SHA-256、同原始提交方式（手动模板 ID/版本或 smart）、同提交目标表，且已有任务未失败/取消或已经保留提取结果/当前事实时，HTTP 返回 409，`detail` 包含 `code: "duplicate_upload"`、`message`、`duplicate_task_id`、`duplicate_status`。拒绝在共享服务的原子数据库插入条件中完成；不新增任务、不发布队列、不遗留上传副本。MCP 以工具错误报告同一拒绝；明确确认再次提取后可传 `allow`，新增任务仍保留 `duplicate_of_task_id`。不同模板/版本、不同目标表，以及尚无结果的失败或取消任务重传允许直接创建；失败任务仍可使用原有 retry。

失败或取消后的直接重传仅限尚无提取结果/当前事实的任务。已提取但待复核的任务被取消后仍保留事实，因此同输入再次上传仍返回409，需要明确确认，以免重复生成数据。

本机票据遇重复也会被消耗。409 额外返回 `retry_token`，它只继承已验证原件的指纹、数据世代和原过期时间，不返回或接受原件路径。确认时用新票据及 `allow`；旧票据不可复用。取消后新票据自然过期，文件变动或数据世代改变后必须重新选择。

0046 单列迁移保存不可变 `upload_intent_json`；模板匹配、重选、重试不覆写它。旧任务无法恢复已经丢失的原始选择：smart 按保留的 mode 和目标表判断，手动按当前保留的模板/版本/目标表判断。历史任务被删除后不再参与判重；这是任务级误操作防护，不是对所有历史数据的永久去重。smart 池或模型设置变化仍会提醒，用户可明确再次提取。

## 当前威胁模型

首版后端只绑定 `127.0.0.1`，目标是单机单用户。读写密钥用于约束正常的 ERP、脚本和 AI 客户端，尤其防止只读客户端误调用写能力。

它不声称防御已经能读取本机文件、环境变量或数据库的恶意程序。这样的程序已经拥有与当前用户相同的操作系统权限。

在加入以下任一能力前，必须重新设计认证，而不是直接把监听地址改成 `0.0.0.0`：

- 局域网访问。
- 云端部署。
- 多用户。
- 跨部门数据隔离。

届时内部 `/api/v1` 也必须受正式会话认证保护，并评估 OAuth 2.1、用户身份、组织和审计主体。

## MCP 1.0 能力权限（已实现）

当前实例按各项权限注册工具，除能力查询外默认关闭：

| 能力组 | 默认 | 工具范围 |
|---|---:|---|
| 读取任务、模板、文件结果、数据仓库 | 分别关闭 | 各组分别注册，表工具支持筛选、分页和聚合 |
| 修订历史 | 关闭 | `get_file_revisions` 查询文件版本及修改，`get_independent_row_revisions` 查询手工或导入记录历史 |
| 读取原件 | 关闭 | `read_original_page` 分段读文字，按页或帧读 PDF 和图像 |
| 事实修改 | 关闭 | 按 `expected_version` 修改事实行并写审计 |
| 任务控制 | 关闭 | 暂停、恢复、重试、取消、选择模板 |
| 文件访问 | 关闭 | 从允许目录创建任务；导出 CSV/JSON 还需“读取数据仓库”权限 |

MCP 权限与 HTTP API 密钥独立。已连接的客户端在权限变更后需重新连接。目录访问用于导入和导出，需配置允许根目录。按页读取托管原件使用独立的原件权限。指定表范围同时约束文件结果、修订和原件，通过合并表的授权不会获得其原表的整份文件权限。MCP 目前通过本机 `stdio` 连接。

环境变量：

- `DOCUMENT_PIPELINE_MCP_WRITE_ENABLED=1`
- `DOCUMENT_PIPELINE_MCP_TASK_READ_ENABLED=1`
- `DOCUMENT_PIPELINE_MCP_TEMPLATE_READ_ENABLED=1`
- `DOCUMENT_PIPELINE_MCP_REVISION_READ_ENABLED=1`
- `DOCUMENT_PIPELINE_MCP_ORIGINAL_READ_ENABLED=1`
- `DOCUMENT_PIPELINE_MCP_RESULT_READ_ENABLED=0`（规划实例不读取提取正文）
- `DOCUMENT_PIPELINE_MCP_DATA_READ_ENABLED=0`（规划实例不读取事实行与聚合）
- `DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED=1`
- `DOCUMENT_PIPELINE_MCP_FILE_ACCESS_ENABLED=1`
- `DOCUMENT_PIPELINE_MCP_FILE_ROOTS=目录1;目录2`（Windows 使用系统路径分隔符）

未启用的工具不会出现在 `tools/list` 中。`get_capabilities` 返回当前实例实际能力，方便上层强模型在规划前确认边界。

## 模型协作的后续权限

当前 HTTP 密钥和 MCP 开关适用于本机外部集成。以下是未来模型协作的提案：

- `raw_file.read`：读取原文件或页面图像；
- `extracted_text.read`：读取 OCR/解析全文；
- `redacted_summary.read`：读取脱敏摘要；
- `schema.read`、`task_metadata.read`：读取模板和任务元数据；
- `task.create`、`task.assign`、`rule.run`：创建、分配和要求执行本地任务；
- `result.propose`：提交候选修改；
- `fact.commit`：把候选提交为事实；
- `export.create`、`data.delete`、`network.send`：导出、删除和向外发送。

默认策略是：本地模型在用户授权工作区内可读取原文；云端模型默认可以读取模板、状态和脱敏错误摘要并分配本地任务，但没有 `raw_file.read`、`extracted_text.read` 或 `fact.commit`。如果用户要求云端模型亲自处理原文，系统必须展示数据范围、目标提供商和用途，并记录一次明确的外发授权。

模型输出与事实提交必须分离。模型最多产生候选修改，确定性规则、版本检查和人工确认决定能否提交。策略执行器必须在工具调用前拒绝越权，而不是把全部工具暴露后只依赖提示词。

当前应用配置适用于同一工作区的 MCP 连接。未来开放局域网、云部署或组织协作时，需要独立身份、授权范围和凭据管理。

## MCP 实施决定

官方 Python SDK 当前存在稳定 v1 与 v2 迁移窗口。先冻结工具契约和权限测试，再锁定已验证的正式版本：

- 默认 MCP 实例只注册 `get_capabilities`。
- 事实写、任务控制和文件访问工具不是“调用时再提示”，而是在未显式启用时根本不注册。
- 安装版通过接口页生成本机 `stdio` 配置，权限以工作区设置为准。HTTP MCP 属于后续设想。
- MCP 只是协议适配器，不拥有独立业务逻辑。

当前锁定 `mcp==1.28.1`。v2 仍为 beta，且 `2026-07-28` 协议仍是候选版，因此不追预发布版本。

### 本仓库验证启动方式

MCP 使用 `stdio`，不是常驻 HTTP 端口：客户端连接时启动子进程，断开后子进程退出。必须使用项目锁定的 API 环境，不能直接调用系统 Python：

```powershell
uv --cache-dir .uv-cache run --project apps/api python -m document_pipeline_api.mcp_server
```

协议级回归使用官方 MCP 客户端真实启动上述子进程并执行 `initialize → tools/list → tools/call`：

```powershell
uv --cache-dir .uv-cache run --project apps/api pytest apps/api/tests/test_mcp_e2e.py -vv -s
```

该回归不是函数级 mock：只读实例实际查询任务、事实表并聚合；授权实例实际修改带版本的事实行、暂停任务、从白名单文件创建任务，并验证目录越界被拒绝。若系统 Python 报缺少 SQLAlchemy，只说明启动命令绕过了项目环境，不代表发布运行时缺依赖。

官方依据：

- https://pypi.org/project/mcp/
- https://github.com/modelcontextprotocol/python-sdk
- https://github.com/modelcontextprotocol/modelcontextprotocol/releases

2026-09-12：读取任务权限新增 `get_task` 单任务查询和 `get_task_diagnostics` 有界脱敏诊断；`get_capabilities.table_export` 返回实际导出可用性。
