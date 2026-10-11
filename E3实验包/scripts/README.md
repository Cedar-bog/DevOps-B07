# scripts — 自动化脚本（占位）

课程未提供实验包脚本，B07 自建。

## 待填文件

```
scripts/
├── run_lab.py          # 一键预演：复制 fixture 到 work/本次运行/，执行构建与行为断言
└── verify_docker.py    # Docker 验证：build/run broken 与 reference，抓退出码、日志、镜像 ID
```

## 目标

- 每次运行创建新目录（`work/<本次运行>/`），保留旧证据。
- 输出 `observations.json` 与 `commands.json`（命令 + 实际观察，供 E10/E12 对比）。
- `run_lab.py` 与 `verify_docker.py` 都在服务器（E3 就绪）执行。
