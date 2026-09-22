# DevOps-B07

南京大学 DevOps 教学实验 · B07组仓库

对接 A07 组，仓库地址：https://github.com/jkiuiui0058/DevOps-A07

双方在 E2 使用 `pair07` 作为 Artifact URI 的 pair 段。

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
└── docs/
    ├── references/             # 复现参考论文
    ├── tasks/                  # 课程实验要求
    ├── contracts/              # 接口契约
    │   ├── task.schema.json        # 统一任务模型 Schema
    │   ├── validate.py             # 失败输入校验脚本
    │   ├── examples/               # 任务请求/响应样例
    │   │   ├── full-check/             # A07 提供的 FULL_CHECK 样例
    │   │   ├── incremental-check/      # A07 提供的 INCREMENTAL_CHECK 样例
    │   │   └── invalid/                # 负例区（不计入正向校验）
    │   └── fixtures/               # 校验用例
    │       └── negative/               # 失败输入用例（来源指针 + 变异操作）
    └── design/                 # 设计记录
        ├── backlog.md              # 任务与验收条件
        ├── adr/                    # 架构决策记录（B07-ADR-* 单方 / PAIR07-ADR-* 双方共同）
        └── AI_USAGE.md             # AI 使用与验证记录
```

## 架构决策记录（ADR）

ADR 编号只保证仓库内唯一，跨组引用带组名前缀（如 `A07:ADR-002`、`B07:ADR-0002`）。

| 命名空间 | 归属 | 现有条目 |
|----------|------|----------|
| `B07-ADR-NNNN` | B07 单方决策 | `0001` 异步任务模型、`0002` 产物传递与可追溯性 |
| `A07-ADR-NNN` | A07 单方决策 | 见 A07 仓库 `docs/adr/` |
| `PAIR07-ADR-NNN` | A07 / B07 **共同决策** | `001` ADR 命名空间、`002` 产物读取入口与保留策略、`003` 错误码前缀分域、`004` 公共 Schema 版本同步 |
