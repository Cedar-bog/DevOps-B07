# MDFixer 固定样例 —— 缺失依赖报告与四种声明风格

- 对应任务：**E3-B07-004**（MDFixer 固定 MD 报告与 Makefile）、**E3-B07-005**（四种声明风格与参考 Patch）
- 来源：人工构造的固定样例（非上游真实项目），作为 MDFixer 的 ORACLE 输入基线
- 用途：为 MDFixer（缺失依赖修复）提供固定输入、人工标签（ORACLE）与参考修复补丁；E3-B07-006 行为验证以此样例为输入

> 本样例为人工构造基线；人工标签（ORACLE）与后续工具真实运行日志须分开保存，不得将人工预期计入工具准确率。

## 目录内容

| 文件 / 目录 | 说明 |
|---|---|
| `main.c` | C 源码，`#include "config.h"` 并使用宏 `VALUE` 输出 |
| `config.h` | 被 `main.c` 依赖的头文件（**MISSING**：Makefile 未声明） |
| `unused.h` | 未被 `main.c` 使用的头文件（**REDUNDANT**：Makefile 声明了但源码未用） |
| `Makefile` | 基础（有缺陷）Makefile：`main.o: main.c unused.h`（缺 `config.h`、冗余 `unused.h`） |
| `fixed-md-report.json` | E3-B07-004 固定 MD 报告（ORACLE，`detector=INSTRUCTOR_ORACLE`） |
| `styles/` | E3-B07-005 四种声明风格，各含 `Makefile.before` 与 `reference.patch` |
| `README.md` | 本文件：场景设定、判据、验证命令、真实 SHA |

## 场景设定（缺陷模型）

源码只有一个编译目标 `main.o`，它有两个依赖问题：

1. **缺失依赖（MISSING）**：`main.c` 里写了 `#include "config.h"`，但 Makefile 中
   `main.o` 目标（`main.o: main.c unused.h`）没有把 `config.h` 列为依赖。后果是：
   修改 `config.h` 后 `make` 不会重建 `main.o`，输出仍是旧值。
2. **冗余依赖（REDUNDANT）**：Makefile 的 `main.o` 目标声明了 `unused.h`，但 `main.c`
   根本没有包含它。这是声明了但未使用的依赖，**不在 MDFixer 修复范围**，应被忽略。

> 按 E2-B07-004 消费约束，MDFixer（REPAIR）只消费 `MISSING` 类型，`REDUNDANT` 应忽略而非处理。

## 固定 MD 报告（E3-B07-004）

产物：`fixed-md-report.json`。报告字段满足 E2-B07-004 消费约束的必填项：

| 字段 | 值 | 说明 |
|---|---|---|
| `type` | `MISSING` | 修复范围（`REDUNDANT` 记录仅用于登记、被忽略） |
| `target` | `main.o` | 存在缺失依赖的构建目标 |
| `dependency` | `config.h` | 需要追加到目标声明的依赖 |
| `commit` | `dea937f3df3de19d1f0003adf4ae8abb3275221e` | 引入该 Makefile 的提交，报告与源码同一版本 |
| `position.file` | `fixtures/md-fixer/Makefile` | 声明所在 Makefile（相对仓库根目录） |
| `position.line` | `14` | `main.o: main.c unused.h` 所在行 |
| `position.declaration` | `main.o: main.c unused.h` | 声明文本，辅助风格识别 |
| `detector` | `INSTRUCTOR_ORACLE` | 人工标签，不计入工具准确率 |

报告含两条 finding：`MISSING-main-o-config-h`（带 `position`）与
`REDUNDANT-main-o-unused-h`（按约定无 `position`）。

## 四种声明风格（E3-B07-005）

`styles/` 下四种风格对应 `main.o` 目标声明的四种写法。每种风格一个 `Makefile.before`
（缺失 `config.h`）与一份 `reference.patch`（补上 `config.h`），补丁**不改变原声明风格**，
只做最小改动。

| 风格 | 目录 | 声明写法（before） | reference.patch 改了什么 |
|---|---|---|---|
| Target（显式目标） | `styles/target/` | `main.o: main.c` | 依赖列表追加 `config.h` → `main.o: main.c config.h` |
| Macro（宏） | `styles/macro/` | `DEPS := main.c` + `main.o: $(DEPS)` | 宏定义追加 `config.h` → `DEPS := main.c config.h` |
| Hybrid（混合） | `styles/hybrid/` | `HEADERS :=` + `main.o: main.c $(HEADERS)` | 填充宏 → `HEADERS := config.h` |
| Implicit（隐式规则） | `styles/implicit/` | `%.o: %.c`（无头文件依赖跟踪） | 加 `-MMD -MP`、`-include $(DEPS)` 自动依赖跟踪 |

### 怎样判断「修复有效」

对每种风格，修复是否有效不只看补丁能否应用，还要看**功能层面**依赖是否真正生效：

1. **补丁干净应用**：`git apply --check reference.patch` 退出码为 `0`（对 `Makefile.before`）。
2. **声明风格不变**：应用后 `main.o` 目标的声明风格与 before 一致（Target 仍是显式
   `main.o: …`，Macro 仍走 `$(DEPS)`，Hybrid 仍走 `$(HEADERS)`，Implicit 仍走 `%.o: %.c`），
   只是补上了 `config.h` 依赖。
3. **行为可观察**（E3-B07-006 在服务器验证）：修复前，只改 `config.h`（如 `VALUE` 由 1 改 2）
   后 `make` **不重建**，`./hello` 仍输出旧值；应用参考补丁后，改 `config.h` 再 `make` 会
   自动重建并输出新值。这是「缺失依赖已修复」的最终判据。

## 验证命令

在仓库根目录执行：

```sh
# 四种参考补丁均可干净应用（对各自的 Makefile.before）
cd fixtures/md-fixer/styles/target     && git apply --check reference.patch && cd -
cd fixtures/md-fixer/styles/macro      && git apply --check reference.patch && cd -
cd fixtures/md-fixer/styles/hybrid     && git apply --check reference.patch && cd -
cd fixtures/md-fixer/styles/implicit   && git apply --check reference.patch && cd -
```

构建与功能验证（基础 Makefile 本身可编译运行，缺失依赖为「改头文件不重建」的隐性缺陷）：

```sh
cd fixtures/md-fixer
make            # 退出码 0，生成 ./hello
./hello         # 输出 1（VALUE=1）
make clean
```

## 验证状态

- **已本地验证**：四份 `reference.patch` 均通过 `git apply --check`。
- **E3-B07-006 已完成**：`bash scripts/verify-md-fixer.sh` 在 WSL Ubuntu 22.04
  中验证了修复前的陈旧输出、四种声明风格修复后的自动重建、再次修改头文件后的自动重建，
  以及无效候选拒绝和样例副本恢复。实际命令与输出分别保存在
  `evidence/md-fixer/apply.log`、`before.log`、`after.log`、`reapply.log` 和
  `rejected.log`；这些工具日志与本样例的 ORACLE 标签分开保存。

## 源码版本与真实 SHA

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/jkiuiui0058/DevOps-B07 |
| 基准提交（引入本样例源码与风格的 `HEAD`） | `dea937f3df3de19d1f0003adf4ae8abb3275221e` |
| `main.c` Git blob SHA | `f63388456898baae50b2ed5b9823e3b7fcaf93a7` |
| `main.c` SHA-256 | `f3834cb89a3d12451e993ea2d6e762a2a56184f5c215868129259b9da7931aeb` |
| `config.h` Git blob SHA | `81f0de950364be04fa3ebccc3288548d54a445ac` |
| `config.h` SHA-256 | `16b4dfbcd633bc40c459d018a906d930f6735ace65d81beda186e464c675e2cb` |
| `unused.h` Git blob SHA | `0afea29915494125aaea6a3c57d3382d06d1060e` |
| `unused.h` SHA-256 | `60b3a3070f693c01e60f5cc8a7c16d72cd6d78d61e9342a87be63f7ac02c6933` |
| `Makefile` Git blob SHA | `5f7c615d6778c123c5416393a4a60cc6437677a0` |
| `Makefile` SHA-256 | `57f445890045a1025b512bc5f3325ddd81f8c1920f450e30fed650420b9f71e2` |

> 上述 SHA 为内容哈希，用于核验样例未被改动；即使尚未提交，Git blob SHA 与 SHA-256 亦可作为「真实 SHA」。
