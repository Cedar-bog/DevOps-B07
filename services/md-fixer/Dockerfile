ARG BASE_IMAGE=python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN sed -i 's|deb.debian.org|mirrors.huaweicloud.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gcc \
        git \
        libc6-dev \
        make \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && install -d -o app -g app /app

COPY --chown=app:app services/md-fixer/ /app/
COPY --chown=app:app services/draft/draft/llm.py /app/draft/llm.py
COPY --chown=app:app services/draft/draft/__init__.py /app/draft/__init__.py

USER app
WORKDIR /app

ENTRYPOINT ["python", "-m", "mdfixer.api"]
