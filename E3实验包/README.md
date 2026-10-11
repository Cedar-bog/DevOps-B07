# E3 并行测试基线（B07 自建实验包）

课程未提供实验包，本目录由 B07 自建。E3 目标：为 B07 的两个工具准备「可判断对错」的
测试基线 —— 固定项目版本、写明本次修改/故障、预期输出与判断依据、可重跑命令、实际运行记录。
E12 再接入 A07 真实数据联调。

> **E3 范围**：只准备测试基线（输入 + 预期 + 证据），**不实现** DRAFT/MDFixer 工具本身。
> 工具实现在 E5（BuildChecker / DRAFT）与 E8（EChecker / MDFixer）；E10 比对工具报告与人工预期。
> 所有参考答案均为人工标注（INSTRUCTOR_ORACLE），不计入工具准确率。

> 当前状态：**目录骨架**，各 fixture 文件逐个填充后更新下方清单。

## 目录结构

```
E3实验包/
├── fixtures/
│   ├── draft/        # DRAFT 输入样本（hello E3 项目）
│   └── md-rd/        # MDFixer 输入样本（app 读 config.h、Makefile 漏声明）
├── scripts/          # run_lab.py / verify_docker.py（自建）
├── work/             # 真实 Git 提交与参考修复
└── evidence/         # linux-verified 实际命令与观察结果
```

## 两个项目（按 PPT 拆开）

| 项目 | 服务工具 | 核心场景 |
|------|----------|----------|
| `fixtures/draft/` | DRAFT | 构建 `make`、验证 `./hello` → `hello E3`；broken/reference 双 Dockerfile |
| `fixtures/md-rd/` | MDFixer | `main.o` 缺 `config.h`（MD），`unused.h` 冗余声明（RD）；固定 MD 报告 + 参考补丁 |

## 任务清单（自行认领，填写负责人）

| # | 任务 | 验收条件 | 负责人 |
|---|---|---|---|
| 1 | 环境基线：服务器 Ubuntu 22.04 就绪 + 记录工具版本 | `make --version`、`gcc --version`、`git --version` 记录在案 | |
| 2 | DRAFT 项目：main.c / Makefile / README | `make && ./hello` 输出 `hello E3`、退出码 0；保存构建日志（make 输出+退出码）；验证失败仍要记录并继续修正 | |
| 3 | DRAFT 双 Dockerfile | broken 缺工具链（`make: not found`）/ reference 装 gcc make；记录两者 diff；reference 容器内 `./hello` 通过同一验证；构建在服务器验证 | |
| 4 | MDFixer 项目：main.c / config.h / unused.h / Makefile.before | Makefile.before 漏 config.h、多 unused.h | |
| 5 | MDFixer 修复前行为验证 | 改 config.h 后增量仍出旧值 1；clean 后出 2 | |
| 6 | 固定 MD 报告 + reference.patch | fixed-md-report.json 标 INSTRUCTOR_ORACLE；patch 可 git apply --check | |
| 7 | 四种声明风格 | target/macro/hybrid/implicit 各一份等价修复 | |
| 8 | MDFixer 修复后行为验证 | patch 后 clean 出新值、改头文件自动重建、无效候选拒绝+恢复 | |
| 9 | 脚本：run_lab.py / verify_docker.py | 一键预演留痕；Docker 验证脚本就位 | |
| 10 | 服务器复现 | Ubuntu 22.04 跑全量，填 evidence/linux-verified/ | |
| 11 | 提交 | 根 README 更新 + feature 分支 + PR | |
| 12 | 提交前自检 | ①别人能按 README 重跑 ②人工答案与实际日志分清 ③预期能说明依据 ④失败记录能定位到具体版本（记录 commit SHA） | |

## 环境基线

- 本地（Windows）：搭框架、写文件。
- 服务器：Ubuntu 22.04，**E3 就绪**；make/gcc/docker 验证与 evidence 都在服务器上跑。
- 记录 `make --version` / `gcc --version` / `git --version`。
