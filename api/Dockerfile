ARG CODEX_VERSION=0.146.0

FROM node:20-bookworm-slim AS codex-cli
ARG CODEX_VERSION
RUN npm install --global "@openai/codex@${CODEX_VERSION}" \
    && codex --version

FROM python:3.12-slim AS python-runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python-runtime AS stateless

FROM python-runtime AS stateful

ENV CODEX_HOME=/app/.codex

COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai

RUN ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && mkdir -p /app/.codex /tmp/cv-codex-work
