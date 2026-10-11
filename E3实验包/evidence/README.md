# evidence — 实际命令与观察结果（占位）

存放真实运行证据，用于后续 E10（增量/全量报告一致性）、E12（接入真实数据联调）对比。

## 待填内容

```
evidence/linux-verified/
├── make-draft/          # DRAFT：make / ./hello 输出与退出码
├── docker-broken/       # 失败构建日志（make: not found）
├── docker-reference/    # 成功构建日志 + 镜像 ID + hello E3
├── mdfixer-before/      # 修复前：增量 1 / clean 2
└── mdfixer-after/       # 修复后：clean 新值、改头文件自动重建、拒绝恢复
```

## 要求

- 所有运行在 **Ubuntu 22.04** 上完成，记录 `make --version` / `gcc --version` / `strace --version`。
- 保留原始日志与退出码，区分「人工预期」与「实际观察」。
- 服务器就绪（E3）后填充。
