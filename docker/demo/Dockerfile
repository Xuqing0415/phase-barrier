# phase-barrier 一键体验镜像（v0.26.0）
# 构建：docker build -t phase-barrier-demo .
# 运行：docker run --rm -it phase-barrier-demo
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/phase-barrier-demo

# 安装 phase-barrier 与 pytest（模拟 Agent 运行测试需要）
RUN pip install phase-barrier pytest

COPY agent_demo.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
