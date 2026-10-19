# DevOps-B07

南京大学 DevOps 教学实验 · B07 组仓库

Wiki：https://github.com/Cedar-bog/DevOps-B07/wiki

对接 A07 组仓库：https://github.com/jkiuiui0058/DevOps-A07

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
├─── reference/             # 参考资料
│   ├─── papers/                # 论文
│   └─── slides/                # 课件
├─── contracts/             # 接口契约
├─── design/                # 设计文件
├─── fixtures/              # 测试样本
├─── scripts/               # 脚本工具
└─── services/              # 服务
```

## 可重复工程环境（E4）

一台装好 Docker 与 Compose 的机器、`git`、`make`、`python3` 即可复现本组服务。

```bash
git clone <本组仓库地址>
cd <仓库>
cp .env.example .env && chmod 600 .env   # 填 STUDENT_ID=<学号>；需要连服务器时再填 HUWEI_*
make doctor
```

| 命令 | 作用 | 证据（`work/<UTC 时间戳>/`） |
|---|---|---|
| `make doctor` | 服务器、Git 身份、工具链、Docker 自检 | `env.json` |
| `make build` | 按 digest 固定基础镜像构建服务镜像 | `build.log`、`image.json`、`toolchain.lock` |
| `make test` | 容器内运行本组单元测试 | `test.log` |
| `make smoke` | 用 E3 样例 `fixtures/draft` 做冒烟测试 | `smoke.json` |
| `make scan` | 工作区、Git 历史、镜像三处密钥检查 | `secret-scan.txt` |
| `make all` | 依次执行上面五步，共用同一个证据目录 | 以上全部 |
| `make shell` | 进入服务容器（compose，挂载 `docker.sock`） | — |
| `make clean` | 删除服务镜像与 smoke 镜像 | — |
| `make lock` | 由 `requirements-dev.in` 重新生成带哈希锁文件 | — |

约定：

- 每次运行都新建 `work/<UTC 时间戳>/`，不覆盖旧记录；`work/`、`evidence/`、`.env` 均不入库。
- `STUDENT_ID` 与 `DOCKER_GID` 从 `.env` 读取，也可用命令行变量覆盖；`DOCKER_GID` 缺省时自动取 `docker.sock` 的组号。
- 镜像名为 `e4-draft:<学号>`，与 `compose.yaml` 的 `${STUDENT_ID}` 一致，组员之间互不覆盖。
- `make scan` 只输出命中的前 4 位与指纹，报告本身可直接归档。
