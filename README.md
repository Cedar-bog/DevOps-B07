# DevOps-B07

南京大学 DevOps 教学实验 · B07组仓库

对接A07组，仓库地址： _TODO_

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
    │   └── fixtures/               # 校验用例
    │       └── negative/               # 失败输入用例（来源指针 + 变异操作）
    └── design/                 # 设计记录
        ├── backlog.md              # 任务与验收条件
        ├── adr/                    # 架构决策记录
        └── AI_USAGE.md             # AI 使用与验证记录
```