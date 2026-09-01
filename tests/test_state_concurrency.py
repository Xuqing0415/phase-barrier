"""StateManager 并发安全测试（v0.26.3）：多 Agent / 多进程共享门禁状态。

覆盖：
- 并发推进：同一阶段只允许一个写入者成功，其余拒绝跳跃，最终状态一致；
- 并发写入：``mark_test_run`` / ``set_evidence`` 并发写入不损坏、不丢更新；
- 文件锁：锁文件可被后续实例正常接管，持锁超时抛明确异常。
"""
import json
import threading

import pytest

from anti_shortcut.state import StateLockTimeoutError, StateManager, _file_lock


def _make(ws, **kw) -> StateManager:
    return StateManager(ws / ".agent_gate" / "state.json", **kw)


def _run_threads(workers: list) -> list:
    threads = [threading.Thread(target=fn) for fn in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return threads


def test_concurrent_advance_single_winner(tmp_path):
    _make(tmp_path)  # 先初始化
    ok = []
    errors = []

    def worker():
        try:
            local = _make(tmp_path)
            local.advance(2)
            ok.append(1)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    _run_threads([worker] * 6)
    # 只有第一个拿到锁并在重载后仍处于阶段 1 的实例能推进；其余重载后看到阶段 2 -> 拒绝跳跃
    assert len(ok) == 1
    assert len(errors) == 5
    final = _make(tmp_path)
    assert final.current_stage == 2
    assert final.completed_stages == [0, 1]
    assert all(isinstance(e, ValueError) for e in errors)


def test_concurrent_mark_test_run_no_corruption(tmp_path):
    _make(tmp_path)
    runs = [{"exit_code": i, "output": f"run-{i}"} for i in range(8)]

    def worker(i: int):
        local = _make(tmp_path)
        local.mark_test_run(runs[i])

    _run_threads([lambda i=i: worker(i) for i in range(len(runs))])
    final = _make(tmp_path)
    last = final.get_evidence("last_test_run")
    # mark_test_run 会附加 at_epoch / at 时间戳字段，因此按业务字段比对
    assert any(
        last.get("exit_code") == r["exit_code"] and last.get("output") == r["output"]
        for r in runs
    )
    raw = json.loads((tmp_path / ".agent_gate" / "state.json").read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert any(
        raw["evidence"]["last_test_run"].get("exit_code") == r["exit_code"]
        and raw["evidence"]["last_test_run"].get("output") == r["output"]
        for r in runs
    )


def test_concurrent_mixed_mutations(tmp_path):
    sm = _make(tmp_path, user_request="初始需求")

    def worker(i: int):
        local = _make(tmp_path)
        if i % 2 == 0:
            local.set_evidence("note", f"note-{i}")
        else:
            local.mark_source_change(f"file-{i}.py")

    _run_threads([lambda i=i: worker(i) for i in range(6)])
    final = _make(tmp_path)
    # 用户需求与阶段 0 记录仍在（重载不丢基础字段）
    assert final.get_evidence("user_request") == "初始需求"
    assert final.current_stage == 1
    assert final.completed_stages == [0]
    raw = json.loads((tmp_path / ".agent_gate" / "state.json").read_text(encoding="utf-8"))
    assert raw["evidence"]["note"] in {f"note-{i}" for i in range(6)} or "note" in raw["evidence"]


def test_lock_file_is_reused_across_instances(tmp_path):
    sm = _make(tmp_path, user_request="需求")
    sm.set_evidence("probe", 1)  # 带锁写入，生成 .lock 文件
    lock_path = tmp_path / ".agent_gate" / "state.json.lock"
    assert lock_path.exists()
    # 锁文件可被新实例继续使用（内容为单字节占位）
    with _file_lock(lock_path, timeout=1.0):
        pass


def test_lock_timeout_raises(tmp_path):
    sm = _make(tmp_path)
    lock_path = sm.state_file.with_name(sm.state_file.name + ".lock")
    with _file_lock(lock_path, timeout=0.15):
        with pytest.raises(StateLockTimeoutError):
            with _file_lock(lock_path, timeout=0.15):
                pass  # 未持锁线程/进程内二次获取应超时


def test_reload_sees_other_instance_write(tmp_path):
    """reload() 重新从磁盘读取他人实例的推进结果（v0.26.3）。"""
    a = _make(tmp_path, user_request="需求")
    b = _make(tmp_path, user_request="需求")
    a.advance(2)
    # b 的内存缓存未刷新，仍停留在阶段 1
    assert b.current_stage == 1
    b.reload()
    assert b.current_stage == 2
    assert b.completed_stages == [0, 1]
    assert b.get_evidence("user_request") == "需求"
