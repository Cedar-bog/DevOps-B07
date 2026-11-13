# 任务分工
> AI 请阅读 [工作约束](AGENT.md)。
---
## 小组成员
| 学号|姓名| GitHub|
|-----------|:----:|-----------|
| 241250022|姚睿哲| yaorz26|
| 241250026|郑悫| Cedar-bog|
| 241250064|王师师| Shishi-w|
| 241250103|刘雯| Gogo1101|
---
## E2 - 需求与接口契约
| 编号| 状态| 任务|负责人| 产物|
|------|------|-------------------------|:----:|----------------------------------------------------------------------------|
| 001| DONE| 统一任务模型|郑悫| `contracts/task.schema.json`|
| 002| DONE| 状态与错误码约定|郑悫| `contracts/conventions/status_error.json`|
| 003| DONE| 产物访问样例|郑悫| `contracts/conventions/artifact.json`|
| 004| DONE| MD 报告消费约束|郑悫| `contracts/conventions/error_report.json`|
| 005| DONE| DRAFT 请求样例|刘雯| `contracts/examples/draft/request.json`|
| 006| DONE| DRAFT 响应样例|刘雯| `contracts/examples/draft/response.json`|
| 007| DONE| REPAIR 请求样例|姚睿哲| `contracts/examples/repair/request.json`|
| 008| DONE| REPAIR 响应样例|姚睿哲| `contracts/examples/repair/response.json`|
| 009| DONE| 查询任务样例|姚睿哲| `contracts/examples/job_query.json`|
| 010| DONE| 失败输入和校验脚本|王师师| `contracts/negative`<br/>`contracts/validate.py`|
| 011| DONE| ADR：异步任务模型|郑悫| `design/adr/B07-ADR-0001-async-job-model.md`|
| 012| DONE| ADR：产物传递与可追溯性|郑悫| `design/adr/B07-ADR-0002-artifact-passing.md`|
| 013| DONE| AI_USAGE.md|王师师| `design/AI_USAGE.md`|
| 014| DONE| 交换契约样例|王师师| `contracts/examples/full-check`<br/>`contracts/examples/incremental-check`|
| 015| DONE| 交换决定与未决追踪|王师师| `design/backlog.md`<br/>`design/adr/PAIR07-ADR-*.md`|
---
## E3 - 并行测试基线
| 编号| 状态| 任务|负责人| 产物|
|------|------|-------------------------------------|:----:|--------------------------------------------------------------------------------------|
| 001| DONE| DRAFT 样本项目与两层成功判据|郑悫| `fixtures/draft/main.c`<br/>`fixtures/draft/Makefile`<br/>`fixtures/draft/README.md`|
| 002| DONE| DRAFT 失败样例|郑悫| `fixtures/draft/Dockerfile.broken`|
| 003| DONE| DRAFT 参考成功|郑悫| `fixtures/draft/Dockerfile.reference`<br/>`evidence/draft`|
| 004| DONE| MDFixer 固定 MD 报告与 Makefile|王师师| `fixtures/md-fixer`|
| 005| DONE| MDFixer 四种声明风格与参考 Patch|王师师| `fixtures/md-fixer/styles`|
| 006| DONE| MDFixer 行为验证与无效候选拒绝/恢复|姚睿哲| `evidence/md-fixer`|
| 007| DONE| 共同信息与失败记录|刘雯| `scripts`<br/>`fixtures/README.md`|
---
## E4 - 可重复工程环境
| 编号| 状态| 任务|负责人| 产物|
|------|------|------------------------|:----:|---------------------------------------------------------------------------------------|
| 001| DONE| 服务骨架与命令行入口|郑悫| `services/draft/draft/cli.py`<br/>`services/draft/draft/smoke.py`|
| 002| DONE| 依赖声明与锁文件|郑悫| `requirements-dev.in`<br/>`requirements-dev.lock`|
| 003| DONE| 环境变量样例与忽略规则|郑悫| `.env.example`<br/>`.gitignore`<br/>`.dockerignore`|
| 004| DONE| 服务 Dockerfile|刘雯| `services/draft/Dockerfile`|
| 005| DONE| Compose 运行配置|刘雯| `compose.yaml`|
| 006| DONE| 环境自检脚本|刘雯| `scripts/doctor.py`|
| 007| DONE| 密钥扫描脚本|姚睿哲| `scripts/secret_scan.py`|
| 008| DONE| 一键命令 Makefile|姚睿哲| `Makefile`|
| 009| DONE| 服务单元测试|王师师| `services/draft/tests/test_cli.py`<br/>`services/draft/tests/test_smoke.py`|
| 010| DONE| 运行验证与证据归档|王师师| `work/<timestamp>/{test.log,smoke.json,env.json,build.log,image.json,toolchain.lock}`|
---
## E5 - DRAFT 复现
| 状态|负责人|
|------|:----:|
| DONE|刘雯|
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
## E6 - DRAFT MVP
| 编号| 状态| 任务|负责人| 产物|
|------|------|-----------------------------------------------------------------------|:----:|-------------------------------------|
| 001| DONE| 扩展环境变量样例：LLM API 地址、密钥、模型|郑悫| `.env.example`|
| 002| DONE| 可注入的 LLM 适配层：读取 API 配置、发送提示词、返回原始回复|郑悫| `services/draft/draft/llm.py`|
| 003| DONE| 构建指定 Dockerfile，采集退出码与日志，成功时返回镜像 ID|姚睿哲| `services/draft/draft/docker.py`|
| 004| DONE| 运行构建出的镜像，检查退出码与预期输出，记录验证结果|姚睿哲| `services/draft/draft/verify.py`|
| 005| DONE| 使用当前 Dockerfile、该轮失败日志与项目 README 生成完整提示词|姚睿哲| `services/draft/draft/prompt.py`|
| 006| DONE| 从回复中提取候选 Dockerfile，校验必要指令并选择|姚睿哲| `services/draft/draft/candidate.py`|
| 007| DONE| 自动修复循环，直到成功或达到上限|姚睿哲| `services/draft/draft/pipeline.py`|
| 008| DONE| 汇总自动修复循环的每轮结果，创建 work/<timestamp>/ 并写入 result.json|姚睿哲| `services/draft/draft/result.py`|
| 009| DONE| 扩展 DRAFT CLI，增加自动修复运行子命令|姚睿哲| `services/draft/draft/cli.py`|
| 010| DONE| 在 fixtures/draft 上执行一次完整自动修复|郑悫| `work/<timestamp>/`|
---
## E7 - DRAFT 验证
| 编号| 状态| 任务|负责人| 产物|
|------|------|----------------------------------------------------------------|:----:|------------------------------------------|
| 001| DONE| 实现 fake LLM 测试替身，按预置候选回复模拟模型输出，供测试注入|郑悫| `services/draft/tests/fake_llm.py`|
| 002| DONE| 覆盖正常修复路径，验证从初始失败到候选成功|姚睿哲| `services/draft/tests/test_pipeline.py`|
| 003| DONE| 覆盖无效候选，验证改动 CMD 或缺少必要指令的候选|姚睿哲| `services/draft/tests/test_candidate.py`|
| 004| DONE| 覆盖候选构建失败，验证该轮不进入运行验证|姚睿哲| `services/draft/tests/test_pipeline.py`|
| 005| DONE| 覆盖输出不符，验证运行输出不匹配时该轮失败并继续下一轮|姚睿哲| `services/draft/tests/test_pipeline.py`|
| 006| DONE| 覆盖运行超时，验证超时后终止并清理运行容器|王师师| `services/draft/tests/test_pipeline.py`|
| 007| DONE| 运行全部测试与场景，汇总通过情况和退出码|王师师| `scripts/run_e7_gate.py`|
| 008| DONE| 接入 GitHub Actions，在提交和 PR 上运行 E7 门禁并归档结果|郑悫| `.github/workflows/e7-quality-gate.yml`|
| 009| DONE| 配置 main 分支保护，将 E7 门禁设为必需检查|郑悫| -|
| 010| DONE| 归档 E7 运行证据|郑悫| `evidence/e7/<timestamp>/`|
---
## E8 - MDFixer 复现
| 状态|负责人|
|------|:----:|
| ||
#### 任务
- 复用 E3 的 md-fixer 样例与固定 MD 报告，确认报告与样例版本一致
- 人工定位缺失依赖的声明位置，按原声明风格补入 Makefile
- 对比修复前后的程序输出与增量重建行为，确认未修复时漏重建、修复后自动重建
- 整理修复位置、依据与验证结果，写入复现实验报告
#### 产物
```
evidence/e8/
├─── inputs/        # 复用 E3 样例与固定 MD 报告
├─── fixed/
│   └── Makefile        # 人工修复后的 Makefile
├─── logs/
│   ├─── before.log     # 修复前运行与增量重建
│   └─── after.log      # 修复后运行与增量重建
└─── report.md      # 实验报告，包含修复位置与依据
```
---
## E9 - MDFixer MVP
| 编号| 状态| 任务|负责人| 产物|
|------|------|-------------------------------------------------------------------------|:----:|--------------------------------------------------------------------------------|
| 001| | 服务骨架与命令行入口 |郑悫| `services/md-fixer/mdfixer/cli.py` |
| 002| | Makefile 解析与声明图构建 || `services/md-fixer/mdfixer/makefile.py` |
| 003| | 声明风格识别：直接、宏、混合、隐式 || `services/md-fixer/mdfixer/style.py` |
| 004| | MD 报告校验与错误定位：只消费 MISSING，校验提交与字段，定位目标与缺失依赖 || `services/md-fixer/mdfixer/report.py` |
| 005| | 前置拒绝与源项目保护：旧报告、不安全路径、不支持目标 || `services/md-fixer/mdfixer/guard.py` |
| 006| | 显式声明补丁生成：只补 MISSING，保留 REDUNDANT 与原风格，生成补丁与计划，不写源项目 || `services/md-fixer/mdfixer/patch.py`<br/>`services/md-fixer/mdfixer/plan.py` |
| 007| | 隐式声明补丁生成：简单模式规则生成 .d 方案，复杂规则交由 LLM || `services/md-fixer/mdfixer/implicit.py`<br/>`services/md-fixer/mdfixer/llm.py` |
| 008| | 单元测试：解析、风格、报告校验、补丁、拒绝与源项目保护 || `services/md-fixer/tests/` |
| 009| | 运行证据：在四种样例风格上生成补丁与计划，保存命令、输入与结果 |郑悫| `evidence/e9/<timestamp>/` |
---
## E10 - MDFixer 验证
| 编号| 状态| 任务|负责人| 产物|
|------|------|--------------------------------------------------------------------------|:----:|---------------------------------------------------------------------------------|
| 001| | 独立沙箱与补丁应用：复制项目副本，执行应用检查并应用候选补丁 || `services/md-fixer/mdfixer/sandbox.py` |
| 002| | 构建与程序验证：clean 构建、运行程序、检查输出 || `services/md-fixer/mdfixer/build.py` |
| 003| | 增量重建与重检：修改头文件后不 clean 构建，检查重建与 MISSING 消除，保留 REDUNDANT || `services/md-fixer/mdfixer/recheck.py` |
| 004| | 接受与写回：全部检查通过后写回源项目并记录结果 || `services/md-fixer/mdfixer/accept.py` |
| 005| | 拒绝与回滚：覆盖测试拒绝与补丁拒绝，确认源文件保持不变 || `services/md-fixer/mdfixer/rollback.py` |
| 006| | 验证入口与场景编排：正常接受、测试拒绝、补丁拒绝 || `services/md-fixer/mdfixer/gate.py` |
| 007| | 端到端测试与门禁汇总：运行三类事务，汇总检查结果与退出码 || `services/md-fixer/tests/test_e2e.py`<br/>`services/md-fixer/mdfixer/result.py` |
| 008| | 接入 CI：离线运行 E9 单元测试与 E10 端到端验证，归档结果 |郑悫| `.github/workflows/e8e10-quality-gate.yml` |
| 009| | 配置 main 分支保护：将 E10 门禁设为必需检查，确认失败阻止合并、修复后恢复通过 |郑悫| - |
| 010| | 归档 E10 运行证据 |郑悫| `evidence/e10/<timestamp>/` |
---