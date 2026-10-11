# fixtures/md-rd — MDFixer 输入样本（占位）

MDFixer 工具（B 侧）：消费固定 MD 报告，修复 Makefile 中缺失的依赖声明（`MISSING` 类型）。

## 待填文件

```
fixtures/md-rd/
├── main.c                  # include "config.h"；输出 VALUE
├── config.h                # #define VALUE 1
├── unused.h                # 被 Makefile 声明但 main.c 未读取（RD）
├── Makefile.before         # main.o: main.c unused.h  —— 漏声明 config.h
├── fixed-md-report.json    # INSTRUCTOR_ORACLE：main.o 缺 config.h（MISSING）
├── reference.patch         # 期望补丁：给 main.o 补 config.h
└── styles/                 # 四种声明风格参考修复
    ├── target/             # main.o: main.c config.h（直接补）
    ├── macro/              # main.o: $(DEPS)，在 DEPS 补 config.h
    ├── hybrid/             # main.c $(HEADERS)，更新头文件宏
    └── implicit/           # %.o: %.c + -MMD -MP 生成 .d 再 -include
```

## 预期证据（修复前后行为对照）

| 场景 | 命令 | 预期 |
|------|------|------|
| 修复前·增量 | 改 `config.h` 后 `make && ./app` | 仍输出旧值 `1`（漏重建） |
| 修复前·全量 | `make clean && make && ./app` | 输出 `2`（对照） |
| 修复后 | `git apply reference.patch` → clean → make | 输出新值 |
| 修复后·再改头文件 | 不 clean，改 `config.h` 后 `make` | 自动重建，输出新值 |
| 无效候选 | 故意加失败命令 → make 非零退出 | 拒绝该候选，恢复原 Makefile |

## 备注

- MD 与 RD 后果不同：MD 导致「旧产物」（漏重建），RD 导致「多余编译」（unused.h 改动触发 cc）。
- 人工报告标记 `provenance: INSTRUCTOR_ORACLE`，不用于计算工具准确率。
- 行为验证（1/2/3）在服务器（E3 就绪）跑，以 Linux 为准。
