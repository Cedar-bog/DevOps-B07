# Backlog

| 编号       | 状态   | 任务           | 负责人 | 产物                                | 验收条件                                                                                                                                                                                                                                                                                                      |
|------------|------|--------------|-----|-----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| E2-B07-001 | DONE | 统一任务模型       | 郑悫  | `contracts/task.schema.json`      | 四类任务对象 DRAFT、FULL_CHECK、INCREMENTAL_CHECK、REPAIR 均可用同一 Schema 表达；公共字段 `schema_version`、`job_id`、`trace_id`、`job_type`、`status`、`execution`、`input`、`output`、`error` 有定义；`trace_id` 串联同一次平台流程；FULL_CHECK 与 INCREMENTAL_CHECK 的输入字段由 A 组定义，B 侧仅提供占位与公共约束 |
| E2-B07-002 | DONE | 状态与错误码约定     | 郑悫  | `examples/status_error.json`      | 状态 `QUEUED`、`RUNNING`、`SUCCEEDED`、`FAILED`、`TIMED_OUT`、`CANCELLED` 有定义；发现 MD 与工具失败语义分开，SUCCEEDED 也可报告 MD/RD；错误码 `ENV_3002`、`EXEC_4002`、`ANALYSIS_5001` 有定义并写入 `job.error`；枚举与编码命名需经 A07 确认                                                                      |
| E2-B07-003 | DONE | 产物访问样例       | 郑悫  | `examples/artifact.json`          | 说明 artifact URI 即 `artifact://` 前缀、media_type、producer_job_id、sha256 的读取方式；下游解析约定见 ADR 012                                                                                                                                                                                                    |
| E2-B07-004 | DONE | MD 报告消费约束    | 郑悫  | `examples/error_report.json`      | MISSING 记录必填字段 target、dependency、commit、位置满足 REPAIR 定位需求；只声明 B 消费侧约束，完整 ERROR_REPORT 格式由 A 组定义；URI 读取方式遵循 003 约定                                                                                                                                                       |
| E2-B07-005 | DONE | DRAFT 请求样例   | 刘雯  | `examples/draft_request.json`     | 输入含 `repository.url`、`commit`、`build.command`、`verify_command`、`max_iterations`、时间限制、幂等键；字段齐全且符合 schema；`job_id` 由服务端产生，不随请求传入                                                                                                                                               |
| E2-B07-006 | DONE | DRAFT 响应样例   | 刘雯  | `examples/draft_response.json`    | 输出含 `job_id`、Dockerfile、镜像引用、每轮日志、每轮修改与选择理由、最终构建和验证结果                                                                                                                                                                                                                            |
| E2-B07-007 | DONE | REPAIR 请求样例  | 姚睿哲 | `examples/repair_request.json`    | 输入含相同仓库与 commit、MD 报告 URI 且格式遵循 004 约定、Makefile、环境与构建验证命令、幂等键；`job_id` 由服务端产生，不随请求传入                                                                                                                                                                                |
| E2-B07-008 | DONE | REPAIR 响应样例  | 姚睿哲 | `examples/repair_response.json`   | 输出含 `job_id`、Git Patch、声明风格说明、构建测试重检结果；只消费 `MISSING` 类型；失败时拒绝候选并记录原因                                                                                                                                                                                                        |
| E2-B07-009 | DONE | 查询任务样例       | 姚睿哲 | `examples/job_query.json`         | `GET /v1/jobs/{job_id}` 返回当前状态与产物引用；运行中返回输入摘要而非大日志；`trace_id` 贯穿查询链路                                                                                                                                                                                                              |
| E2-B07-010 | DONE | 失败输入校验脚本     | 王师师 | `contracts/validate.py`<br/>`contracts/fixtures/negative/` | 运行 validate.py 通过四类请求与响应的正向校验；`job_type` 改为 `ABC` 被拒绝；删除增量任务 `baseline` 被拒绝，该用例由 A 组定义并提供样例，B 侧仅实现校验逻辑；能解释 MD 与工具失败的语义差异，见 002 约定                                                                                                               |
| E2-B07-011 | DONE | ADR：异步任务模型   | 郑悫  | `adr/0001-async-job-model.md`     | 记录 Context、Decision 即 POST 202 加 GET 查询、Alternatives、Consequences                                                                                                                                                                                                                                    |
| E2-B07-012 | DONE | ADR：产物传递与可追溯性 | 郑悫  | `adr/0002-artifact-passing.md`    | 记录大文件引用交接方式、下游解析约定；不重复样例内容                                                                                                                                                                                                                                                               |
| E2-B07-013 | 部分   | AI_USAGE.md  | 王师师 | `design/AI_USAGE.md`              | 记录 AI 提议、人工采纳修改拒绝理由、关联文件与验证结果                                                                                                                                                                                                                                                             |
| E2-B07-014 | 阻塞   | 交换契约样例       | 王师师 | `examples/` 下 A 侧样例               | 与 A07 完成双向样例交换：复制 A 组 FULL_CHECK / INCREMENTAL_CHECK 请求与响应样例至 `examples/`；四类任务样例齐全，双方能解释同一份请求与结果；A07 提供的失败用例（如删 baseline）可被 validate.py 校验                                                                                                                  |
| E2-B07-015 | 部分   | 交换决定与未决追踪    | 王师师 | `backlog.md` + `adr/0001` `0002` 更新 | 课堂配对三轮练习（环境基线→MD 报告→失败输入）的决定写入 ADR 011/012，未决工作同步 backlog                                                                                                                                                                                                                          |

状态说明：`DONE` 已完成；`阻塞` 依赖外部输入无法推进；`部分` 仅完成可独立推进的部分。

## A 侧待确认 / 定义清单

（对应 E2-B07-015「未决工作同步 backlog」。来源：各样例 `open_items`、`task.schema.json` 的 `defined_by` 占位、以及 `validate.py` 正向一致性运行的发现。课堂配对练习第三轮需与 A07 逐条过一遍，确认结果回写此处并同步 `adr/0001`、`adr/0002`。）

### 一、A 必须定义（B 定不了，缺失即无法交接）

| # | 待定义项 | 影响的 B 侧产物 | 当前状态 |
|---|----------|-----------------|----------|
| 1 | `FULL_CHECK` 输入字段全集（`configuration_id`、clean build 命令、项目根目录等） | `task.schema.json` 的 FULL_CHECK 分支 | 仅 `defined_by: "A07"` 占位 |
| 2 | `INCREMENTAL_CHECK` 输入字段全集 | `task.schema.json` 的 INCREMENTAL_CHECK 分支 | 仅 `defined_by: "A07"` 占位 |
| 3 | 完整 `ERROR_REPORT` 文件结构 | `examples/error_report.json`（B 仅声明消费侧约束） | B 侧必读字段已声明，完整结构待 A 提供 |
| 4 | `baseline` 语义与「删 baseline」失败用例样例 | `validate.py` 用例 `incremental_missing_baseline` | B 侧校验逻辑已实现并自测；该用例来源登记为 `owner: A07` |

### 二、A 必须确认（B 已起草，待点头）

| # | 待确认项 | 依据文件 | B 侧当前取值 |
|---|----------|----------|--------------|
| 5 | 状态枚举命名 | `examples/status_error.json` | `QUEUED` / `RUNNING` / `SUCCEEDED` / `FAILED` / `TIMED_OUT` / `CANCELLED` |
| 6 | 错误码命名与语义 | `examples/status_error.json` | `ENV_3002` / `EXEC_4002` / `ANALYSIS_5001` |
| 7 | `artifact://` 命名与解析机制、实际下载接口/解析器 | `examples/artifact.json`、`adr/0002` | 格式 `artifact://<pair>/<job>/<file>`；解析入口待定 |
| 8 | `ERROR_REPORT.position` 字段命名与必填性 | `examples/error_report.json` | B 至少需要 `file` + `line` 才能定位 Makefile 声明 |
| 9 | `detector` 枚举命名 | `examples/error_report.json` | `BUILDCHECKER` / `ECHECKER` / `INSTRUCTOR_ORACLE` |
| 10 | `REDUNDANT` 记录的消费策略 | `examples/error_report.json` | B 主张忽略（不属修复范围） |
| 11 | 严格性约定：B 侧以 `additionalProperties: false` 拒绝新增字段 | `task.schema.json` | 遵循课程「严格 Schema 拒绝新增字段」；新增可选字段须经消费者允许 |

### 三、A 需交付的交接物

| # | 交接物 | 用途 | 当前状态 |
|---|--------|------|----------|
| 12 | A07 的 FULL_CHECK / INCREMENTAL_CHECK 请求与响应样例文件 | 完成 E2-B07-014，补齐四类任务样例 | **未获取**。README 中 A07 仓库地址仍为 `_TODO_` |
| 13 | A07 提供的失败用例（如删 baseline） | 交由 `validate.py` 校验 | **未获取**。B 侧接收端已就绪（见第四节） |
| 14 | A07 仓库地址 | 双向样例交换与 E12 交叉读取产物 | **未获取**，待填入 README |

### 四、B 侧为实现上述交接已就绪的部分

- **接收端零改码**：`validate.py` 递归发现 `examples/*.json`，A 侧样例文件落地后自动纳入校验，无需修改脚本。
- **失败用例数据化**：`contracts/fixtures/negative/` 下的用例记录「来源指针 + 变异操作」而非样例副本，A07 的用例可作为一条 fixture 直接加入，B 侧不改代码。样例见 `incremental_missing_baseline.json`（`owner: A07`）。
- **防静默漏判**：任一 `examples/*.json` 未贡献任何可识别 payload 即 FAIL，避免 A 侧样例落地后被静默跳过。
- **四类覆盖现状**：DRAFT、REPAIR 有独立请求/响应样例；FULL_CHECK 与 INCREMENTAL_CHECK 目前取自 `status_error.json` 内嵌的 Job 实例（FULL_CHECK 4 条、INCREMENTAL_CHECK 1 条）与 `job_query.json`（FULL_CHECK 2 条）。第 12 项到位后转为独立文件。

### 五、`validate.py` 正向一致性发现（新增未决项）

| # | 发现 | 影响 | 建议 |
|---|------|------|------|
| 15 | `task.schema.json` 的 `required` 含 `input`，但 `job_query.json` 的查询响应体不带 `input`（运行中用 `input_summary`，终态两者皆无），Schema 与查询视图不一致 | `task.schema.json`（与 A07 共享的契约）与 `examples/job_query.json` | 二选一：收紧样例补上 `input`，或在 Schema 中为查询视图补 `$defs/job_view`。当前 `validate.py` 以显式规则 `QUERY_VIEWS` 放宽（依据 ADR-0001），并保持「未登记且缺 `input` 的 Job 仍 FAIL」 |