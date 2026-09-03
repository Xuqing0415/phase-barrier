# phase-barrier 一键体验镜像（Docker Demo）

无需本地安装 Python / Node，直接运行容器即可看到 phase-barrier 阶段门禁的
**拦截跳步** 与 **规范流程全通** 两个场景。

## 快速体验（已发布镜像）

```bash
docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo
```

## 本地构建

```bash
docker build -t phase-barrier-demo docker/demo
docker run --rm -it phase-barrier-demo
```

## 内容

- `agent_demo.py`：模拟编码 Agent——先尝试跳步（未完成 spec / 测试就写实现，被
  `write_file` 拦截），再按规范流程 spec -> 测试 -> 实现 -> 测试 -> 交付推进，
  每一步由 `advance_stage` 自动校验证据。
- `entrypoint.sh`：容器启动入口。
- 镜像在 tag 驱动发布时自动构建并推送到 GHCR
  （`ghcr.io/xuqing0415/phase-barrier-demo`，tag 与 `latest` 两个标签）。
