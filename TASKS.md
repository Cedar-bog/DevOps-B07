# 任务分工

## 小组成员
| 学号      | 姓名   | GitHub    |
|-----------|--------|-----------|
| 241250022 | 姚睿哲 | yaorz26   |
| 241250026 |  郑悫  | Cedar-bog |
| 241250064 | 王师师 | Shishi-w  |
| 241250103 |  刘雯  | Gogo1101  |
---
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
---
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
---
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
---
## E5 - 论文1复现
| 状态 | 负责人 |
|------|--------|
|      |        |
#### 任务
- 复用 E3 fixtures/draft 样例，手动复现初始构建失败
- 构造包含失败日志与当前 Dockerfile 的修复提示词，保存原始回复
- 提取候选 Dockerfile，人工挑选候选并记录选择依据
- 重复直到构建成功，保留构建与验证日志
#### 产物
```
evidence/e5/
├─── inputs/            # 复用 E3 样例
├─── candidates/        # 候选 Dockerfile
├─── logs/
│   ├─── build-broken.log   # 初始失败日志
│   ├─── build-01.log       # 构建日志
│   ├─── verify-01.log      # 验证日志
│   └─── ...
└─── report.md          # 实验报告，包含提示词与回复、选择依据
```
---
## E6 - 论文1 MVP
| 编号 | 状态 | 任务                                                                  | 负责人 | 产物                                |
|------|------|-----------------------------------------------------------------------|--------|-------------------------------------|
| 001  | DONE | 扩展环境变量样例：LLM API 地址、密钥、模型                            |  郑悫  | `.env.example`                      |
| 002  |      | 可注入的 LLM 适配层：读取 API 配置、发送提示词、返回原始回复          |  郑悫  | `services/draft/draft/llm.py`       |
| 003  |      | 构建指定 Dockerfile，采集退出码与日志，成功时返回镜像 ID              |        | `services/draft/draft/docker.py`    |
| 004  |      | 运行构建出的镜像，检查退出码与预期输出，记录验证结果                  |        | `services/draft/draft/verify.py`    |
| 005  |      | 使用当前 Dockerfile、该轮失败日志与项目 README 生成完整提示词         |        | `services/draft/draft/prompt.py`    |
| 006  |      | 从回复中提取候选 Dockerfile，校验必要指令并选择                       |        | `services/draft/draft/candidate.py` |
| 007  |      | 自动修复循环，直到成功或达到上限                                      |        | `services/draft/draft/pipeline.py`  |
| 008  |      | 汇总自动修复循环的每轮结果，创建 work/<timestamp>/ 并写入 result.json |        | `services/draft/draft/result.py`    |
| 009  |      | 扩展 DRAFT CLI，增加自动修复运行子命令                                |        | `services/draft/draft/cli.py`       |
| 010  |      | 在 fixtures/draft 上执行一次完整自动修复                              |  郑悫  | `work/<timestamp>/`                 |
---
## E7 - 论文1验证
| 编号 | 状态 | 任务                                                           | 负责人 | 产物                                     |
|------|------|----------------------------------------------------------------|--------|------------------------------------------|
| 001  |      | 实现 fake LLM 测试替身，按预置候选回复模拟模型输出，供测试注入 |  郑悫  | `services/draft/tests/fake_llm.py`       |
| 002  |      | 覆盖正常修复路径，验证从初始失败到候选成功                     |        | `services/draft/tests/test_pipeline.py`  |
| 003  |      | 覆盖无效候选，验证改动 CMD 或缺少必要指令的候选                |        | `services/draft/tests/test_candidate.py` |
| 004  |      | 覆盖候选构建失败，验证该轮不进入运行验证                       |        | `services/draft/tests/test_pipeline.py`  |
| 005  |      | 覆盖输出不符，验证运行输出不匹配时该轮失败并继续下一轮         |        | `services/draft/tests/test_pipeline.py`  |
| 006  |      | 覆盖运行超时，验证超时后终止并清理运行容器                     |        | `services/draft/tests/test_pipeline.py`  |
| 007  |      | 运行全部测试与场景，汇总通过情况和退出码                       |        | `scripts/run_e7_gate.py`                 |
| 008  |      | 接入 GitHub Actions，在提交和 PR 上运行 E7 门禁并归档结果      |  郑悫  | `.github/workflows/e7-quality-gate.yml`  |
| 009  |      | 配置 main 分支保护，将 E7 门禁设为必需检查                     |  郑悫  | -                                        |
| 010  |      | 创建故意失败的 PR，验证 E7 门禁失败并阻止合并                  |  郑悫  | -                                        |
| 011  |      | 归档 E7 运行证据                                               |  郑悫  | `evidence/e7/<timestamp>/`               |
