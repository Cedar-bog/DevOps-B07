# 测试样本 fixtures/

## 目录结构

```
fixtures/
├─── draft/                 # 生成可构建环境
│   ├─── main.c                 # 被测源码
│   ├─── Makefile               # 构建脚本
│   ├─── README.md              # 项目说明
│   ├─── Dockerfile.broken      # 失败候选
│   └─── Dockerfile.reference   # 参考成功
└─── md-fixer/              # 修复缺失依赖
    ├─── main.c                 # 被测源码
    ├─── config.h               # 缺失依赖
    ├─── unused.h               # 冗余依赖
    ├─── Makefile               # 缺陷声明
    ├─── fixed-md-report.json   # ORACLE 固定报告
    └─── styles/                # 四种声明风格
        ├─── target/                # 显式目标
        ├─── macro/                 # 宏
        ├─── hybrid/                # 混合
        └─── implicit/              # 隐式规则
```