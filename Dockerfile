# MiniDevAgent — 研发团队 AI 值守工程师
# 多阶段构建：builder 安装依赖 → runtime 运行服务

FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime ──────────────────────────────────────────────────────

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="MiniDevAgent"
LABEL org.opencontainers.image.description="PERR-based AI engineer for dev teams"
LABEL org.opencontainers.image.version="2.0"

RUN groupadd -r agent && useradd -r -g agent agent

WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ ./src/
COPY config/ ./config/
COPY demo_project/ ./demo_project/

RUN chown -R agent:agent /app
USER agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "src/main.py", "demo_project", "--serve", "--port", "8000"]
