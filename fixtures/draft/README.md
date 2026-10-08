# Tiny Greeting —— DRAFT 测试基线样例

- 对应任务：**E3-B07-001**（DRAFT 样本项目与两层成功判据）
- 来源：课程人工样例 **Tiny Greeting**（人工构造，非上游真实项目）
- 用途：作为 DRAFT（自动生成可构建环境）的最小被测项目；后续 `Dockerfile.broken` / `Dockerfile.reference`（E3-B07-002 / 003）均以本项目为输入

## 目录内容

| 文件 | 说明 |
|---|---|
| `main.c` | C 源码，标准输出一行 `hello E3` 后以退出码 0 结束 |
| `Makefile` | GNU Make 构建脚本，编译 `main.c` 生成可执行文件 `hello` |
| `README.md` | 本文件：构建与验证命令、成功判据、环境与版本记录 |

## 构建与验证

构建（生成可执行文件 `hello`）：

```sh
make
```

功能验证（按 README 约定）：

```sh
./hello
```

### 两层成功判据

DRAFT 样本的成功需要**两层都满足**，仅“编译通过”不足以判定成功：

1. **第一层：编译通过**
   - `make` 的退出码为 `0`；
   - 预期可执行文件 `hello` 确实被生成（`test -x ./hello`）。
2. **第二层：功能验证通过**
   - 运行 `./hello` 的退出码为 `0`；
   - 标准输出恰为一行 `hello E3`。

### 预期结果

```
$ make
$ echo $?
0
$ ls -l hello
$ ./hello
hello E3
$ echo $?
0
```

## 环境与工具版本（本仓库实测记录）

| 项 | 值 |
|---|---|
| 操作系统 | Ubuntu 26.04.1 LTS（WSL2） |
| 内核 / 架构 | Linux 6.18.33.1-microsoft-standard-WSL2 / x86_64 |
| GNU Make | 4.4.1 |
| C 编译器 | cc / gcc (Ubuntu 15.2.0-16ubuntu1) 15.2.0 |
| Python（实验包脚本用） | 3.14.4 |

## 源码版本与真实 SHA

本项目随仓库 `DevOps-B07` 一同保存，不依赖外部上游仓库。基准信息如下：

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/jkiuiui0058/DevOps-B07 |
| 基准提交（本文件生成时的 `HEAD`） | `7c0ec6ea67ca8d9ba239dae9b66bd66744bf99a4` |
| `main.c` Git blob SHA | `32a9724ebd42a2bd5a3621f408837ce7a8a9196b` |
| `Makefile` Git blob SHA | `1482981f06f5d5afd9f499b08b08a10af4bf7c07` |
| `main.c` SHA-256 | `6be27e6e47cd782efd0c6ddb183285b04feda4ee5938fda24874a11f9353a910` |
| `Makefile` SHA-256 | `8c79ebb76adc49c94941a5377c067c9f9c442636da24597c6d071c5a83c314d2` |

> 上述 SHA 为内容哈希，用于核验样例未被改动；即使尚未提交，Git blob SHA 与 SHA-256 亦可作为“真实 SHA”。

## 复现步骤

```sh
cd fixtures/draft
make            # 退出码应为 0，生成 ./hello
./hello         # 应输出 hello E3，退出码 0
make clean      # 清理 hello 与 main.o，使样例可重复运行
```

> 本样例为人工构造的基线；人工标签（ORACLE）与后续工具真实运行日志须分开保存，不得将人工预期计入工具准确率。
