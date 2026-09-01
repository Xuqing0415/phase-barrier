# phase-barrier kind e2e 镜像：本地代码 + pytest（sidecar 与 GateClient 全链路验证）
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md ./
COPY anti_shortcut ./anti_shortcut
RUN pip install --no-cache-dir --no-build-isolation . pytest
COPY deploy ./deploy
COPY examples ./examples
ENTRYPOINT ["python", "-m", "anti_shortcut"]