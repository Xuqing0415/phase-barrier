# phase-barrier-plugin-alpha（测试 fixture）

模拟第三方插件仓库：声明四类 phase-barrier 入口点（语言适配器 / 阶段校验器 /
拦截规则 / 集成插件），供 `tests/test_auto_discover_e2e.py` 端到端验证自动发现
与增量刷新流程。**仅测试用，不发布到 PyPI / 不收录进插件索引。**

真实场景中，第三方插件作者把同类仓库打上 `phase-barrier-plugin` GitHub topic
后，`plugin-verification.yml`（每周一 03:00 UTC）会自动发现、验证并收录。