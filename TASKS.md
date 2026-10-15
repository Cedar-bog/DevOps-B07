# 任务分工

## 小组成员

| 学号      | 姓名   | GitHub    |
|-----------|--------|-----------|
| 241250022 | 姚睿哲 | yaorz26   |
| 241250026 | 郑悫   | Cedar-bog |
| 241250064 | 王师师 | Shishi-w  |
| 241250103 | 刘雯   | Gogo1101  |

## E2

| 编号 | 状态 | 任务                    | 负责人 | 产物                                                                    |
|------|------|-------------------------|--------|-------------------------------------------------------------------------|
| 001  | DONE | 统一任务模型            |  郑悫  | `contracts/task.schema.json`                                            |
| 002  | DONE | 状态与错误码约定        |  郑悫  | `contracts/conventions/status_error.json`                               |
| 003  | DONE | 产物访问样例            |  郑悫  | `contracts/conventions/artifact.json`                                   |
| 004  | DONE | MD 报告消费约束         |  郑悫  | `contracts/conventions/error_report.json`                               |
| 005  | DONE | DRAFT 请求样例          |  刘雯  | `contracts/examples/draft/request.json`                                 |
| 006  | DONE | DRAFT 响应样例          |  刘雯  | `contracts/examples/draft/response.json`                                |
| 007  | DONE | REPAIR 请求样例         | 姚睿哲 | `contracts/examples/repair/request.json`                                |
| 008  | DONE | REPAIR 响应样例         | 姚睿哲 | `contracts/examples/repair/response.json`                               |
| 009  | DONE | 查询任务样例            | 姚睿哲 | `contracts/examples/job_query.json`                                     |
| 010  | DONE | 失败输入和校验脚本      | 王师师 | `contracts/negative`<br/>`contracts/validate.py`                        |
| 011  | DONE | ADR：异步任务模型       |  郑悫  | `design/adr/B07-ADR-0001-async-job-model.md`                            |
| 012  | DONE | ADR：产物传递与可追溯性 |  郑悫  | `design/adr/B07-ADR-0002-artifact-passing.md`                           |
| 013  | DONE | AI_USAGE.md             | 王师师 | `design/AI_USAGE.md`                                                    |
| 014  | DONE | 交换契约样例            | 王师师 | `contracts/examples/full-check`、`contracts/examples/incremental-check` |
| 015  | DONE | 交换决定与未决追踪      | 王师师 | `design/backlog.md`、`design/adr/PAIR07-ADR-*.md`                       |

## E3

| 编号 | 状态 | 任务                                | 负责人 | 产物                                                                                                  |
|------|------|-------------------------------------|--------|-------------------------------------------------------------------------------------------------------|
| 001  | DONE | DRAFT 样本项目与两层成功判据        |  郑悫  | `../../fixtures/draft`（main.c、Makefile、README）                                                         |
| 002  | DONE | DRAFT 失败样例                      |  郑悫  | `../../fixtures/draft/Dockerfile.broken`、`../../evidence`                                       |
| 003  | DONE | DRAFT 参考成功                      |  郑悫  | `../../fixtures/draft/Dockerfile.reference`、`../../evidence`                                 |
| 004  | DONE | MDFixer 固定 MD 报告与 Makefile     | 王师师 | `../../fixtures/md-fixer`（`main.o` 缺 `config.h` 的 ORACLE 报告、对应 Makefile）                          |
| 005  | DONE | MDFixer 四种声明风格与参考 Patch    | 王师师 | `../../fixtures/md-fixer`（Target/Macro/Hybrid/Implicit 的 before 与 reference.patch）                     |
| 006  | DONE | MDFixer 行为验证与无效候选拒绝/恢复 | 姚睿哲 | `../../evidence/md-fixer`（apply、before、after、reapply、rejected 日志）                                  |
| 007  | DONE | 共同信息与失败记录                  |  刘雯  | `../../../README.md`、`../../scripts`、`../../evidence`                                                                  |
