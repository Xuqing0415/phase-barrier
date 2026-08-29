FROM python:3.12-slim

WORKDIR /app

# 安装反捷径校验 Skill（含 CLI：python -m anti_shortcut）
COPY pyproject.toml README.md ./
COPY anti_shortcut ./anti_shortcut
RUN pip install --no-cache-dir --no-build-isolation .

# 部署辅助脚本与示例
COPY deploy ./deploy
COPY examples ./examples

# 默认入口为 CLI（gate-keeper 使用）；agent 服务覆盖 command
ENTRYPOINT ["python", "-m", "anti_shortcut"]
