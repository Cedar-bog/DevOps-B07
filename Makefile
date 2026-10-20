# E4-B07 一键命令：可重复工程环境（服务 draft）
#
# 命令在本地执行，docker 通过 .env 中的 HUWEI_* 在服务器上运行。
# 每次运行都新建 work/<UTC 时间戳>/ 作为证据目录，不覆盖旧记录；
# make all 依次执行五步，共用同一个证据目录。
#   make doctor  -> env.json
#   make build   -> build.log  image.json  toolchain.lock
#   make test    -> test.log
#   make smoke   -> smoke.json
#   make scan    -> secret-scan.txt
#
# STUDENT_ID 与 DOCKER_GID 从 .env 读取，都可以用命令行变量覆盖；
# DOCKER_GID 缺省时从服务器 docker.sock 读取。

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.DEFAULT_GOAL := help

ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

PYTHON ?= python3
UV ?= uv
REMOTE ?= $(ROOT)/scripts/remote.sh
DOCKER ?= $(ROOT)/scripts/remote-docker.sh
COMPOSE ?= $(DOCKER) compose
SERVICE := draft
DOCKERFILE := services/draft/Dockerfile
TESTS_DIR := services/draft/tests
DOCKER_SOCK ?= /var/run/docker.sock

# .env 只用于补缺；命令行变量优先。这里只读取纯文本值。
env_get = $(shell sed -n 's/^[[:space:]]*$(1)[[:space:]]*=[[:space:]]*//p' $(ROOT)/.env 2>/dev/null | tail -n 1 | tr -d '\r\042\047')

STUDENT_ID := $(strip $(if $(strip $(STUDENT_ID)),$(STUDENT_ID),$(call env_get,STUDENT_ID)))
DOCKER_GID ?= $(strip $(if $(strip $(call env_get,DOCKER_GID)),$(call env_get,DOCKER_GID),$(shell $(REMOTE) stat -c %g $(DOCKER_SOCK) 2>/dev/null || true)))
IMAGE ?= e4-draft:$(STUDENT_ID)

RUN_TS := $(shell date -u '+%Y-%m-%dT%H-%M-%SZ')
WORK_ROOT ?= $(ROOT)/work
RUN_ID := $(shell d='$(WORK_ROOT)/$(RUN_TS)'; n=0; while [ -e "$$d" ]; do n=$$((n + 1)); d='$(WORK_ROOT)/$(RUN_TS)-'"$$n"; done; basename "$$d")
RUN_DIR := $(WORK_ROOT)/$(RUN_ID)
EVIDENCE := work/$(RUN_ID)

.PHONY: all help doctor build test smoke scan shell clean lock config \
        require-student-id require-remote require-image require-gid

all: doctor build test smoke scan
	@cd $(ROOT)
	printf '\n==> 五步已完成，证据目录：%s\n' '$(EVIDENCE)'
	ls -1 '$(RUN_DIR)'

help:
	@printf '%s\n' \
	  'E4-B07 一键命令（本地执行，docker 在 .env 指定的服务器上运行）' \
	  '每次运行新建 work/<UTC 时间戳>/，不覆盖旧记录' \
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
	  '服务器：$(HUWEI_LABEL)' \
	  '变量：STUDENT_ID=$(STUDENT_ID) DOCKER_GID=$(DOCKER_GID) IMAGE=$(IMAGE)'

config:
	@printf '%s\n' \
	  'ROOT=$(ROOT)' \
	  'PYTHON=$(PYTHON)' \
	  'REMOTE=$(REMOTE)' \
	  'DOCKER=$(DOCKER)' \
	  'SERVER=$(HUWEI_USER)@$(HUWEI_HOST):$(HUWEI_PORT)' \
	  'STUDENT_ID=$(STUDENT_ID)' \
	  'DOCKER_SOCK=$(DOCKER_SOCK)' \
	  'DOCKER_GID=$(DOCKER_GID)' \
	  'IMAGE=$(IMAGE)' \
	  'RUN_ID=$(RUN_ID)' \
	  'RUN_DIR=$(RUN_DIR)'

HUWEI_HOST := $(call env_get,HUWEI_HOST)
HUWEI_PORT := $(or $(call env_get,HUWEI_PORT),22)
HUWEI_USER := $(call env_get,HUWEI_USER)
HUWEI_LABEL := $(HUWEI_USER)@$(HUWEI_HOST):$(HUWEI_PORT)

$(RUN_DIR):
	@mkdir -p '$(RUN_DIR)'

require-student-id:
	@if [ -z '$(STUDENT_ID)' ]; then
	  printf '%s\n' 'STUDENT_ID 为空：在 .env 中写 STUDENT_ID=<学号>，或用 make <目标> STUDENT_ID=<学号> 覆盖。'
	  exit 1
	fi

require-gid:
	@if [ -z '$(DOCKER_GID)' ]; then
	  printf '%s\n' 'DOCKER_GID 为空：在 .env 中写 DOCKER_GID=<服务器 docker.sock 组号>。'
	  exit 1
	fi

require-remote:
	@if [ ! -f '$(ROOT)/.env' ]; then
	  printf '%s\n' '.env 不存在：复制 .env.example 并填入 HUWEI_* 与 STUDENT_ID。'
	  exit 1
	fi
	@if [ ! -x '$(REMOTE)' ] || [ ! -x '$(DOCKER)' ]; then
	  printf '%s\n' '远程脚本缺失或不可执行：scripts/remote.sh / scripts/remote-docker.sh'
	  exit 1
	fi
	@if ! command -v sshpass >/dev/null 2>&1; then
	  printf '%s\n' '缺少 sshpass：sudo apt-get install -y sshpass'
	  exit 1
	fi

require-image: require-student-id require-remote
	@if ! $(DOCKER) image inspect '$(IMAGE)' >/dev/null 2>&1; then
	  printf '%s\n' "镜像 $(IMAGE) 不存在，先执行 make build。"
	  exit 1
	fi

doctor: | $(RUN_DIR)
	@cd $(ROOT)
	printf '==> doctor  证据：%s/env.json\n' '$(EVIDENCE)'
	STUDENT_ID='$(STUDENT_ID)' DOCKER_GID='$(DOCKER_GID)' \
	  $(PYTHON) scripts/doctor.py --docker '$(DOCKER)' --ssh '$(REMOTE)' \
	    --docker-gid '$(DOCKER_GID)' --output '$(RUN_DIR)/env.json'

build: require-student-id require-remote | $(RUN_DIR)
	@set +e
	cd $(ROOT)
	printf '==> build   证据：%s/{build.log,image.json,toolchain.lock}\n' '$(EVIDENCE)'
	STAGE="/tmp/e4-b07-build-$(RUN_ID)"
	{
	  printf '# E4-B07 draft image build\n'
	  printf 'started_at_utc: %s\n' "$$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
	  printf 'run_id: %s\n' '$(RUN_ID)'
	  printf 'git_commit: %s\n' "$$(git rev-parse HEAD)"
	  printf 'image: %s\n' '$(IMAGE)'
	  printf 'dockerfile: %s\n' '$(DOCKERFILE)'
	  printf 'dockerfile_sha256: %s\n' "$$(sha256sum '$(ROOT)/$(DOCKERFILE)' | awk '{print $$1}')"
	  grep -E '^[[:space:]]*ARG (BASE_IMAGE|DOCKER_CLI_IMAGE)=' '$(ROOT)/$(DOCKERFILE)' || true
	  printf 'server: %s\n' '$(HUWEI_LABEL)'
	  printf 'docker: %s\n\n' "$$($(DOCKER) --version 2>&1)"
	} > '$(RUN_DIR)/build.log'

	tar -C '$(ROOT)' -cf - services/draft fixtures/draft requirements-dev.lock \
	  | $(REMOTE) --shell "rm -rf '$$STAGE' && mkdir -p '$$STAGE' && tar -C '$$STAGE' -xf -" 2>&1 \
	  | tee -a '$(RUN_DIR)/build.log'
	stage_rc=$${PIPESTATUS[1]}

	if [ "$$stage_rc" -eq 0 ]; then
	  $(REMOTE) --shell "cd '$$STAGE' && docker build --progress=plain --no-cache -f $(DOCKERFILE) -t '$(IMAGE)' ." 2>&1 \
	    | tee -a '$(RUN_DIR)/build.log'
	  rc=$${PIPESTATUS[0]}
	else
	  rc=$$stage_rc
	fi
	$(REMOTE) --shell "rm -rf '$$STAGE'" >/dev/null 2>&1 || true

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
	  printf 'image: %s\n' '$(IMAGE)'
	  printf 'server: %s\n\n' '$(HUWEI_LABEL)'
	  if ! compgen -G '$(ROOT)/$(TESTS_DIR)/test_*.py' >/dev/null; then
	    printf '%s\n' '$(TESTS_DIR) 下没有 test_*.py。'
	    exit 1
	  fi
	  $(DOCKER) run --rm --entrypoint python '$(IMAGE)' -m pytest /app/tests -q -p no:cacheprovider
	} 2>&1 | tee '$(RUN_DIR)/test.log'
	rc=$${PIPESTATUS[0]}
	printf 'exit_code: %s\n' "$$rc" | tee -a '$(RUN_DIR)/test.log'
	set -e
	test "$$rc" -eq 0 || exit "$$rc"
	printf 'result: 容器内单元测试通过\n' | tee -a '$(RUN_DIR)/test.log'

smoke: require-image require-gid | $(RUN_DIR)
	@cd $(ROOT)
	printf '==> smoke   证据：%s/smoke.json\n' '$(EVIDENCE)'
	$(DOCKER) run --rm --group-add '$(DOCKER_GID)' -v '$(DOCKER_SOCK):$(DOCKER_SOCK)' '$(IMAGE)' smoke > '$(RUN_DIR)/smoke.json'
	$(PYTHON) -c 'import json, sys; d = json.load(open(sys.argv[1], encoding="utf-8")); print("smoke_ok: {}".format(d["ok"])); print("exit_code: {}".format(d["observed"]["exit_code"])); print("signature_hit: {}".format(d["observed"]["log_has_signature"])); sys.exit(0 if d["ok"] else 1)' '$(RUN_DIR)/smoke.json'
	printf 'result: 冒烟测试符合预期（构建失败且命中签名）\n'

scan: require-remote | $(RUN_DIR)
	@cd $(ROOT)
	printf '==> scan    证据：%s/secret-scan.txt\n' '$(EVIDENCE)'
	STUDENT_ID='$(STUDENT_ID)' $(PYTHON) scripts/secret_scan.py \
	  --docker '$(DOCKER)' --output '$(RUN_DIR)/secret-scan.txt'

shell: require-image require-gid | $(RUN_DIR)
	@cd $(ROOT)
	STAGE="/tmp/e4-b07-shell-$(RUN_ID)"
	tar -C '$(ROOT)' -cf - compose.yaml \
	  | $(REMOTE) --shell "rm -rf '$$STAGE' && mkdir -p '$$STAGE' && tar -C '$$STAGE' -xf -"
	rc=0
	$(REMOTE) --tty --shell "cd '$$STAGE' && STUDENT_ID='$(STUDENT_ID)' DOCKER_GID='$(DOCKER_GID)' docker compose -f compose.yaml run --rm --entrypoint bash draft" || rc=$$?
	$(REMOTE) --shell "rm -rf '$$STAGE'" >/dev/null 2>&1 || true
	exit $$rc

clean: require-remote
	@$(REMOTE) --shell "docker image rm -f '$(IMAGE)' >/dev/null 2>&1 || true; docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E '^e4-draft-smoke:' | xargs -r docker image rm -f >/dev/null 2>&1 || true"
	@printf 'result: 已删除服务器上的 %s 与 e4-draft-smoke:* 镜像\n' '$(IMAGE)'

lock:
	@cd $(ROOT)
	$(UV) pip compile requirements-dev.in --universal --generate-hashes -o requirements-dev.lock
