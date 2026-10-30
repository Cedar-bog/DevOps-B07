# DevOps-B07

南京大学 DevOps 教学实验 · B07 组仓库

Wiki：https://github.com/Cedar-bog/DevOps-B07/wiki

对接 A07 组仓库：https://github.com/jkiuiui0058/DevOps-A07

## 小组成员

| 学号      | 姓名   | GitHub    |
|-----------|--------|-----------|
| 241250022 | 姚睿哲 | yaorz26   |
| 241250026 |  郑悫  | Cedar-bog |
| 241250064 | 王师师 | Shishi-w  |
| 241250103 |  刘雯  | Gogo1101  |

## 仓库结构

```
DevOps-B07/
├─── reference/             # 参考资料
│   ├─── papers/                # 论文
│   └─── slides/                # 课件
├─── contracts/             # 接口契约
├─── design/                # 设计文件
├─── fixtures/              # 测试样本
├─── scripts/               # 脚本工具
├─── services/              # 服务
│
│ 以下内容以压缩包提交，不进入仓库
├╌╌╌ evidence/              # 阶段实验证据
└╌╌╌ work/                  # 服务/工具运行产物
```

## DRAFT 自动修复（E6）

在仓库根目录设置 `PYTHONPATH=services/draft` 后运行：

```sh
python -m draft.cli repair --max-iterations 3 --json
```

Windows PowerShell 可先执行 `$env:PYTHONPATH = "services/draft"`；Linux/macOS
可使用 `export PYTHONPATH=services/draft`。使用仓库虚拟环境中的 Python，
或先按 `requirements-dev.lock` 安装开发依赖。LLM 配置从环境变量或仓库根目录
`.env` 读取，环境变量优先；配置项见 `.env.example`。

服务先构建 `fixtures/draft/Dockerfile.broken`，用实际失败日志、当前 Dockerfile
和 README 生成提示词。每轮提取至多三个候选，检查基础镜像、指令顺序和运行命令，
再按 R1 历史去重、R2 归一化多数、R3 错误关键词、R4 序号选择候选。
构建成功后运行镜像，要求退出码为 0 且标准输出与 `hello E3` 匹配。
运行容器使用独立名称、禁用网络并限制 CPU、内存和进程数；每次验证后清理，
超时立即结束修复，结果中记录清理是否成功。

已有候选和离线回放均跳过模型调用，仍执行真实 Docker 构建和运行验证：

```sh
python -m draft.cli repair --candidate fixtures/draft/Dockerfile.reference
python -m draft.cli repair --replay --max-iterations 3 --json
```

`--candidate` 与 `--replay` 互斥；回放依次读取 `fixtures/ci-candidates/*.Dockerfile`，
先尝试只安装 Make 的候选，再尝试完整编译依赖。两种模式的 `input.prompt_mode`
均记为 `skipped`。候选耗尽后复用最后一份回复，由历史去重规则拒绝重复构建。

默认将完整证据写入当前目录的 `work/<UTC 时间戳>/`，同秒运行追加后缀。
可用 `--out PATH` 指定一个尚不存在的目录。初始构建保存在 `initial/`，
每轮保存在 `iter-NN/`，包括提示词、原始回复、候选检查、选择依据、构建日志和
运行验证日志。`result.json` 每轮更新，日志以相对路径引用；fixture 原文件保留，
构建使用独立副本。结果记录模型和去掉查询参数的地址，不记录 API 密钥。

其他参数见 `python -m draft.cli repair --help`。退出码为：成功 `0`，
失败或超时 `1`，参数、目录或 LLM 配置错误 `2`。`--docker` 接受单个可执行文件
路径，不接受包含参数的命令字符串。Windows 下不能直接传入
`scripts/remote-docker.sh`；服务器场景应在服务器上运行，或由 Makefile 环境提供
可执行的 Docker 入口。

无需 Docker 或 LLM 的本地验证：

```sh
python -m pytest -q
```

上述测试使用 fake LLM 和假命令执行器验证修复流程。E6-010 的真实环境实验
需另外在具备 Docker 的环境执行并归档 `work/`。
