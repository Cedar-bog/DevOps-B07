# E4-B07 一键命令：可重复工程环境（服务 draft）
#
# 每次运行都新建 work/<UTC 时间戳>/ 作为证据目录，不覆盖旧记录；
# make all 依次执行这五步，共用同一个证据目录。
#   make doctor  -> env.json
#   make build   -> build.log  image.json  toolchain.lock
#   make test    -> test.log
#   make smoke   -> smoke.json
#   make scan    -> secret-scan.txt
#
# STUDENT_ID 与 DOCKER_GID 从 .env 读取，都可以用命令行变量覆盖；
# DOCKER_GID 缺省时自动取 docker.sock 的组号。

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.DEFAULT_GOAL := help

ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

PYTHON ?= python3
UV ?= uv
DOCKER ?= docker
COMPOSE ?= $(DOCKER) compose
SERVICE := draft
DOCKERFILE := services/draft/Dockerfile
TESTS_DIR := services/draft/tests
DOCKER_SOCK ?= /var/run/docker.sock

# .env 只用于补缺；命令行变量与环境变量优先，不把 .env 当成 make 语法加载。
# 只服务于 STUDENT_ID / DOCKER_GID 这两个纯字母数字值，因此顺带去掉 CR 与引号，
# 避免 Windows 上编辑过的 .env（CRLF 行尾）拼出带 \r 的镜像名。
env_get = $(shell sed -n 's/^[[:space:]]*$(1)[[:space:]]*=[[:space:]]*//p' $(ROOT)/.env 2>/dev/null | tail -n 1 | tr -d '\r\042\047')

STUDENT_ID := $(strip $(if $(strip $(STUDENT_ID)),$(STUDENT_ID),$(call env_get,STUDENT_ID)))
DOCKER_GID := $(strip $(if $(strip $(DOCKER_GID)),$(DOCKER_GID),$(call env_get,DOCKER_GID)))
DOCKER_GID := $(strip $(if $(strip $(DOCKER_GID)),$(DOCKER_GID),$(shell stat -c %g $(DOCKER_SOCK) 2>/dev/null || true)))
IMAGE ?= e4-draft:$(STUDENT_ID)

RUN_TS := $(shell date -u '+%Y-%m-%dT%H-%M-%SZ')
WORK_ROOT ?= $(ROOT)/work
RUN_ID := $(shell d='$(WORK_ROOT)/$(RUN_TS)'; n=0; while [ -e "$$d" ]; do n=$$((n + 1)); d='$(WORK_ROOT)/$(RUN_TS)-'"$$n"; done; basename "$$d")
RUN_DIR := $(WORK_ROOT)/$(RUN_ID)
EVIDENCE := work/$(RUN_ID)

.PHONY: all help doctor build test smoke scan shell clean lock config \
        require-student-id require-image

all: doctor build test smoke scan
	@cd $(ROOT)
	printf '\n==> 五步已完成，证据目录：%s\n' '$(EVIDENCE)'
	ls -1 '$(RUN_DIR)'

help:
	@printf '%s\n' \
	  'E4-B07 一键命令（每次运行新建 work/<UTC 时间戳>/，不覆盖旧记录）' \
	  '' \
	  '  make doctor   服务器、Git 身份与工具链自检        -> env.json' \
	  '  make build    构建服务镜像                       -> build.log  image.json  toolchain.lock' \
	  '  make test     容器内运行本组单元测试             -> test.log' \
	  '  make smoke    用 E3 样例做冒烟测试               -> smoke.json' \
	  '  make scan     工作区、Git 历史、镜像的密钥检查   -> secret-scan.txt' \
	  '  make all      依次执行上面五步，共用同一个证据目录' \
	  '' \
	  '  make shell    进入服务容器（compose，挂载 docker.sock）' \
	  '  make clean    删除服务镜像与 smoke 镜像' \
	  '  make lock     由 requirements-dev.in 重新生成锁文件' \
	  '  make config   打印本次解析出的变量，用于排查' \
	  '' \
	  '变量：STUDENT_ID=$(STUDENT_ID) DOCKER_GID=$(DOCKER_GID) IMAGE=$(IMAGE)'

config:
	@printf '%s\n' \
	  'ROOT=$(ROOT)' \
	  'PYTHON=$(PYTHON)' \
	  'DOCKER=$(DOCKER)' \
	  'STUDENT_ID=$(STUDENT_ID)' \
	  'DOCKER_SOCK=$(DOCKER_SOCK)' \
	  'DOCKER_GID=$(DOCKER_GID)' \
	  'IMAGE=$(IMAGE)' \
	  'RUN_ID=$(RUN_ID)' \
	  'RUN_DIR=$(RUN_DIR)'

$(RUN_DIR):
	@mkdir -p '$(RUN_DIR)'

require-student-id:
	@if [ -z '$(STUDENT_ID)' ]; then
	  printf '%s\n' 'STUDENT_ID 为空：在 .env 中写 STUDENT_ID=<学号>，或用 make <目标> STUDENT_ID=<学号> 覆盖。'
	  exit 1
	fi

require-image: require-student-id
	@if ! $(DOCKER) image inspect '$(IMAGE)' >/dev/null 2>&1; then
	  printf '%s\n' "镜像 $(IMAGE) 不存在，先执行 make build。"
	  exit 1
	fi

doctor: | $(RUN_DIR)
	@cd $(ROOT)
	printf '==> doctor  证据：%s/env.json\n' '$(EVIDENCE)'
	$(PYTHON) scripts/doctor.py --output '$(RUN_DIR)/env.json'

build: require-student-id | $(RUN_DIR)
	@set +e
	cd $(ROOT)
	printf '==> build   证据：%s/{build.log,image.json,toolchain.lock}\n' '$(EVIDENCE)'
	{
	  printf '# E4-B07 draft image build\n'
	  printf 'started_at_utc: %s\n' "$$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
	  printf 'run_id: %s\n' '$(RUN_ID)'
	  printf 'git_commit: %s\n' "$$(git rev-parse HEAD)"
	  printf 'image: %s\n' '$(IMAGE)'
	  printf 'dockerfile: %s\n' '$(DOCKERFILE)'
	  printf 'dockerfile_sha256: %s\n' "$$(sha256sum '$(ROOT)/$(DOCKERFILE)' | awk '{print $$1}')"
	  grep -E '^[[:space:]]*ARG (BASE_IMAGE|DOCKER_CLI_IMAGE)=' '$(ROOT)/$(DOCKERFILE)' || true
	  printf 'docker: %s\n' "$$($(DOCKER) --version)"
	  printf '\n'
	  $(DOCKER) build --progress=plain --no-cache -f '$(DOCKERFILE)' -t '$(IMAGE)' .
	} 2>&1 | tee '$(RUN_DIR)/build.log'
	rc=$$?
	printf 'exit_code: %s\n' "$$rc" | tee -a '$(RUN_DIR)/build.log'
	set -e
	test "$$rc" -eq 0 || exit "$$rc"

	$(DOCKER) image inspect '$(IMAGE)' > '$(RUN_DIR)/image.json'
	$(DOCKER) run --rm --entrypoint cat '$(IMAGE)' /opt/toolchain.lock > '$(RUN_DIR)/toolchain.lock'
	printf 'image_id: %s\n' "$$($(DOCKER) image inspect --format '{{.Id}}' '$(IMAGE)')" | tee -a '$(RUN_DIR)/build.log'
	printf 'result: 镜像构建成功，toolchain.lock 已导出\n' | tee -a '$(RUN_DIR)/build.log'

test: require-image | $(RUN_DIR)
	@set +e
	cd $(ROOT)
	printf '==> test    证据：%s/test.log\n' '$(EVIDENCE)'
	{
	  printf '# E4-B07 draft unit tests\n'
	  printf 'started_at_utc: %s\n' "$$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
	  printf 'run_id: %s\n' '$(RUN_ID)'
	  printf 'git_commit: %s\n' "$$(git rev-parse HEAD)"
	  printf 'image: %s\n\n' '$(IMAGE)'
	  if ! compgen -G '$(ROOT)/$(TESTS_DIR)/test_*.py' >/dev/null; then
	    printf '%s\n' '$(TESTS_DIR) 下没有 test_*.py：单元测试由 E4-B07-009 补齐。'
	    exit 1
	  fi
	  $(DOCKER) run --rm --entrypoint python '$(IMAGE)' -m pytest /app/tests -q -p no:cacheprovider
	} 2>&1 | tee '$(RUN_DIR)/test.log'
	rc=$$?
	printf 'exit_code: %s\n' "$$rc" | tee -a '$(RUN_DIR)/test.log'
	set -e
	test "$$rc" -eq 0 || exit "$$rc"
	printf 'result: 容器内单元测试通过\n' | tee -a '$(RUN_DIR)/test.log'

smoke: require-image | $(RUN_DIR)
	@cd $(ROOT)
	printf '==> smoke   证据：%s/smoke.json\n' '$(EVIDENCE)'
	args=()
	if [ -n '$(DOCKER_GID)' ]; then args+=(--group-add '$(DOCKER_GID)'); fi
	if [ -S '$(DOCKER_SOCK)' ]; then args+=(-v '$(DOCKER_SOCK):$(DOCKER_SOCK)'); fi
	$(DOCKER) run --rm "$${args[@]}" '$(IMAGE)' smoke > '$(RUN_DIR)/smoke.json'
	$(PYTHON) -c 'import json, sys; d = json.load(open(sys.argv[1], encoding="utf-8")); print("smoke_ok: {}".format(d["ok"])); print("exit_code: {}".format(d["observed"]["exit_code"])); print("signature_hit: {}".format(d["observed"]["log_has_signature"])); sys.exit(0 if d["ok"] else 1)' '$(RUN_DIR)/smoke.json'
	printf 'result: 冒烟测试符合预期（构建失败且命中签名）\n'

scan: | $(RUN_DIR)
	@cd $(ROOT)
	printf '==> scan    证据：%s/secret-scan.txt\n' '$(EVIDENCE)'
	STUDENT_ID='$(STUDENT_ID)' $(PYTHON) scripts/secret_scan.py \
	  --docker '$(DOCKER)' --output '$(RUN_DIR)/secret-scan.txt'

shell: require-student-id
	@cd $(ROOT)
	STUDENT_ID='$(STUDENT_ID)' DOCKER_GID='$(DOCKER_GID)' \
	  $(COMPOSE) run --rm --entrypoint bash $(SERVICE)

clean:
	@cd $(ROOT)
	$(DOCKER) image rm -f '$(IMAGE)' >/dev/null 2>&1 || true
	$(DOCKER) image ls --format '{{.Repository}}:{{.Tag}}' | grep -E '^e4-draft-smoke:' | xargs -r $(DOCKER) image rm -f >/dev/null 2>&1 || true
	printf 'result: 已删除 %s 与 e4-draft-smoke:* 镜像；磁盘仍不足时再执行 docker builder prune\n' '$(IMAGE)'

lock:
	@cd $(ROOT)
	$(UV) pip compile requirements-dev.in --universal --generate-hashes -o requirements-dev.lock
