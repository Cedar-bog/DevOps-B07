# fixtures/draft — DRAFT 输入样本（占位）

DRAFT 工具（B 侧）：根据源码 + 构建/验证命令，自动生成可构建的 Docker 环境（Dockerfile）。

## 待填文件

```
fixtures/draft/
├── main.c                  # Tiny Greeting：打印 hello E3
├── Makefile                # 构建目标 hello
├── README.md               # 构建 make；验证 ./hello；记录项目版本与环境
├── Dockerfile.broken       # 故意缺工具链（python:3.13-slim 无 make/C 工具链）→ 必失败
└── Dockerfile.reference    # apt-get 装 gcc make libc6-dev → 成功
```

## 预期证据

| 场景 | 命令 | 预期 |
|------|------|------|
| 编译通过 | `make` | 退出码 0，生成可执行文件 |
| 功能验证 | `./hello` | 标准输出 `hello E3`，退出码 0 |
| 失败样例 | `docker build -f Dockerfile.broken` | 非零退出，日志可定位 `make: not found` |
| 参考成功 | `docker build -f Dockerfile.reference && docker run` | `hello E3`，记录镜像 ID |

## 备注

- 失败日志是后续 DRAFT 修复方法的输入，必须留存。
- 记录 commit、工具版本（`make --version` / `gcc --version`）。
- 本地搭框架写文件；`make && ./hello` 与双 Dockerfile 构建都在服务器（E3 就绪）验证。
