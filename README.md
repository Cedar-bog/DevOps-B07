# DevOps-B07

南京大学 DevOps 教学实验 · B07 组仓库

- 组仓库（origin）：https://github.com/Cedar-bog/DevOps-B07
- 对接 A07 组：https://github.com/jkiuiui0058/DevOps-A07
- 双方在 E2 使用 `pair07` 作为 Artifact URI 的 pair 段。

## 小组成员

| 学号      | 姓名   | GitHub    |
|-----------|--------|-----------|
| 241250022 | 姚睿哲 | yaorz26   |
| 241250026 | 郑悫   | Cedar-bog |
| 241250064 | 王师师 | Shishi-w  |
| 241250103 | 刘雯   | Gogo1101  |

## 仓库结构

```
DevOps-B07/
├── README.md
├── pyproject.toml / uv.lock         # Python 依赖（契约校验脚本用）
├── docs/
│   ├── references/                  # 复现参考论文
│   ├── tasks/                       # 课程实验要求（含 E2/E3 课件）
│   ├── contracts/                   # E2 接口契约
│   │   ├── task.schema.json             # 统一任务模型 Schema
│   │   ├── validate.py                  # 失败输入校验脚本
│   │   ├── examples/                    # 任务请求/响应样例（含 A07 样例与负例区）
│   │   └── fixtures/negative/           # 失败输入用例（来源指针 + 变异操作）
│   └── design/
│       ├── backlog.md               # 任务与验收条件
│       ├── adr/                     # B07-ADR-* 单方 / PAIR07-ADR-* 双方共同
│       └── AI_USAGE.md              # AI 使用与验证记录
├── fixtures/                        # E3 并行测试基线样例（人工构造，非上游真实项目）
│   ├── draft/                       # E3-001/002/003：DRAFT 两层判据 + 失败/参考 Dockerfile
│   └── md-fixer/                    # E3-004/005：MDFixer 固定 MD 报告 + 四种声明风格参考 Patch
├── evidence/                        # 真实运行日志（逐字保留；与人工 ORACLE 标签分离）
│   ├── draft/                       # E3-001/002/003 构建与运行日志
│   └── md-fixer/                    # E3-006 apply/before/after/reapply/rejected 日志
└── scripts/                         # 复现脚本
    ├── verify-draft.sh              # DRAFT 基线复现（E3-001/002/003）
    ├── verify-md-fixer.sh           # MDFixer 行为验证（E3-006）
    └── verify-all.sh                # 一键串联以上两者
```

## 环境与工具版本

E3 基线样例在本机 WSL2（Ubuntu）上实测，关键版本如下（完整信息见各 `evidence/*.log` 头部）：

| 项 | 值 |
|---|---|
| 操作系统 | Ubuntu 26.04.1 LTS（WSL2），内核 `6.18.x-microsoft-standard-WSL2` / x86_64 |
| GNU Make | 4.4.1 |
| C 编译器 | `cc` / `gcc (Ubuntu 15.2.0-16ubuntu1) 15.2.0` |
| Docker | 客户端/引擎 `29.8.2`（Docker Desktop 4.94.0），`linux/amd64`；参考构建使用 registry mirror `docker.1ms.run` |
| Python / uv | 3.14.4 / uv（E2 契约校验用） |
| Git | 2.47.1（Windows） / 2.53.0（WSL） |

> 本机 WSL 未启用 Docker Desktop 的 WSL 集成，DRAFT 的 docker 阶段需用 Windows 侧 `docker.exe`（脚本按 PATH 上的 `docker` 调用）。

## E3 并行测试基线

E3 以人工构造的固定样例验证「DRAFT 环境生成」与「MDFixer 缺失依赖修复」两条流程。

| 任务 | 负责人 | 产物 | 说明 |
|---|---|---|---|
| E3-B07-001 | 郑悫 | `fixtures/draft/` | DRAFT 样本项目与两层成功判据（编译通过 + 功能验证） |
| E3-B07-002 | 郑悫 | `fixtures/draft/Dockerfile.broken`、`evidence/draft/broken.log` | 缺工具链镜像构建失败，失败点可定位 |
| E3-B07-003 | 郑悫 | `fixtures/draft/Dockerfile.reference`、`evidence/draft/reference.log` | 参考 Dockerfile 构建成功、容器输出 `hello E3` |
| E3-B07-004 | 王师师 | `fixtures/md-fixer/fixed-md-report.json` 等 | 固定 MD 报告（ORACLE）与对应 Makefile |
| E3-B07-005 | 王师师 | `fixtures/md-fixer/styles/` | 四种声明风格（Target/Macro/Hybrid/Implicit）参考 Patch |
| E3-B07-006 | 姚睿哲 | `evidence/md-fixer/`、`scripts/verify-md-fixer.sh` | MDFixer 行为验证与无效候选拒绝/恢复 |
| E3-B07-007 | 刘雯 | `README.md`、`scripts/`、`evidence/` | 共同信息、复现脚本与失败记录 |

## 复现步骤

在仓库根目录执行（bash；Windows 请在 WSL 或 Git Bash 中运行）。

一键复现全部 E3 基线：

```sh
bash scripts/verify-all.sh
```

### DRAFT（E3-B07-001/002/003）

本地两层成功判据：

```sh
cd fixtures/draft
make            # 退出码 0，生成可执行文件 hello
./hello         # 输出 hello E3，退出码 0
make clean
```

docker 两条基线（需运行中的 Docker 引擎）：

```sh
# E3-B07-002：失败样例，构建应非零退出并出现 make: not found
docker build --progress=plain --no-cache \
  -f fixtures/draft/Dockerfile.broken -t nju-e3-draft-broken fixtures/draft

# E3-B07-003：参考成功，构建退出码 0，运行输出 hello E3
docker build --progress=plain --no-cache \
  -f fixtures/draft/Dockerfile.reference -t nju-e3-draft-reference fixtures/draft
docker run --rm nju-e3-draft-reference
```

以上三步也可用 `bash scripts/verify-draft.sh` 一次完成（无 Docker 引擎时自动跳过 docker 两相并给出提示）。

### MDFixer（E3-B07-004/005/006）

```sh
bash scripts/verify-md-fixer.sh
```

脚本对四种声明风格分别验证：补丁可 `git apply --check` 通过、应用后 clean 构建输出新值、再次修改头文件不 clean 自动重建、无效候选被拒绝且样例副本可恢复。

## ORACLE 与真实日志分离

- **人工 ORACLE 标签**：`fixtures/md-fixer/fixed-md-report.json`（`detector=INSTRUCTOR_ORACLE`）、各 `fixtures/*/README.md` 中的「人工预期判据」小节。这些是人工构造的期望，**不计入工具准确率**。
- **真实运行日志**：`evidence/**`，均为本仓库实测捕获、逐字保留，头部记录命令、环境、提交 SHA 与退出码。
- 两者分目录保存，前者用于对照，后者用于核验。

## 提交与版本

E3 产物对应的提交 SHA（`main`）：

| 阶段 | 提交 | 内容 |
|---|---|---|
| E3-B07-001 | `74cc2af` | `fixtures/draft/`（main.c、Makefile、README） |
| E3-B07-002 | `8002fef` | `Dockerfile.broken` + 实测 `broken.log` + README 失败小节 |
| E3-B07-003 | `9286a6c` | `Dockerfile.reference` + 实测 `reference.log` + README 参考成功小节 |
| E3-B07-004/005 | `2b8412f`、`0b4b771`、`17ce3d6` | `fixtures/md-fixer/`（固定报告、Makefile、四种风格与参考 Patch、README） |
| E3-B07-006 | `8095609`、`3974b7e` | `scripts/verify-md-fixer.sh` + `evidence/md-fixer/` |
| E3-B07-007 | 见本分支 `E3/B07-007` 提交历史 | 本 README、`scripts/verify-draft.sh`、`scripts/verify-all.sh`、`evidence/draft/build.log` |

## 未完成 / 已知问题

（按 E3-B07-007 要求如实记录，本任务不负责修复。）

1. **AI_USAGE 记录缺口**：`docs/design/AI_USAGE.md` 中 E3-B07-003 的「§六 记录」仍为占位「见本节提交」；§五 003 引用的提交 `a90d569` 不在 `main` 祖先上（`main` 对应 `9286a6c`）；E3-B07-001/004/005/006 尚无单独记录。卡点：属历史记录补录，不阻塞功能。下一步：后续统一补齐。
2. **E2 契约遗留**：B 的 `$defs/repository.url` 要求 `^https?://` 且 `format: uri`，A07 仅要求非空字符串，待 A 侧确认 http(s) 是否足够。见 `docs/design/backlog.md` 第二节末尾。
3. **DRAFT docker 阶段环境依赖**：本机 WSL 未启用 Docker Desktop 集成，`scripts/verify-draft.sh` 的 docker 两相需 Windows 侧 `docker.exe` 与运行中的引擎；无引擎时脚本跳过并提示，`evidence/draft/broken.log`、`reference.log` 为既往前述环境下的真实运行结果。
4. **E3 仅覆盖基线样例**：DRAFT 与 MDFixer 尚未接入真实工具链，当前为人工构造的固定样例，仅用于判据与行为验证。

## 架构决策记录（ADR）

ADR 编号只保证仓库内唯一，跨组引用带组名前缀（如 `A07:ADR-002`、`B07:ADR-0002`）。

| 命名空间 | 归属 | 现有条目 |
|----------|------|----------|
| `B07-ADR-NNNN` | B07 单方决策 | `0001` 异步任务模型、`0002` 产物传递与可追溯性 |
| `A07-ADR-NNN` | A07 单方决策 | 见 A07 仓库 `docs/adr/` |
| `PAIR07-ADR-NNN` | A07 / B07 **共同决策** | `001` ADR 命名空间、`002` 产物读取入口与保留策略、`003` 错误码前缀分域、`004` 公共 Schema 版本同步 |
