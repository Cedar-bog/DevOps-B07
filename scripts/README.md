# 脚本 scripts/

## 目录结构

```
scripts/
├─── lib/                       # 共享工具
│   ├─── proc.py                    # 子进程与 Git 工具
│   ├─── evidence.py                # 证据与门禁共用函数
│   ├─── verify.sh                  # 验证脚本共用函数
│   └─── progress_filter.py         # DRAFT 进度过滤
├─── remote/                    # 远程执行
│   ├─── remote.sh                  # 远程命令包装
│   └─── remote-docker.sh           # 远程 Docker 包装
├─── ops/                       # 运维与检查
│   ├─── doctor.py                  # 环境自检
│   ├─── secret_scan.py             # 密钥扫描
│   └─── check_llm.py               # LLM 配置检查
├─── verify/                    # 阶段验证
│   ├─── verify-draft.sh            # DRAFT 验证
│   ├─── verify-md-fixer.sh         # MDFixer 验证
│   └─── auto-repair.sh             # 远程 DRAFT 自动修复
├─── gates/                     # 质量门禁
│   ├─── run_e7_gate.py             # DRAFT 测试门禁
│   └─── run_e10_gate.py            # MDFixer 三场景门禁
└─── admin/                     # 用户管理
    └─── b07_users.py               # Basic Auth 用户管理 CLI
```
