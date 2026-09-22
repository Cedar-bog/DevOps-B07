# B07-ADR-0001：异步任务模型

- 状态：Accepted（B 单方决策）
- 命名空间：`B07-ADR-*` = B07 单方决策；跨组共同决策见 `PAIR07-ADR-*`（约定见 `PAIR07-ADR-001`）
- 日期：2026-09-19
- 关联任务：E2-B07-011
- 关联文件：`contracts/task.schema.json`、`contracts/examples/status_error.json`

## Context

平台需要承接四类任务：DRAFT（生成可构建环境）、FULL_CHECK（全量依赖检测，A07）、INCREMENTAL_CHECK（跨提交增量检测，A07）、REPAIR（修复缺失依赖）。四类任务均为耗时操作，无法在单次 HTTP 请求生命周期内完成：

- DRAFT 需迭代调用 LLM 生成并实际执行 Docker 构建：论文实测单项目生成耗时中位数 33.78s，最长 19.57 分钟（tinycc）。
- FULL_CHECK 依赖干净构建（clean build）以获取实际依赖图，大规模项目可达数小时。
- REPAIR 需应用补丁后重新构建、测试并重检。

课程约束：E2 阶段只约定接口契约，不要求部署 API；配对组 A07/B07 通过契约样例协商数据交接；耗时任务应"先受理，再后台执行"，另一方（配对组）需要能够查询进度并取回结果。同时应避免把大日志塞进每次响应，并用 `trace_id` 串联同一次平台流程。

## Decision

采用**异步 Job 模型**：客户端提交任务后立即得到受理结果，后台异步执行，另一方通过查询接口获取状态与产物。

- **创建**：`POST /v1/{job-type}-jobs`（如 `/v1/dockerfile-jobs`、`/v1/full-check-jobs`、`/v1/incremental-check-jobs`、`/v1/repair-jobs`）返回 `HTTP 202 Accepted`，响应体为 `{ "job_id": ..., "status": "QUEUED" }`。
- **job_id 由服务端生成**，不随请求传入；请求携带输入与幂等键（`idempotency_key`），幂等键用于去重。
- **查询**：`GET /v1/jobs/{job_id}` 返回完整 Job（含 `input`）与产物引用（`artifact://` URI）；运行中 `output` 为空以省略大日志与产物内容。
- **状态机**：`QUEUED` → `RUNNING` → `SUCCEEDED | FAILED | TIMED_OUT | CANCELLED`；`SUCCEEDED` 也可能报告 MD/RD 发现。见 `contracts/examples/status_error.json` 的 transition 定义。
- **串联**：同一次平台流程的所有任务携带同一 `trace_id`（DRAFT → FULL_CHECK / INCREMENTAL_CHECK → REPAIR），查询链路贯穿该标识。
- **公共字段**：`schema_version`、`job_id`、`trace_id`、`job_type`、`status`、`execution`、`input`、`output`、`error` 由 `contracts/task.schema.json` 统一约束；服务专有输入由各 `job_type` 分支约束。

## Alternatives

- **同步等待**：请求持有连接直至任务完成。实现最简单，但客户端与执行强耦合；长构建极易触发 HTTP/网关超时，且调用方必须一直在线，无法支撑配对组间异步协作。
- **WebSocket / 服务端推送**：实时性好，可推送进度与日志。但需要长连接管理与额外的基础设施，且客户端（CI 场景）不易保持长连接，超出 E2 接口约定范围。
- **Webhook 回调**：任务完成时主动回调。需为配对组额外暴露回调端点与鉴权，协商与部署成本高；失败重试语义也更复杂。

## Consequences

### 正面

- 客户端与执行解耦，长任务不受 HTTP 生命周期限制。
- 配对组可随时查询进度并取回结果，支持异步协作。
- `idempotency_key` 支持幂等去重，重复提交不会重复执行。
- 产物通过 `artifact://` 引用传递，查询响应保持轻量（运行中 `output` 为空），避免大日志随每次响应传输。
- 查询响应体即完整 Job，与 `task.schema.json` 的 `required.input` 一致，无需查询视图特例（A07 `A07:ADR-002`，文件 `ADR-002-job-query-response.md` 确认）。
- 支持超时（`TIMED_OUT`）与重试语义，错误码集中表达（`ENV_3002`/`EXEC_4002`/`ANALYSIS_5001`）。

### 代价与待办

- 需要任务存储与查询层：job 元数据持久化、按 `job_id` 查询。
- 需要 job 生命周期管理：QUEUED/RUNNING 中间态清理、终态保留策略。
- 客户端需轮询查询接口，可能引入轮询延迟；可后续用 `trace_id` 聚合查询优化。
- 状态枚举与错误码命名需经 A07 确认（见 `status_error.json` 的 open_items）。