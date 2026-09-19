# ADR-0002：产物传递与可追溯性

- 状态：Accepted
- 日期：2026-09-19
- 关联任务：E2-B07-012
- 关联文件：`contracts/task.schema.json`、`contracts/examples/artifact.json`

## Context

四类服务之间需要交接大文件产物：实际/声明依赖图（`ACTUAL_GRAPH`、`DECLARED_GRAPH`）、MD/RD 报告（`ERROR_REPORT`）、构建日志（`LOG`）、Dockerfile、Git Patch。这些产物体积大（如大规模项目的完整构建日志、依赖图 JSON），不适合随每次 HTTP 响应内联传递：

- ADR-0001 已确定查询接口返回**产物引用**而非内容本身，响应保持轻量，避免把大日志塞进每次响应。
- `task.schema.json` 的 `$defs/artifact` 定义了产物引用的字段结构（`artifact_id`、`type`、`uri`、`media_type`、`producer_job_id`、`sha256`）；`contracts/examples/artifact.json` 说明了各字段的读取语义。
- 课程约束：E2 阶段只约定接口契约，不要求部署 API；配对组 A07/B07 通过契约样例协商数据交接；E12 必须证明另一组能实际读取产物文件。

产物字段的**含义**已由 `artifact.json` 定义，本 ADR 记录的是**交接方式与下游解析约定**，不重复样例内容。

## Decision

大文件一律**按引用交接**：Job 的输出只携带产物元数据与 `artifact://` URI，文件内容存放在平台产物存储，消费方按约定解析后读取。存储、解析与读取全部经平台侧统一入口，不依赖生产方的原始存储。

### URI 格式与解析

- 格式：`artifact://<pair>/<job>/<file>`。`pair` 标识配对组短名，`job` 标识生产任务短名（与 `producer_job_id` 对应），`file` 标识产物文件名。
- `artifact://` 前缀标识平台内部产物引用，不是任意 URL；消费方对 `artifact://` 之后的部分向平台产物存储发起解析。
- 解析入口由平台定义（下载接口或解析器），与 A07 协商确认；消费方持有统一解析方式，不直接访问生产方仓库或构建机。

### 下游解析约定

消费方按以下顺序解析与校验产物，任何一步失败即拒绝该产物并记录原因：

1. **来源校验**：确认 `producer_job_id` 指向真实存在的 Job，且其 `trace_id` 与本流程链路一致；否则视为无效来源。
2. **类型匹配**：校验 `artifact.type` 与生产者的 `job_type` 匹配——`ACTUAL_GRAPH`/`DECLARED_GRAPH`/`ERROR_REPORT` 由 `FULL_CHECK` 或 `INCREMENTAL_CHECK` 生产；`LOG`/Dockerfile 等由 `DRAFT` 生产。类型不匹配则拒绝。
3. **按 media_type 解析**：根据 `media_type` 选择解析器（`application/json` → 结构化解析；`text/plain` → 文本读取；`application/octet-stream` → 二进制处理）。未知 `media_type` 应拒绝并记录原因。
4. **完整性校验**：`sha256` 为可选字段；跨网络/跨组传递时推荐启用。消费方下载后计算 `sha256` 并与字段比对，不一致则拒绝并报告完整性错误。

### 基线可追溯性

`INCREMENTAL_CHECK` 的 `baseline` 引用历史实际依赖图（`actual_graph_uri`）时，必须同时携带基线 `commit` 与 `configuration_id`。下游据此校验基线提交与配置是否与当前任务匹配，不匹配视为无效基线并拒绝。

### 版本约定

产物引用的字段结构与语义属于共享契约的一部分，变更须同步 `task.schema.json` 的 `schema_version` 并保留已有字段含义；新增可选字段须得到消费者（A07）允许。

## Alternatives

- **按值传递**：在响应内直接携带完整产物内容。实现最简单，但构建日志、依赖图会显著膨胀响应体，违背 ADR-0001 的轻响应决策；同一产物被多处消费时重复传输，无法去重。
- **内容寻址存储（CAS）**：以内容哈希作为地址（如 IPFS 式），天然支持去重与完整性。但需要额外的基础设施与协调，超出 E2 课程规模；`sha256` 字段已覆盖完整性校验需求。
- **直连生产方原始存储**：消费方直接访问生产方的文件服务。耦合对方基础设施与网络，无统一解析入口，破坏可追溯性；E12 需要的是平台统一的读取证明。

## Consequences

### 正面

- 查询响应保持轻量，大文件只存储一次、多处引用。
- 可追溯：`producer_job_id` + `trace_id` 串联生产任务与整条平台流程，基线可校验。
- 完整性可验：`sha256` 支持跨组传递核验。
- 存储解耦：消费方不依赖生产方的存储细节，统一经平台解析。

### 代价与待办

- 需要平台产物存储与解析/下载入口，命名与解析机制需经 A07 确认（见 `artifact.json` 的 open_items）。
- 需要产物保留策略：生产者须在消费方取用前持久化产物，平台须定义清理/保留期。
- 需要维护 `media_type` 注册表，未知类型消费方应拒绝并记录。
- `sha256` 对超大文件计算有开销，仅在需要核验完整性的场景启用。