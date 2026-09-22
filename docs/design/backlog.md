# Backlog

| 编号       | 状态   | 任务           | 负责人 | 产物                                | 验收条件                                                                                                                                                                                                                                                                                                      |
|------------|------|--------------|-----|-----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| E2-B07-001 | DONE | 统一任务模型       | 郑悫  | `contracts/task.schema.json`      | 四类任务对象 DRAFT、FULL_CHECK、INCREMENTAL_CHECK、REPAIR 均可用同一 Schema 表达；公共字段 `schema_version`、`job_id`、`trace_id`、`job_type`、`status`、`execution`、`input`、`output`、`error` 有定义；`trace_id` 串联同一次平台流程；FULL_CHECK 与 INCREMENTAL_CHECK 的输入字段由 A 组定义，B 侧仅提供占位与公共约束 |
| E2-B07-002 | DONE | 状态与错误码约定     | 郑悫  | `examples/status_error.json`      | 状态 `QUEUED`、`RUNNING`、`SUCCEEDED`、`FAILED`、`TIMED_OUT`、`CANCELLED` 有定义；发现 MD 与工具失败语义分开，SUCCEEDED 也可报告 MD/RD；错误码 `ENV_3002`、`EXEC_4002`、`ANALYSIS_5001` 有定义并写入 `job.error`；枚举与编码命名需经 A07 确认                                                                      |
| E2-B07-003 | DONE | 产物访问样例       | 郑悫  | `examples/artifact.json`          | 说明 artifact URI 即 `artifact://` 前缀、media_type、producer_job_id、sha256 的读取方式；下游解析约定见 B07-ADR-0002；读取入口见 PAIR07-ADR-002                                                                                                                                                                                                    |
| E2-B07-004 | DONE | MD 报告消费约束    | 郑悫  | `examples/error_report.json`      | MISSING 记录必填字段 target、dependency、commit、位置满足 REPAIR 定位需求；只声明 B 消费侧约束，完整 ERROR_REPORT 格式由 A 组定义；URI 读取方式遵循 003 约定                                                                                                                                                       |
| E2-B07-005 | DONE | DRAFT 请求样例   | 刘雯  | `examples/draft_request.json`     | 输入含 `repository.url`、`commit`、`build.command`、`verify_command`、`max_iterations`、时间限制、幂等键；字段齐全且符合 schema；`job_id` 由服务端产生，不随请求传入                                                                                                                                               |
| E2-B07-006 | DONE | DRAFT 响应样例   | 刘雯  | `examples/draft_response.json`    | 输出含 `job_id`、Dockerfile、镜像引用、每轮日志、每轮修改与选择理由、最终构建和验证结果                                                                                                                                                                                                                            |
| E2-B07-007 | DONE | REPAIR 请求样例  | 姚睿哲 | `examples/repair_request.json`    | 输入含相同仓库与 commit、MD 报告 URI 且格式遵循 004 约定、Makefile、环境与构建验证命令、幂等键；`job_id` 由服务端产生，不随请求传入                                                                                                                                                                                |
| E2-B07-008 | DONE | REPAIR 响应样例  | 姚睿哲 | `examples/repair_response.json`   | 输出含 `job_id`、Git Patch、声明风格说明、构建测试重检结果；只消费 `MISSING` 类型；失败时拒绝候选并记录原因                                                                                                                                                                                                        |
| E2-B07-009 | DONE | 查询任务样例       | 姚睿哲 | `examples/job_query.json`         | `GET /v1/jobs/{job_id}` 返回当前状态与产物引用；运行中返回输入摘要而非大日志；`trace_id` 贯穿查询链路                                                                                                                                                                                                              |
| E2-B07-010 | DONE | 失败输入校验脚本     | 王师师 | `contracts/validate.py`<br/>`contracts/fixtures/negative/` | 运行 validate.py 通过四类请求与响应的正向校验；`job_type` 改为 `ABC` 被拒绝；删除增量任务 `baseline` 被拒绝，该用例由 A 组定义并提供样例，B 侧仅实现校验逻辑；能解释 MD 与工具失败的语义差异，见 002 约定                                                                                                               |
| E2-B07-011 | DONE | ADR：异步任务模型   | 郑悫  | `adr/B07-ADR-0001-async-job-model.md`     | 记录 Context、Decision 即 POST 202 加 GET 查询、Alternatives、Consequences                                                                                                                                                                                                                                    |
| E2-B07-012 | DONE | ADR：产物传递与可追溯性 | 郑悫  | `adr/B07-ADR-0002-artifact-passing.md`    | 记录大文件引用交接方式、下游解析约定；不重复样例内容                                                                                                                                                                                                                                                               |
| E2-B07-013 | 部分   | AI_USAGE.md  | 王师师 | `design/AI_USAGE.md`              | 记录 AI 提议、人工采纳修改拒绝理由、关联文件与验证结果                                                                                                                                                                                                                                                             |
| E2-B07-014 | DONE | 交换契约样例       | 王师师 | `examples/full-check/`、`incremental-check/`、`invalid/` | 与 A07 完成双向样例交换：复制 A 组 FULL_CHECK / INCREMENTAL_CHECK 请求与响应样例至 `examples/`；四类任务样例齐全，双方能解释同一份请求与结果；A07 提供的失败用例（如删 baseline）可被 validate.py 校验                                                                                                                  |
| E2-B07-015 | DONE | 交换决定与未决追踪    | 王师师 | `backlog.md`、`adr/B07-ADR-0001` `B07-ADR-0002`、`adr/PAIR07-ADR-001`~`004`、`design/a07-proposal.md` | 课堂配对三轮练习（环境基线→MD 报告→失败输入）的决定写入 `B07-ADR-0001` / `B07-ADR-0002`；J1–J5 推荐方案已由 B 侧落地为 `PAIR07-ADR-001`~`004`（现状态 Accepted），未决工作同步 backlog §七                                                                                                                                                                                                                          |

状态说明：`DONE` 已完成；`阻塞` 依赖外部输入无法推进；`部分` 仅完成可独立推进的部分。

## A 侧待确认 / 定义清单

（对应 E2-B07-015「未决工作同步 backlog」。来源：各样例 `open_items`、`task.schema.json` 的 `defined_by` 占位、以及 `validate.py` 正向一致性运行的发现。课堂配对练习第三轮需与 A07 逐条过一遍，确认结果回写此处并同步 `adr/B07-ADR-0001`、`adr/B07-ADR-0002`。）

### 一、A 必须定义（已由 A07 定义，见 `github.com/jkiuiui0058/DevOps-A07`）

| # | 待定义项 | 影响的 B 侧产物 | 当前状态 |
|---|----------|-----------------|----------|
| 1 | `FULL_CHECK` 输入字段全集（`configuration_id`、clean build 命令、项目根目录等） | `task.schema.json` 的 FULL_CHECK 分支 | **已定义**：A07 `docs/contracts/task.schema.json` `$defs/fullCheckInput` = `repository` / `environment`（`image` + `project_root`）/ `build`（`clean_command` + `build_command` + 可选 `verify_command` / `timeout_seconds`）/ `configuration_id` / `commit` / `defined_by` / `idempotency_key`。B 侧 schema 保持占位并引用 |
| 2 | `INCREMENTAL_CHECK` 输入字段全集 | `task.schema.json` 的 INCREMENTAL_CHECK 分支 | **已定义**：A07 `$defs/incrementalCheckInput` 在上述基础上加 `base_commit` 与 `baseline` |
| 3 | 完整 `ERROR_REPORT` 文件结构 | `examples/error_report.json`（B 仅声明消费侧约束） | **已定义**：A07 `error-report.schema.json`（顶层 `schema_version` / `repository` / `configuration_id` / `findings`，可选 `producer_job_id`）+ `finding.schema.json`；B 示例已按此补齐并对齐（校验 0 错误） |
| 4 | `baseline` 语义与「删 baseline」失败用例样例 | `validate.py` 用例 `incremental_missing_baseline` | **已确认**：A07 `$defs/baseline` = `commit` + `configuration_id` + `actual_graph_uri` 必填、`sha256` 可选。失败样例已由 A07 提供（`examples/invalid/incremental-without-baseline.json`），与 B 的 fixture 断言同一拒绝点 |

### 二、A 必须确认（已由 A07 确认）

| # | 待确认项 | 依据文件 | B 侧当前取值 | 状态 |
|---|----------|----------|--------------|------|
| 5 | 状态枚举命名 | `examples/status_error.json` | `QUEUED` / `RUNNING` / `SUCCEEDED` / `FAILED` / `TIMED_OUT` / `CANCELLED` | **已确认**（A07 `B07-INTEGRATION.md`） |
| 6 | 错误码命名与语义 | `examples/status_error.json` | `ENV_3002` / `EXEC_4002` / `ANALYSIS_5001` | **已确认**（A07 `error-codes.md`，与 B 一致）；更细的 A07 编码暂不入共享枚举，先用 `message` / `details` 表达 |
| 7 | `artifact://` 命名与解析机制、实际下载接口/解析器 | `examples/artifact.json`、`B07-ADR-0002`、`PAIR07-ADR-002` | 格式 `artifact://<pair>/<job>/<file>`；读取入口已定为平台统一 HTTP 接口 | pair 段已确认 `pair07`（A07 `A07:ADR-003`，`<job>` 取生产任务短名）；读取入口已按 `PAIR07-ADR-002` 定为平台统一 HTTP 接口（A07 已确认） |
| 8 | `ERROR_REPORT.position` 字段命名与必填性 | `examples/error_report.json` | B 至少需要 `file` + `line` 才能定位 Makefile 声明 | **已确认**：`position.file` + `position.line` 必填、`declaration` 可选（A07 `finding.schema.json` / `B07-CONFIRMATION-RECORD.md`） |
| 9 | `detector` 枚举命名 | `examples/error_report.json` | `BUILDCHECKER` / `ECHECKER` / `INSTRUCTOR_ORACLE` | **已确认**：必填且枚举为三者之一（A07 `finding.schema.json`） |
| 10 | `REDUNDANT` 记录的消费策略 | `examples/error_report.json` | B 主张忽略（不属修复范围） | **已确认**忽略（A07 `B07-CONFIRMATION-RECORD.md`，并声明该说法不构成新的未协商策略） |
| 11 | 严格性约定：B 侧以 `additionalProperties: false` 拒绝新增字段 | `task.schema.json` | 遵循课程「严格 Schema 拒绝新增字段」；新增可选字段须经消费者允许 | 已按 A07 三字段显式登记（见 C5），维持 `additionalProperties: false` |

另有一处约束差异待 A 确认：B 的 `$defs/repository.url` 要求 `^https?://` 且 `format: uri`，A07 的 `$defs/repository.url` 仅要求非空字符串（`minLength: 1`）。A07 样例均使用 https，故当前不阻塞；需 A 确认 http(s) 是否足够，或由 B 放宽。

### 三、A 需交付的交接物（已交接）

| # | 交接物 | 用途 | 当前状态 |
|---|--------|------|----------|
| 12 | A07 的 FULL_CHECK / INCREMENTAL_CHECK 请求与响应样例文件 | 完成 E2-B07-014，补齐四类任务样例 | **已获取**：5 份样例落地至 `examples/full-check/`、`examples/incremental-check/`，全部通过 `validate.py` |
| 13 | A07 提供的失败用例（如删 baseline） | 交由 `validate.py` 校验 | **已获取**：`examples/invalid/incremental-without-baseline.json`（负例区），与 fixture `incremental_missing_baseline` 断言同一拒绝点 |
| 14 | A07 仓库地址 | 双向样例交换与 E12 交叉读取产物 | **已获取**：https://github.com/jkiuiui0058/DevOps-A07，已填入 `README.md` |

### 四、B 侧的实现现状

- **递归发现**：`validate.py` 用 `rglob` 递归发现 `examples/` 下的样例，A 侧样例按其 `full-check/`、`incremental-check/` 目录原样落地即自动纳入校验，无需修改脚本。原先用的 `glob("*.json")` 是**非递归**的，放进子目录会被静默忽略 —— 已修正（见第五节 #16）。
- **负例分区分流**：`examples/invalid/` 为负例区，不计入正向扫描，由 SKIP 行显式列出；既不会被误判为漂移，也不会被「防静默漏判」误报。
- **失败用例数据化**：`contracts/fixtures/negative/` 下的用例记录「来源指针 + 变异操作」而非样例副本，故样例更新时不会静默失效。A07 提供的失败样例与 `incremental_missing_baseline`（`owner: A07`）断言同一拒绝点。
- **防静默漏判**：任一正向样例文件未贡献任何可识别 payload 即 FAIL，避免 A 侧样例落地后被静默跳过。
- **四类覆盖现状**：DRAFT、REPAIR、FULL_CHECK、INCREMENTAL_CHECK 均有独立请求/响应样例文件。

### 五、`validate.py` 正向一致性发现（已解决）

| # | 发现 | 结论 |
|---|------|------|
| 15 | `task.schema.json` 的 `required` 含 `input`，但 `job_query.json` 的查询响应体不带 `input`（运行中用 `input_summary`，终态两者皆无），Schema 与查询视图不一致 | **已解决**：A07 `ADR-002-job-query-response.md` 规定查询返回完整 Job、保留完整 `input`。B 侧据此为 `job_query.json` 两个响应体补上完整 `input`，并**删除 `validate.py` 中专为查询视图放宽 `required` 的例外 `QUERY_VIEWS`**（连带移除 `_without_required_input()`）。查询响应体现与 `task.schema.json` 的 `required.input` 一致，无特例；`B07-ADR-0001` 与 `status_error.json` 中「返回输入摘要」的措辞已同步更新 |
| 16 | 发现层用 `EXAMPLES.glob("*.json")` **非递归**，A07 样例位于 `full-check/` 等子目录，落地后会被静默忽略；且 `unrecognized_sample_files()` 同样非递归，「防静默漏判」守卫抓不到 | **已解决**：发现改为 `rglob` 递归，并新增 `examples/invalid/` 负例区分流（SKIP 显式列出，不计入正向也不误报漏判）。正向样例 18 → 23 条（+5 条 A07 样例），失败输入仍 7、失败 0 |

### 六、A07 分歧清单的逐项处置（E2-B07-015）（已解决）

来源：A07 返回的分歧说明（5 项已存在冲突 + 4 项需共同决定）。这 5 项冲突中 A07 是其域的定义方（`ADR-002` 查询响应、`ADR-003` artifact URI、`artifact.schema.json`、`finding.schema.json`、`error-report.json`），B 为消费方，故 B 侧按其定法对齐自身样例与 ADR。

| # | 冲突 | A07 定法 | B 侧处置 | 涉及文件 |
|---|------|----------|----------|----------|
| C1 | 查询结果格式 | 返回完整 Job，保留完整 `input` | 已对齐：响应体补完整 `input`，并删除 `QUERY_VIEWS` 例外 | `examples/job_query.json`、`validate.py`、`B07-ADR-0001`、`examples/status_error.json` |
| C2 | artifact URI 的 pair 段 | `artifact://pair07/...` | 已对齐：11 处 URI 由 `pair01` 改为 `pair07`；另修正 `repair_response.json` 的 runner 名 | 6 个 `examples/*.json` |
| C3 | 日志产物类型 | `TRACE_LOG` / `BUILD_LOG` 两种 | 已对齐：泛化 `LOG` 改 `BUILD_LOG`，新增 `TRACE_LOG` 示例 | `examples/artifact.json`、`task.schema.json` |
| C4 | `detector` 是否必填 | 每条 finding 必填 | 已对齐：加入 `required_fields.fields`；并补进 `definitions`（原文件 `additionalProperties: false` 但定义里没有 `detector`，B 自己的样例都会被判非法） | `examples/error_report.json` |
| C5 | 报告额外字段 | 有 `finding_id` / `configuration_id` / `evidence_uri` | 已按 B07-ADR-0002「新增可选字段须得到消费者允许」显式登记三字段（B 不消费，仅保证可解析），维持 `additionalProperties: false` | `examples/error_report.json` |

**A07 仓库到位后的复核**：C1–C5 原先依据分歧清单的*文字摘要*对齐，拿到 A07 实际文件（`github.com/jkiuiui0058/DevOps-A07`）后逐条核对，五项均与 A07 实际定义一致。其中 C5 又发现两个摘要未涵盖的必填点 —— A07 `finding.schema.json` 要求每条 finding 必填 `finding_id`，`error-report.schema.json` 要求顶层必填 `configuration_id`，B 的 `examples.error_report` 示例原本两处都缺，已补齐；补齐后以 A07 的 `error-report.schema.json` + `finding.schema.json` 校验该示例，**0 错误**。

### 七、共同决定（J1–J5）：B 侧已按推荐方案落地，A07 已确认


| # | 决定 | 落点 | B 侧落地内容 |
|---|------|------|--------------|
| J1 | `artifact://pair07/...` 实际如何定位文件 | `PAIR07-ADR-002` | 以平台统一 HTTP 读取入口为主（`GET /v1/artifacts/{pair}/{job}/{file}`；200 带 `Content-Type` 与 `X-Artifact-Sha256`、404 未找到或已过保留期、403 跨 pair）；共享目录仅作可选降级实现 |
| J2 | 报告的保留时长、清理方、访问权限 | `PAIR07-ADR-002` | 平台统一 GC；终态后保留 30 天；pair 内只读互访；硬约束「只要还有 Job 引用某个 artifact，该文件不得删除」 |
| J3 | A07 新增错误码是否写入双方共享表 | `PAIR07-ADR-003` | 前缀分域（`ENV_*`/`EXEC_*` 共享、`ANALYSIS_*` A07 专有）+ 按前缀区分的宽容规则（共享前缀未知码拒绝、A07 专有前缀未知码容忍）；已落地到 `status_error.json` 与 `task.schema.json` |
| J4 | 公共 Schema 的版本同步方式 | `PAIR07-ADR-004` | `schema_version` 保持 `const: "1.0"`；变更须发起方先立项 → 双方确认 → 同一提交内更新版本号与全部样例；由变更发起方递增 |
| J5 | `ADR-002` 编号跨组歧义 | `PAIR07-ADR-001` | 编号只保证仓库内唯一；`B07-ADR-NNNN` / `A07-ADR-NNN` / `PAIR07-ADR-NNN` 三分；跨组引用带组名前缀（`A07:ADR-002`）。B 侧已把 `0001`/`0002` 迁移为 `B07-ADR-0001`/`B07-ADR-0002` |

