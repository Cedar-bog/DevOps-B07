# 接口契约 contracts/

## 目录结构

```
contracts/
├─── task.schema.json       # 统一任务模型
├─── examples/              # 样例
│   ├─── draft/                 # 生成可构建环境
│   ├─── full-check/            # 全量依赖检测
│   ├─── incremental-check/     # 跨提交增量检测
│   ├─── repair/                # 修复缺失依赖
│   └─── job_query.json         # 查询端点
├─── conventions/           # 约定
│   ├─── status_error.json      # 状态与错误码
│   ├─── artifact.json          # 产物访问
│   └─── error_report.json      # MD 报告消费约束
├─── negative/              # 非法样例
└─── validate.py            # 校验脚本
```