# 任务分工

## 小组成员

| 学号      | 姓名   | GitHub    |
|-----------|--------|-----------|
| 241250022 | 姚睿哲 | yaorz26   |
| 241250026 |  郑悫  | Cedar-bog |
| 241250064 | 王师师 | Shishi-w  |
| 241250103 |  刘雯  | Gogo1101  |

## E2 - 需求与接口契约

| 编号 | 状态 | 任务                    | 负责人 | 产物                                                                       |
|------|------|-------------------------|--------|----------------------------------------------------------------------------|
| 001  | DONE | 统一任务模型            |  郑悫  | `contracts/task.schema.json`                                               |
| 002  | DONE | 状态与错误码约定        |  郑悫  | `contracts/conventions/status_error.json`                                  |
| 003  | DONE | 产物访问样例            |  郑悫  | `contracts/conventions/artifact.json`                                      |
| 004  | DONE | MD 报告消费约束         |  郑悫  | `contracts/conventions/error_report.json`                                  |
| 005  | DONE | DRAFT 请求样例          |  刘雯  | `contracts/examples/draft/request.json`                                    |
| 006  | DONE | DRAFT 响应样例          |  刘雯  | `contracts/examples/draft/response.json`                                   |
| 007  | DONE | REPAIR 请求样例         | 姚睿哲 | `contracts/examples/repair/request.json`                                   |
| 008  | DONE | REPAIR 响应样例         | 姚睿哲 | `contracts/examples/repair/response.json`                                  |
| 009  | DONE | 查询任务样例            | 姚睿哲 | `contracts/examples/job_query.json`                                        |
| 010  | DONE | 失败输入和校验脚本      | 王师师 | `contracts/negative`<br/>`contracts/validate.py`                           |
| 011  | DONE | ADR：异步任务模型       |  郑悫  | `design/adr/B07-ADR-0001-async-job-model.md`                               |
| 012  | DONE | ADR：产物传递与可追溯性 |  郑悫  | `design/adr/B07-ADR-0002-artifact-passing.md`                              |
| 013  | DONE | AI_USAGE.md             | 王师师 | `design/AI_USAGE.md`                                                       |
| 014  | DONE | 交换契约样例            | 王师师 | `contracts/examples/full-check`<br/>`contracts/examples/incremental-check` |
| 015  | DONE | 交换决定与未决追踪      | 王师师 | `design/backlog.md`<br/>`design/adr/PAIR07-ADR-*.md`                       |

## E3 - 并行测试基线

| 编号 | 状态 | 任务                                | 负责人 | 产物                                                                                 |
|------|------|-------------------------------------|--------|--------------------------------------------------------------------------------------|
| 001  | DONE | DRAFT 样本项目与两层成功判据        |  郑悫  | `fixtures/draft/main.c`<br/>`fixtures/draft/Makefile`<br/>`fixtures/draft/README.md` |
| 002  | DONE | DRAFT 失败样例                      |  郑悫  | `fixtures/draft/Dockerfile.broken`                                                   |
| 003  | DONE | DRAFT 参考成功                      |  郑悫  | `fixtures/draft/Dockerfile.reference`<br/>`evidence/draft`                           |
| 004  | DONE | MDFixer 固定 MD 报告与 Makefile     | 王师师 | `fixtures/md-fixer`                                                                  |
| 005  | DONE | MDFixer 四种声明风格与参考 Patch    | 王师师 | `fixtures/md-fixer/styles`                                                           |
| 006  | DONE | MDFixer 行为验证与无效候选拒绝/恢复 | 姚睿哲 | `evidence/md-fixer`                                                                  |
| 007  | DONE | 共同信息与失败记录                  |  刘雯  | `scripts`<br/>`fixtures/README.md`                                                   |

## E4 - 可重复工程环境

| 编号 | 状态 | 任务                   | 负责人 | 产物                                                                                  |
|------|------|------------------------|--------|---------------------------------------------------------------------------------------|
| 001  | DONE | 服务骨架与命令行入口   |  郑悫  | `services/draft/draft/cli.py`<br/>`services/draft/draft/smoke.py`                     |
| 002  | DONE | 依赖声明与锁文件       |  郑悫  | `requirements-dev.in`<br/>`requirements-dev.lock`                                     |
| 003  | DONE | 环境变量样例与忽略规则 |  郑悫  | `.env.example`<br/>`.gitignore`<br/>`.dockerignore`                                   |
| 004  | DONE | 服务 Dockerfile        |  刘雯  | `services/draft/Dockerfile`                                                           |
| 005  | DONE | Compose 运行配置       |  刘雯  | `compose.yaml`                                                                        |
| 006  | DONE | 环境自检脚本           |  刘雯  | `scripts/doctor.py`                                                                   |
| 007  | DONE | 密钥扫描脚本           | 姚睿哲 | `scripts/secret_scan.py`                                                              |
| 008  | DONE | 一键命令 Makefile      | 姚睿哲 | `Makefile`                                                                            |
| 009  | DONE | 服务单元测试           | 王师师 | `services/draft/tests/test_cli.py`<br/>`services/draft/tests/test_smoke.py`           |
| 010  | DONE | 运行验证与证据归档     | 王师师 | `work/<timestamp>/{test.log,smoke.json,env.json,build.log,image.json,toolchain.lock}` |