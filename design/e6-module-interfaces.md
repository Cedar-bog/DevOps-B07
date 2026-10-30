# E6 模块接口约定（DRAFT 自动修复）

- 覆盖任务：E6-003 ~ E6-009（`docker.py`、`verify.py`、`prompt.py`、`candidate.py`、`result.py`、`pipeline.py`、`cli.py`）
- 上游已定：E6-001 `.env.example`、E6-002 `draft/llm.py`、E7-001 `tests/fake_llm.py`、E4 `draft/smoke.py`
- 文档性质：**实现前的接口冻结**。写完这一份，六个模块才能各写各的、拼起来不返工。
- 说明来源：论文步骤与 R1–R4 取自 E5/E6/E7 课件（`reference/slides/`）；状态与错误码词汇取自本仓 `contracts/task.schema.json` 与 `contracts/conventions/status_error.json`（E2 已定，不另行发明）。

---

## 一、依赖方向

```
        llm.py（E6-002，已存在）
              ↑
prompt.py   candidate.py          ← 纯函数，不碰 docker、不碰网络
       ╲     ╱
        ╲   ╱
       pipeline.py  ←──────────────┐
         ↑   ↑   ↑                 │
   docker.py  verify.py         result.py
         ↑        │                 ↑
         └────────┘                 │
                 cli.py ────────────┘
```

硬约束：

| 规则 | 理由 |
|---|---|
| `prompt.py`、`candidate.py` 不 import 任何同包模块 | 纯函数，最容易测 |
| `docker.py`、`verify.py`、`prompt.py`、`candidate.py`、`result.py` 不 import `pipeline.py` | 禁止反向依赖 |
| `verify.py` 只能从 `docker.py` import `CommandResult` / `Runner` / `default_runner` | 复用注入点，不复制一份 |
| 只有 `pipeline.py` 能同时依赖以上五者 | 单一接合点 |
| `cli.py` 只依赖 `pipeline.py` + `result.py` + `smoke.py` | 命令行薄壳 |
| `smoke.py` 保持原样不动 | E4-009/010 已验收，不引入回归 |

---

## 二、共享约定

### 2.1 命令执行注入点

`smoke.py` 已有的 `Runner` 只回合并输出，验证需要分开 stdout 与 stderr。新模块统一用下面这份，定义在 `docker.py`，`verify.py` 从它 import。

```python
@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

Runner = Callable[[list[str], Path, float], CommandResult]

def default_runner(command: list[str], cwd: Path, timeout: float) -> CommandResult: ...
```

契约：

- runner **不抛** `subprocess.TimeoutExpired`：超时由 runner 自己转成 `timed_out=True, exit_code=-1`。
- 可执行文件不存在时 runner 抛 `FileNotFoundError`，由调用方转成 `DockerError`。
- 所有 `*Result` 的 `log` 字段都是 `stdout + stderr` 去掉 ANSI 转义与 `\r` 之后的结果（沿用 `smoke.py::_clean`）。

### 2.2 状态与错误码

`result.json` 的 `status` 与 `error.code` **直接用 E2 契约里的词**，不新造：

| 字段 | 取值 | 来源 |
|---|---|---|
| `status` | `SUCCEEDED` / `FAILED` / `TIMED_OUT` | `contracts/conventions/status_error.json` |
| `error.code` | `ENV_3002`（镜像构建失败）/ `EXEC_4002`（任务超时） | 同上 |
| `failure_class` | `NONE` / `MISSING_MAKE` / `APT_ERROR` / `NETWORK` / `TIMEOUT` / `OTHER` | 本轮新增，仅用于 `result.json` 的轮次明细，不进共享契约 |

判定顺序（`pipeline` 统一负责，别在别处重复判）：

1. 任一构建 `timed_out` 或验证 `timed_out` → `TIMED_OUT` + `EXEC_4002`
2. 最后一轮构建 `exit_code != 0` → `FAILED` + `ENV_3002`
3. 构建成功但验证 `output_matches == False` 或验证退出码非 0 → `FAILED`，`error.code = null`（不是构建失败，也不是超时，按课件 E7-10「输出不符」记 FAILED 但不套用构建错误码）
4. 达到 `max_iterations` 仍无成功 → `FAILED` + `ENV_3002`
5. 其余 → `SUCCEEDED`

### 2.3 时间与路径

- 时间戳一律 `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`，与 `smoke.py` 一致。
- 所有 `*_now` 参数是为了测试可注入，默认 `None` 时取当前 UTC。
- 目录名用 `work/<YYYY-MM-DDTHH-MM-SSZ>`，已存在时追加 `-1`、`-2`（**不覆盖**，沿用 `Makefile` 的既有做法）。

### 2.4 硬编码常量集中放

`docker.py`：`DEFAULT_BUILD_TIMEOUT = 900.0`
`verify.py`：`DEFAULT_VERIFY_TIMEOUT = 120.0`、`DEFAULT_CPU = "2"`、`DEFAULT_MEMORY = "1g"`、`DEFAULT_PIDS = 256`
`prompt.py`：`TAIL_LINE_LIMIT = 50`、`DEFAULT_KEYWORDS`（见 3.3）
`candidate.py`：`EXPECTED_CANDIDATES = 3`

---

## 三、逐模块

### 3.1 `services/draft/draft/docker.py`（E6-003）

```python
DEFAULT_BUILD_TIMEOUT = 900.0
LOG_TAIL_LIMIT = 20000
FAILURE_CLASSES = ("NONE", "MISSING_MAKE", "APT_ERROR", "NETWORK", "TIMEOUT", "OTHER")

class DockerError(Exception): ...

@dataclass(frozen=True)
class BuildResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    log: str
    image_tag: str
    image_id: str | None        # 仅 exit_code == 0 且 inspect 成功时有
    failed_step: str | None     # 例如 "RUN make"
    failure_class: str          # FAILURE_CLASSES 之一
    timed_out: bool
    started_at: str
    finished_at: str
    duration_s: float

    def ok(self) -> bool: ...   # exit_code == 0 and image_id is not None

def build_image(
    context_dir: Path | str,
    *,
    dockerfile: str = "Dockerfile",
    docker: str = "docker",
    tag: str | None = None,
    timeout: float = DEFAULT_BUILD_TIMEOUT,
    no_cache: bool = False,
    runner: Runner | None = None,
    now=None,
) -> BuildResult: ...

def image_id_of(tag: str, *, docker: str = "docker", runner: Runner | None = None) -> str | None: ...
def remove_image(tag: str, *, docker: str = "docker", runner: Runner | None = None) -> bool: ...
def classify_failure(log: str, exit_code: int, timed_out: bool) -> str: ...
def locate_failed_step(log: str) -> str | None: ...
```

要点：

- 命令行固定为 `[docker, "build", "--progress=plain", "-f", dockerfile, "-t", tag, "."]`，`cwd=context_dir`。`--progress=plain` 是**必需**的，否则日志不可解析（`#8 [4/4] RUN make` 这种行只在 plain 下出现）。`no_cache=True` 时追加 `--no-cache`。
- `tag` 缺省 `e6-draft-iter:<UTC 时间戳>`。
- 构建后单独调 `image_id_of(tag)`（`docker image inspect --format '{{.Id}}'`），不要依赖 `-q`——`-q` 会把日志吞掉。
- `locate_failed_step` 从日志里取最后一处 `#<n> [<i>/<m>] <指令>` 的指令部分。
- `classify_failure` 规则：`timed_out` → `TIMEOUT`；`exit_code == 0` → `NONE`；日志含 `make: not found` / `make: command not found` → `MISSING_MAKE`；含 `Unable to locate package` / `E: ` / `apt-get ... returned a non-zero code` → `APT_ERROR`；含 `Temporary failure resolving` / `i/o timeout` / `connection reset` / `TLS handshake timeout` → `NETWORK`；其余 `OTHER`。
- `context_dir` 不存在或缺 `dockerfile` → 抛 `DockerError`，不静默返回。

测试边界（假 runner，不需要 docker）：命令行构造、`no_cache` 开关、超时转 `TIMEOUT`、`MISSING_MAKE`/`APT_ERROR`/`NETWORK` 分类各一条、构建成功但 inspect 失败时 `image_id=None` 且 `ok()==False`。

### 3.2 `services/draft/draft/verify.py`（E6-004）

```python
DEFAULT_VERIFY_TIMEOUT = 120.0
DEFAULT_CPU = "2"
DEFAULT_MEMORY = "1g"
DEFAULT_PIDS = 256
CLEANUP_TIMEOUT = 60.0

@dataclass(frozen=True)
class VerifyResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    expected_output: str
    actual_output: str
    output_matches: bool
    timed_out: bool
    container_id: str | None
    container_name: str
    container_removed: bool
    duration_s: float

    def ok(self) -> bool: ...   # not timed_out and exit_code == 0 and output_matches

def run_image(
    image_id: str,
    *,
    expected_output: str,
    container_name: str,
    cidfile: Path | str | None = None,
    docker: str = "docker",
    network: str = "none",
    cpus: str = DEFAULT_CPU,
    memory: str = DEFAULT_MEMORY,
    pids_limit: int = DEFAULT_PIDS,
    timeout: float = DEFAULT_VERIFY_TIMEOUT,
    runner: Runner | None = None,
) -> VerifyResult: ...

def remove_container(container_name: str, *, docker: str = "docker", runner: Runner | None = None) -> bool: ...
def normalize_output(text: str) -> str: ...
def outputs_match(actual: str, expected: str) -> bool: ...
```

要点：

- 命令行：`[docker, "run", "--name", name, "--network", "none", "--cpus", cpus, "--memory", mem, "--pids-limit", str(n), "--cidfile", <path>, image_id]`。不加 `--rm`（否则超时后查不到容器、`container_removed` 无从核对），清理统一走 `remove_container`。`--cidfile` 直接落到 `iter-NN/verify-container-id.txt`，正好对上课件 E7-11 的记录要求。
- 超时后**必须**调 `remove_container`，把 `container_removed` 真实记下来（课件 E7-04「超时且运行容器已清理」是判定项）。
- `normalize_output`：去 `\r`、逐行 `rstrip`、去掉末尾空行。`outputs_match` 比归一化后的整串，不用 `in`——`in` 会把「hello E3 之外还有别的输出」判成匹配。
- 镜像 ID 为空或 `image_id` 不以 `sha256:` 开头 → 抛 `DockerError`。

测试边界：`--network none` 与四个资源限制都在命令行里；超时 → `timed_out=True` 且 `remove_container` 被调用一次；输出多一行空白仍判匹配；输出不符 → `ok()==False` 但 `exit_code==0` 仍照实记录。

### 3.3 `services/draft/draft/prompt.py`（E6-005）

```python
PROMPT_VERSION = "v1"
TAIL_LINE_LIMIT = 50
DEFAULT_KEYWORDS = (
    "error", "unknown", "missing", "not specified",
    "no such file or directory", "file not found",
    "failed to execute", "execution failed",
    "unable to locate package", "no matching distribution found for",
)

@dataclass(frozen=True)
class ErrorExcerpt:
    matched: list[tuple[int, str]]   # (行号, 原文)，从 1 开始
    tail: list[str]                  # 末 TAIL_LINE_LIMIT 行原文
    def render(self) -> str: ...

def locate_error(log: str, *, keywords=DEFAULT_KEYWORDS, tail_limit=TAIL_LINE_LIMIT) -> ErrorExcerpt: ...
def build_error_prompt(dockerfile_text: str, failure_log: str) -> str: ...
def build_context_prompt(dockerfile_text: str, failure_log: str, readme_text: str) -> str: ...
```

要点：

- 两类提示的**唯一差别**是上下文驱动多一段 README。构造上让 `build_context_prompt` 复用 `build_error_prompt` 的输出再追加，避免两份模板漂移。
- 关键词按小写子串匹配，行号保留原文件行号——课件 E5-23 要求结论能回到原始证据行。
- 提示里必须明确要求模型**输出 3 个候选**、每个候选一个 ```Dockerfile 围栏、不得改动运行命令。这条是 `candidate.py` 能提取到 3 份的前提，缺了会让 R2 空转。
- 文件头写 `PROMPT_VERSION`，便于把 `result.json` 里的提示版本对回某次实验。

测试边界：关键词命中行号正确；日志只保留末 50 行；两类提示的差异只在 README 段；提示含「3 个候选」与围栏样例。**断言用「包含某子串」，不做整串快照**——整串快照会在每次调措辞时无意义地崩。

### 3.4 `services/draft/draft/candidate.py`（E6-006）

```python
EXPECTED_CANDIDATES = 3

class CandidateError(Exception): ...

@dataclass(frozen=True)
class Candidate:
    index: int          # 1..3，按出现顺序
    text: str           # 去掉围栏后的 Dockerfile 正文
    sha256: str         # text 的 sha256（十六进制小写）
    normalized: str     # 续行归一化后的文本，仅用于 R2 比较
    fenced: bool        # 是否从 ``` 围栏里取到

@dataclass(frozen=True)
class Selection:
    candidate: Candidate
    rule: str           # "R1" / "R2" / "R3" / "R4"
    detail: str         # 一句话说明，写进 result.json
    rejected: tuple[tuple[int, str], ...]   # (候选序号, 拒绝原因)

def extract_candidates(reply: str, *, expected: int = EXPECTED_CANDIDATES) -> list[Candidate]: ...
def normalize_continuations(text: str) -> str: ...
def check_candidate(text: str, baseline: str) -> list[str]: ...   # 返回违规原因；空表示通过
def select_candidate(
    candidates: Sequence[Candidate],
    *,
    used_sha256: Container[str],
    error_terms: Sequence[str],
    chooser: Callable[[Sequence[Candidate]], int] | None = None,
) -> Selection: ...
```

`extract_candidates`：

- 先按 ``` 围栏切（语言标记可有可无）；不足 `expected` 时，用「连续以 `FROM`/`ARG`/`RUN`/`COPY`/`WORKDIR`/`CMD`/`ENV` 开头的块」兜底，`fenced=False`。
- 少于 `expected` 不抛错，交给 `select_candidate` 按减少的候选数走；一份都没有才抛 `CandidateError`。
- 候选文本统一去掉首尾空行、统一 `\n` 换行、保留行内空白——**不改内容**，因为 R1 的 `sha256` 要和历史比。

`normalize_continuations`：把行尾 `\` 续行拼成单行（保留一个空格）、删掉空行与纯注释行、行首尾去空白。课件 E5-27 的「候选 1 与 3 续行归一化后相同」就靠这个函数判定。

`check_candidate` 的检查项（对齐课件 E6-12「保留指令顺序与运行命令」）：

1. 首个非注释、非 `ARG` 指令必须是 `FROM`；`FROM` 参数与 baseline 完全相同（含 digest）。
2. baseline 里的 `WORKDIR`、`COPY`、`CMD`、`ENTRYPOINT` 必须原样出现，且相对顺序不变。
3. baseline 里那条 `RUN make`（即构建命令）必须保留；允许在它**之前**新增 `RUN`。
4. `CMD` 必须与 baseline 完全一致——课件 E7-10 的「无效候选：把 CMD 改成 `["echo","hello E3"]`」必须在这里被拒。
5. 不允许出现 baseline 没有的 `FROM`（多阶段构建暂不支持，直接拒）。

`select_candidate` 的规则链（**依次应用，第一个把候选压到 1 个的规则记为 `rule`**）：

| 规则 | 动作 | 未命中时 |
|---|---|---|
| R1 | 丢掉 `sha256 in used_sha256` 的候选 | 全部被丢 → 抛 `CandidateError`（本轮无可用候选，由 pipeline 记一轮失败） |
| R2 | 按 `normalized` 分组，若某组数量 ≥ 2 且唯一最多，取该组序号最小者 | 并列最多或多组 → 进入 R3 |
| R3 | 取 `text` 含 `error_terms` 任一子串（小写比较）的候选；只有 1 个命中才算定案 | 0 个或多个命中 → 进入 R4 |
| R4 | `chooser(剩余候选)` 返回 `index`；`chooser` 为 `None` 时取 `index` 最小者 | — |

`detail` 写清走到哪一步、为什么，例如 `"R2：3 份候选中 1 与 3 归一化后相同"`、`"R4：前三条未能决定，按序号取 2"`。这段文字会直接出现在 `result.json`，是课件要的「选择依据」。

测试边界：三候选围栏提取；续行归一化让 1 与 3 相等；`used_sha256` 排除后只剩一个（R1）；R2/R3/R4 各一条；CMD 被改 → `check_candidate` 返回非空；`chooser` 被调用时传入的是剩余候选而不是全部。

### 3.5 `services/draft/draft/result.py`（E6-008）

```python
RESULT_SCHEMA_VERSION = "1.0"
RESULT_FILENAME = "result.json"

@dataclass(frozen=True)
class RunPaths:
    root: Path
    project: Path
    def iter_dir(self, iteration: int) -> Path: ...   # root/iter-NN

@dataclass(frozen=True)
class RoundRecord:
    iteration: int
    dockerfile_sha256: str
    selected: dict            # {"index", "rule", "detail"}
    rejected: list[dict]      # [{"index", "reason"}]
    build: dict               # 见下
    verify: dict | None
    evidence: dict            # 路径，相对 root

def new_run_dir(work_root: Path | str, *, now=None) -> RunPaths: ...
def prepare_project(paths: RunPaths, fixture_dir: Path | str) -> Path: ...
def write_round(paths: RunPaths, round_record: RoundRecord) -> Path: ...
def write_result(paths: RunPaths, payload: dict) -> Path: ...
```

目录布局（`root` = `work/<时间戳>/`）：

```
work/<ts>/
├── result.json
├── project/              # fixture 的副本；每轮把该轮 Dockerfile 覆盖成 project/Dockerfile
├── iter-01/
│   ├── Dockerfile        # 本轮选中的候选（原文）
│   ├── prompt.txt
│   ├── reply.txt         # 模型原始回复，逐字
│   ├── candidates.json   # 3 份候选的 index / sha256 / 是否通过 check_candidate
│   ├── build.log
│   ├── build-exit-code.txt
│   ├── verify.log            # 仅当本轮构建成功
│   ├── verify-stderr.log
│   └── verify-container-id.txt
└── iter-02/ …
```

`result.json` 结构（字段名即契约，E7 与报告都按这个读）：

```jsonc
{
  "schema_version": "1.0",
  "service": "draft",
  "run_id": "2026-10-30T02-11-07Z",
  "status": "SUCCEEDED",              // SUCCEEDED / FAILED / TIMED_OUT
  "error": null,                       // 或 {"code": "ENV_3002", "message": "...", "details": {}}
  "input": {
    "fixture": "fixtures/draft",
    "initial_dockerfile": "Dockerfile.broken",
    "initial_sha256": "…",
    "prompt_mode": "context",          // error / context
    "prompt_version": "v1",
    "max_iterations": 3,
    "expected_output": "hello E3",
    "model": "deepseek-v4.1-flash",
    "endpoint": "https://api.example.com/v1"   // 已脱敏，绝不含 key
  },
  "rounds": [{
    "iteration": 1,
    "dockerfile_sha256": "…",
    "selected": {"index": 1, "rule": "R2", "detail": "R2：3 份候选中 1 与 3 归一化后相同"},
    "rejected": [{"index": 2, "reason": "R2：与多数不一致"}],
    "build": {
      "command": ["docker", "build", "…"],
      "exit_code": 1,
      "failure_class": "MISSING_MAKE",
      "failed_step": "RUN make",
      "timed_out": false,
      "duration_s": 12.4,
      "log": "iter-01/build.log"
    },
    "verify": null,
    "evidence": {
      "dockerfile": "iter-01/Dockerfile",
      "prompt": "iter-01/prompt.txt",
      "reply": "iter-01/reply.txt"
    }
  }],
  "final": {
    "image_id": "sha256:6bfd…",
    "verify": {
      "exit_code": 0,
      "expected_output": "hello E3",
      "actual_output": "hello E3\n",
      "output_matches": true,
      "container_id": "…",
      "container_removed": true,
      "duration_s": 3.1,
      "log": "iter-02/verify.log"
    }
  },
  "timing": {"started_at": "…", "finished_at": "…", "duration_s": 41.2}
}
```

约定：

- 日志正文**不内联**，只写相对路径。`result.json` 要能一眼读完，日志在旁边的文件里。
- 时间戳都带 `Z`，`run_id` 与目录名同源。
- `endpoint` 用 `llm.display_endpoint()` 处理，`api_key` 任何情况下不落盘——`scripts/secret_scan.py` 会扫仓库，虽然 `work/` 被忽略，但导出的压缩包会提交。

测试边界：同秒两次 `new_run_dir` 得到 `-1` 后缀；`write_result` 产物能 `json.loads` 且必填键齐全；`prepare_project` 不修改 `fixtures/draft` 原文件。

### 3.6 `services/draft/draft/pipeline.py`（E6-007）

```python
@dataclass(frozen=True)
class RepairOutcome:
    status: str                 # SUCCEEDED / FAILED / TIMED_OUT
    error_code: str | None      # ENV_3002 / EXEC_4002 / None
    message: str
    paths: RunPaths
    result_path: Path
    rounds: tuple[RoundRecord, ...]
    image_id: str | None
    verify: VerifyResult | None

    def ok(self) -> bool: ...

def run_repair(
    fixture_dir: Path | str,
    *,
    out_dir: Path | str | None = None,
    llm,                                 # 任何有 .chat(messages, *, timeout=None) -> LLMResponse 的对象
    initial_dockerfile: str = "Dockerfile.broken",
    prompt_mode: str = "context",        # "error" | "context"
    docker: str = "docker",
    max_iterations: int = 3,
    build_timeout: float = DEFAULT_BUILD_TIMEOUT,
    verify_timeout: float = DEFAULT_VERIFY_TIMEOUT,
    expected_output: str = "hello E3",
    no_cache: bool = False,
    chooser=None,
    runner: Runner | None = None,
    now=None,
) -> RepairOutcome: ...
```

一轮的流程（与课件 E5-11 的 ①–④ 对齐）：

1. `readme = (fixture/"README.md")`，`current = baseline 或上一轮选中的候选`
2. `prompt = build_context_prompt(current, last_failure_log, readme)`（`prompt_mode="error"` 时用 `build_error_prompt`）
3. `reply = llm.chat([{"role": "user", "content": prompt}]).text`，**原文落盘**到 `iter-NN/reply.txt`
4. `candidates = extract_candidates(reply)`；逐份 `check_candidate`，违规的记进 `rejected` 并排除
5. `selection = select_candidate(合法候选, used_sha256=历史, error_terms=从上一轮日志提取, chooser=chooser)`
6. 把 `selection.candidate.text` 写到 `project/Dockerfile`，`build_image(project)` 落 `iter-NN/build.log`
7. 构建失败 → 记一轮（`verify=null`），把日志当下一轮的输入，回到 1
8. 构建成功 → `run_image(image_id, expected_output=…)` 落 `verify.*`；`ok()` 则结束
9. 每轮结束都写一次 `result.json`（中途崩溃也留得下现场）

边界情形：

- `llm.chat` 抛 `LLMError` → 不重试，整体 `FAILED`，`message` 带原始错误（`llm.py` 已做 key 脱敏）。
- 本轮 `extract_candidates` 抛 `CandidateError`（零候选）→ 记一轮 `build` 为 `{"exit_code": null, "failure_class": "OTHER"}`，继续下一轮；连续零候选达到上限 → `FAILED`。
- 达到 `max_iterations` → 按 2.2 判 `FAILED` + `ENV_3002`。
- 第一次构建就成功（候选直接可用）也照常记一轮，不要特判。

测试边界（E7-002/004/005/006，全部走 `fake_llm` + 假 runner，不需要 docker）：

| 场景 | 断言 |
|---|---|
| 正常修复 | 第 1 轮构建 1 失败、第 2 轮构建 0 且输出匹配，`status=="SUCCEEDED"` |
| 候选构建失败 | 该轮 `verify is None`，且 `run_image` 一次都没被调用 |
| 输出不符 | 该轮 `output_matches=False`，继续下一轮，最终 `FAILED` 且 `error_code is None` |
| 运行超时 | `status=="TIMED_OUT"`、`error_code=="EXEC_4002"`、容器已清理 |

### 3.7 `services/draft/draft/cli.py`（E6-009）

新增子命令 `repair`，与已有 `version`、`smoke` 并列：

```
draft repair
  --fixture-dir PATH          # 默认 $DRAFT_FIXTURE_DIR 或 <repo>/fixtures/draft
  --out PATH                  # 缺省 work/<时间戳>/
  --initial-dockerfile NAME   # 默认 Dockerfile.broken
  --mode {error,context}      # 默认 context
  --max-iterations N          # 默认 3
  --build-timeout S           # 默认 900
  --verify-timeout S          # 默认 120
  --expected-output STR       # 默认 "hello E3"
  --docker NAME               # 默认 docker（本地直连；服务器场景传 scripts/remote-docker.sh 的相对路径不行，见下）
  --candidate PATH            # 跳过模型，直接用已保存的候选（E5 产物）
  --replay                    # 用 fixtures/ci-candidates/*.Dockerfile
  --no-cache
  --json                      # 把 result.json 打到 stdout
```

退出码：

| 码 | 含义 |
|---|---|
| 0 | `SUCCEEDED` |
| 1 | `FAILED` 或 `TIMED_OUT` |
| 2 | 参数错误、目录缺失、配置不全（与 `check_llm.py` 的 0/1/2 约定一致） |

两点必须写清楚：

- `--candidate` 与 `--replay` 都跳过 LLM 调用，走的仍是同一套「写 Dockerfile → 构建 → 验证」流程，`result.json` 里 `input.prompt_mode` 记为 `"skipped"`。E6-010 用 E5 的候选、E7 的 CI 用 replay，都从这个口进。
- `--docker` 只接受**可执行字符串**。走服务器时不能把 `scripts/remote-docker.sh` 塞进这个参数（那是 bash 脚本，Windows 上跑不了），正确做法是在服务器上直接跑，或者由 `Makefile` 注入。这条写进 `README` 的用法说明。

---

## 四、上下游对齐点（别自己改）

| 对齐点 | 归属 | 契约 |
|---|---|---|
| `llm.py` 的 `.chat(messages, *, timeout)` → `LLMResponse.text` | E6-002，已冻结 | `pipeline` 只认 `.text`，不解析 payload |
| `fake_llm.py` 的 `FakeLLM` / `fenced_dockerfile()` | E7-001，已冻结 | `FakeLLM().client()` 返回一个真 `LLMClient`（走假 transport），或直接把 `FakeLLM` 本身传给 `llm=`——两者 `.chat()` 签名一致。`replies` 支持传 callable，用来让第二轮换一批候选 |
| `fixtures/draft/Dockerfile.broken` 作为初始候选 | E3-002，已冻结 | `initial_dockerfile` 默认值指向它，不复制一份 |
| `contracts/task.schema.json` 的 DRAFT 分支 | E2-001，已冻结 | `result.json` 的 `status` / `error.code` 用它；**不**要求整份 result 通过 schema 校验（实验证据比 API 响应多字段） |
| `scripts/remote-docker.sh` | E4，已冻结 | `pipeline` 不知道服务器的存在，它只调 `docker` 这个可执行名 |
| `work/`、`evidence/` 不进仓库 | `.gitignore` + `README` | 运行产物压缩包提交，`result.json` 随包 |

---

## 五、落地顺序

接口冻结后按这个顺序推，每一步都能独立验证，不等别人：

| 步 | 内容 | 依赖 | 本地可验 |
|---|---|---|---|
| 1 | `docker.py` + 假 runner 单测 | 无 | 是 |
| 2 | `verify.py` + 假 runner 单测 | 1 | 是 |
| 3 | `prompt.py` + 单测 | 无 | 是 |
| 4 | `candidate.py` + 单测 | 无 | 是 |
| 5 | `result.py` + 单测 | 无 | 是 |
| 6 | `pipeline.py` + `fake_llm` 驱动的单测 | 1–5 | 是 |
| 7 | `cli.py` 的 `repair` 子命令 | 6 | 是（假 runner 路径） |
| 8 | 真机跑一次（E6-010） | 7 + 服务器 | **否，需服务器** |

第 1–7 步在本机就能做完并跑通测试，`make test` 不依赖 Docker。只有第 8 步与 E6 的验收（两轮构建、`image_id`、`hello E3`）必须在服务器上。
