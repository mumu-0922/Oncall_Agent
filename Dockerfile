# syntax=docker/dockerfile:1
# 参考 HolmesGPT 的 Python slim multi-stage 思路：builder 安装依赖，runtime 只保留运行所需文件。
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip wheel setuptools \
    && pip install .

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system oncall \
    && useradd --system --gid oncall --home-dir /app --shell /usr/sbin/nologin oncall

COPY --from=builder /venv /venv
COPY app ./app
COPY mcp_servers ./mcp_servers
COPY static ./static
COPY aiops-docs ./aiops-docs
COPY pyproject.toml README.md ./

RUN mkdir -p /app/logs /app/data/rag /var/log/oncall-agent \
    && chown -R oncall:oncall /app /var/log/oncall-agent

USER oncall

EXPOSE 9900 8003 8004

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9900"]
