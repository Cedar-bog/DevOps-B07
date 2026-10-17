ARG BASE_IMAGE=python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ARG DOCKER_CLI_IMAGE=docker:28.5.1-cli@sha256:9190b0613792e658a7783cf14b2d5ace5941bb68ede7276922ea36ee457d76ad

FROM ${DOCKER_CLI_IMAGE} AS dockercli

FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DRAFT_FIXTURE_DIR=/app/fixtures/draft

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gcc \
        git \
        libc6-dev \
        make \
        strace \
    && rm -rf /var/lib/apt/lists/* \
    && { \
        dpkg-query -W -f='${Package}=${Version}\n' \
            ca-certificates gcc git libc6-dev make strace; \
        docker --version; \
        python --version; \
        pip --version; \
    } > /opt/toolchain.lock

COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-dev.lock \
    && rm -f /tmp/requirements-dev.lock

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && install -d -o app -g app /app

COPY --chown=app:app services/draft/ /app/
COPY --chown=app:app fixtures/draft/ /app/fixtures/draft/

USER app
WORKDIR /app

ENTRYPOINT ["python", "-m", "draft.cli"]
