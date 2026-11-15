# 开发导览与技术契约

从仓库根目录工作。本页说明改动落点、维护边界和验证入口；安装与使用见[项目说明](../../README.md)和[文档入口](../INDEX.md)。具体版本状态以版本说明和对应发行记录为准。

## 代码在哪里改

| 要改什么 | 先读与主要落点 |
|---|---|
| 页面、导航、全局反馈 | [`apps/web/src/main.tsx`](../../apps/web/src/main.tsx) 启动 React；[`App.tsx`](../../apps/web/src/App.tsx) 管导航、处理日志和跨页入口；[`useTaskOverview.ts`](../../apps/web/src/useTaskOverview.ts) 共享任务摘要、进行中任务和队列开关；页面在 `components/`。 |
| 接口请求、任务事件 | [`apps/web/src/api.ts`](../../apps/web/src/api.ts) 管普通 API 与共享任务 SSE 连接；领域类型在 `types.ts`。助手和仪表盘另有领域 API 模块。 |
| 任务状态和操作 | [`domain/tasks.py`](../../apps/api/src/document_pipeline_api/domain/tasks.py) 定义状态迁移；[`services/tasks.py`](../../apps/api/src/document_pipeline_api/services/tasks.py) 管业务变更、分组谓词和容量约束；[`task_presentation.py`](../../apps/api/src/document_pipeline_api/services/task_presentation.py) 生成待确认原因及动作；[`api/tasks.py`](../../apps/api/src/document_pipeline_api/api/tasks.py) 暴露 HTTP 协议。整队控制在 `services/queue_pause.py` 与 `api/queue_control.py`。 |
| 提取、当前数据与修订 | 从 `services/extraction.py`、`services/current_data.py`、`services/data_tables.py` 和 `services/task_exports.py` 开始；`models/` 是持久化模型，`schemas/` 是输入输出协议，结构变更用 `apps/api/migrations/`。 |
| 桌面启动与后台任务 | `apps/api/src/document_pipeline_api/launcher.py`、`desktop.py`、`supervisor.py` 与 `worker.py`；启动和停止前确认实例、数据目录及进程归属。 |
| 样式和交互基础件 | [`styles.css`](../../apps/web/src/styles.css) 管语义 token、主题与共享控件；组件样式与组件共同维护。折叠、焦点和拖拽先查 `CollapseChevron`、`useModalFocus`、`usePointerDrag`。 |
| 开发工具与打包 | 先查[脚本索引](../../scripts/INDEX.md)，再改 `scripts/`、`packaging/`。依赖由根 `package-lock.json` 与 `apps/api/uv.lock` 锁定。 |

产品源码位于 `apps/web` 与 `apps/api`；`scripts` 和 `packaging` 是配套工程入口。

## 维护边界

- 后端持久数据和允许的状态迁移决定业务事实；前端标签、分组与按钮不能代替后端校验。状态改动需一起检查服务、HTTP 协议和消费者。
- 区分单任务与整队操作、原读取设置与当前读取设置。重试读取设置不改变任务绑定的模型；取消保留数据，删除按确认框所述范围执行。
- `App` 持有 `useTaskOverview` 供全局顶栏使用；任务页为自己的时间范围读取同口径分页和摘要。分组、动作条件在 `taskPresentation.ts`，行展示在 `TaskListRow`，恢复操作在 `TaskRecoveryDialog`。多个 SSE 订阅者共用 `api.ts` 中的连接。普通切页恢复原筛选；顶栏的待确认/失败入口经导航守卫放行后清搜索并进入第一页，已挂载任务页通过入口序号和目标分组接收请求。
- 任务列表与 `summary.group_counts` 使用相同 SQL 分组谓词，先分组再分页。自动导出仍属进行中，需要人处理的事项才属待确认。按 `pending_action` 选择操作，目标表确认提交实际读到的 `pending_review_version`，不从中文提示推断或自行补零。
- 任务页默认最近 7 天，也可选最近一天（24 小时）或全部。`terminal_since` 仅按 `updated_at` 收起较早的已结束任务，未结束、失败和待确认仍保留；每轮列表与摘要复用同一 UTC 截止时间。原 `since` 接口语义不变，显示筛选不执行删除。
- `StatusDot` 统一小面积状态色和减少动态效果；`ServiceStatus` 独立展示程序、任务处理、提取模型与问知意的状态，历史任务的模型名称不能证明当前服务可用。`ValidationResults` 共用校验列表、定位、忽略与恢复。历史和提取结果保留原表格结构。
- 引导只在完成或明确跳过时记完成；连接设置通过显式请求展开目标区域，返回继续原步骤与所选方式。新选择连接方式时不能因旧模型已就绪而略过配置，设置页的未保存草稿由原导航守卫保护。
- 批量输出 `TaskRead` 时批量读取待确认元数据；多次读取同一事实时保持事务快照。批量重试如实处理 `skipped_tasks`，不把所有跳过都解释为容量不足。备份恢复保持有界分批和完整校验。
- 界面刷新保留筛选、草稿、焦点与滚动位置；读取或写入中收到更新须在操作结束后补读，旧响应不能覆盖新页面。样式整理需核对动态类名、导入顺序、主题与媒体条件，不能仅凭搜索无命中删除选择器。

## 开发与验证

开发启动参数以根 `package.json` 为准；首次准备环境见[项目说明](../../README.md)，工具职责见[脚本索引](../../scripts/INDEX.md)，自动检查见[CI 配置](../../.github/workflows/verify.yml)。已有根 `node_modules` 和 `apps/api/.venv` 时优先复用，不重复建环境。以下命令从仓库根执行，并替换为受影响的测试路径。

| 用途 | 命令 |
|---|---|
| 前端相关测试 | `npm.cmd run test:web -- src/components/TaskQueuePage.test.tsx` |
| 前端类型检查与构建 | `npm.cmd run build:web` |
| API 相关测试 | `apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/<相关测试>.py` |
| API 静态检查 | `apps/api/.venv/Scripts/python.exe -m ruff check apps/api/src apps/api/tests` |
| 产品文档检查 | `apps/api/.venv/Scripts/python.exe scripts/check-doc-governance.py` |

先查 Git 状态并保护既有修改，再明确触发问题、最小范围和应保留的行为。代码审查需实际读取控制流、调用者、事务边界、失败补偿与资源释放；盘点、哈希或测试通过不能替代审查。验证应覆盖正常、空白、加载、失败、长内容、窄窗、键盘与减少动态效果；数据改动同时核对持久结果和失败后的不变量。

测试使用隔离的合成数据与本机绑定服务。真实模型调用需明确配置与授权，普通测试命令不代表允许调用；凭据、运行数据和原始日志不得提交。改动涉及结构、入口或协议时更新本导览及相关文档。开发测试、代码审查、界面接受和发行验收分别记录，文件或命令存在不代表已执行。
