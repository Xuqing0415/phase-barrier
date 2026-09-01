# phase-barrier kind e2e 镜像：本地代码 + pytest（sidecar 内 GateClient 全链路验证）
# 构建上下文为仓库根；无 .git，故用 SETUPTOOLS_SCM_PRETEND_VERSION 固定版本号。
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}
COPY pyproject.toml README.md ./
COPY anti_shortcut ./anti_shortcut
RUN pip install --no-cache-dir setuptools setuptools-scm wheel \
 && pip install --no-cache-dir --no-build-isolation . pytest
COPY deploy ./deploy
COPY examples ./examples
ENTRYPOINT ["python", "-m", "anti_shortcut"]