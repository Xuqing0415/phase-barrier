"""交付收尾校验：进入阶段 6 后仍须「最终测试全绿且覆盖最新代码」。

背景：真实评测（seaborn-2848 gated）中 Agent 推进到阶段 6 后继续改码 /
运行红测，收尾时仍被计为 completed。本测试固化 ``State.delivery_clean()``
与 ``GateSidecar.state()["delivery_clean"]`` 语义：阶段 6 只是“交付阶段”，
只有最近一次测试全绿且晚于最后一次源码/测试变更，才算收尾通过。
"""
import time

from anti_shortcut.sidecar import GateSidecar
from anti_shortcut.state import StateManager
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC, USER_REQUEST


def make_state(tmp_path, user_request="测试需求") -> StateManager:
    return StateManager(tmp_path / "state.json", user_request=user_request)


def _reach_stage6(state: StateManager) -> StateManager:
    for stage in (2, 3, 4, 5, 6):
        state.advance(stage)
    return state


def test_delivery_clean_requires_stage6(tmp_path):
    s = make_state(tmp_path)
    assert s.delivery_clean() is False
    _reach_stage6(s)
    # 到达阶段 6 但从未有测试运行 -> 不满足收尾
    assert s.is_complete is True
    assert s.delivery_clean() is False


def test_delivery_clean_green_and_current(tmp_path):
    s = make_state(tmp_path)
    s.mark_source_change("fib.py", at_epoch=100.0)
    s.mark_test_run({"exit_code": 0, "passed": True, "at_epoch": 200.0})
    _reach_stage6(s)
    # 到达阶段 6 后最近一次测试仍全绿、且晚于最后变更
    s.mark_test_run({"exit_code": 0, "passed": True, "at_epoch": 300.0})
    assert s.delivery_clean() is True


def test_delivery_clean_red_after_stage6(tmp_path):
    """进入阶段 6 后再跑红测：收尾校验必须失效（seaborn-2848 场景）。"""
    s = make_state(tmp_path)
    _reach_stage6(s)
    s.mark_test_run({"exit_code": 0, "passed": True, "at_epoch": 200.0})
    assert s.delivery_clean() is True
    s.mark_test_run({"exit_code": 4, "passed": False, "at_epoch": 300.0})
    assert s.delivery_clean() is False
    # 补跑全绿后恢复收尾通过（阶段仍单调停在 6）
    s.mark_test_run({"exit_code": 0, "passed": True, "at_epoch": 400.0})
    assert s.delivery_clean() is True
    assert s.current_stage == 6


def test_delivery_clean_change_after_green(tmp_path):
    """进入阶段 6 后改码但未重测：收尾校验失效，重测后恢复。"""
    s = make_state(tmp_path)
    s.mark_source_change("fib.py", at_epoch=100.0)
    _reach_stage6(s)
    s.mark_test_run({"exit_code": 0, "passed": True, "at_epoch": 200.0})
    assert s.delivery_clean() is True
    s.mark_source_change("fib.py", at_epoch=300.0)
    assert s.delivery_clean() is False
    s.mark_test_run({"exit_code": 0, "passed": True, "at_epoch": 400.0})
    assert s.delivery_clean() is True


def test_delivery_confirmed_epoch_recorded(tmp_path):
    s = make_state(tmp_path)
    assert s.get_evidence("delivery_confirmed_at_epoch") is None
    _reach_stage6(s)
    assert s.get_evidence("delivery_confirmed_at_epoch") is not None


def test_delivery_clean_persists_across_reload(tmp_path):
    path = tmp_path / "state.json"
    s = StateManager(path, user_request="测试需求")
    _reach_stage6(s)
    s.mark_test_run({"exit_code": 0, "passed": True, "at_epoch": time.time() + 5})
    assert s.delivery_clean() is True
    reloaded = StateManager(path)
    assert reloaded.current_stage == 6
    assert reloaded.delivery_clean() is True


def test_sidecar_state_exposes_delivery_clean(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = GateSidecar(ws, user_request=USER_REQUEST)
    st = sidecar.state()
    assert st["is_complete"] is False
    assert st["delivery_clean"] is False


def test_e2e_red_rerun_after_delivery(tmp_path):
    """端到端：全绿推进到阶段 6 后改出 bug 并红测 -> delivery_clean 失效；修回并全绿 -> 恢复。"""
    bad_impl = "def fib(n):\n    return 0\n"
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = GateSidecar(ws, user_request=USER_REQUEST)
    proxy = sidecar.proxy

    proxy.write_file("spec.md", SPEC)
    assert sidecar.advance(2)["success"]
    proxy.write_file("test_fib.py", GOOD_TESTS)
    assert sidecar.advance(3)["success"]
    proxy.write_file("fib.py", GOOD_IMPL)
    assert sidecar.advance(4)["success"]

    r = proxy.execute_command("python -m pytest test_fib.py -q", timeout=120)
    assert r["recorded_test_run"] is True and r["exit_code"] == 0
    adv = sidecar.advance(5)
    assert adv["success"] and adv["stage"] == 6
    assert sidecar.state()["is_complete"] is True
    assert sidecar.state()["delivery_clean"] is True

    # 进入阶段 6 后改坏实现并运行红测：交付收尾必须失效
    proxy.write_file("fib.py", bad_impl)
    assert sidecar.state()["delivery_clean"] is False  # 改码未重测
    r = proxy.execute_command("python -m pytest test_fib.py -q", timeout=120)
    assert r["recorded_test_run"] is True and r["exit_code"] != 0
    assert sidecar.state()["delivery_clean"] is False
    assert sidecar.state()["is_complete"] is True  # 阶段保持单调

    # 修回并全绿：收尾校验恢复通过
    proxy.write_file("fib.py", GOOD_IMPL)
    assert sidecar.state()["delivery_clean"] is False
    r = proxy.execute_command("python -m pytest test_fib.py -q", timeout=120)
    assert r["recorded_test_run"] is True and r["exit_code"] == 0
    assert sidecar.state()["delivery_clean"] is True
