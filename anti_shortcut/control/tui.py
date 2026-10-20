"""Read-only event-driven dashboard with explicit human review and task claiming."""
from __future__ import annotations

import asyncio
import os

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, VerticalScroll
    from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Select, Static
except ImportError as exc:
    raise RuntimeError("TUI 需要 pip install 'phase-barrier[tui]'") from exc

from ..config import STAGES
from ..proxy_client import GateClient, GateClientError


class GateApp(App):
    TITLE = "phase-barrier · Control Plane"
    CSS = """
    #stage { height: 3; border: round $accent; padding: 0 1; }
    #status { height: 2; }
    #panels { layout: grid; grid-size: 2 2; grid-rows: 1fr 2fr; }
    #panels > * { border: round $primary; padding: 0 1; }
    #tasks { height: 1fr; }
    #actions { height: auto; max-height: 12; border: round $warning; }
    #actions Horizontal { height: 3; }
    #review-id { width: 2fr; }
    #reason { width: 3fr; }
    #task-id { width: 1fr; }
    #diff-mode { width: 20; }
    #diff-panel { height: 1fr; }
    #diff-panel DiffView { height: auto; }
    """
    BINDINGS = [("q", "quit", "退出"), ("s", "toggle_diff", "Unified / Split")]

    def __init__(self, client: GateClient):
        super().__init__()
        self.client, self.cursor = client, 0
        self.data = {}
        self.busy = False
        self.diff_key = None
        self.split = False
        self._render_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("连接 sidecar…", id="stage", markup=False)
        yield Static("", id="status", markup=False)
        with Container(id="panels"):
            yield Static("防线状态", id="defense", markup=False)
            yield DataTable(id="tasks")
            yield VerticalScroll(Static("暂无 Diff", markup=False), id="diff-panel")
            yield RichLog(id="events", wrap=True, markup=False, max_lines=200)
        with Container(id="actions"):
            yield Static("人工操作 · 审批通过后须重试原操作；不会自动写入或推进阶段", markup=False)
            with Horizontal():
                yield Select([("Diff 复核", "diff_decide"), ("交付复核", "review_decide")],
                             value="diff_decide", allow_blank=False, id="review-kind")
                yield Input(placeholder="复核 ID", id="review-id")
                yield Input(placeholder="复核理由（必填）", id="reason")
                yield Button("批准", id="approve", variant="success")
                yield Button("驳回", id="reject", variant="error")
            with Horizontal():
                yield Input(placeholder="待认领 btw 任务 ID", id="task-id")
                yield Button("人工认领", id="claim")
        yield Footer()

    async def on_mount(self):
        table = self.query_one("#tasks", DataTable)
        table.add_columns("类型", "标题", "状态", "归属", "ID")
        table.cursor_type = "row"
        self.set_interval(1, self.refresh_control)
        await self.refresh_control()

    async def refresh_control(self):
        if self.busy:
            return
        self.busy = True
        try:
            events = await asyncio.to_thread(self.client.events, self.cursor)
            changed = self.cursor == 0 or bool(events["events"])
            self.cursor = events["cursor"]
            for event in events["events"]:
                self.query_one("#events", RichLog).write(
                    f"{event['timestamp']} {event['type']} {event['data'].get('reason', '')}")
            if changed:
                self.data = await asyncio.to_thread(self.client.control_snapshot)
                await self.render_snapshot()
        except GateClientError as exc:
            self.query_one("#status", Static).update(f"连接/权限错误：{exc}")
        finally:
            self.busy = False

    async def render_snapshot(self):
        # Timer refreshes, button actions and split-view changes can overlap at
        # widget mount awaits. Serialize them so a refresh cannot remove widgets
        # while a second render is still mounting its diff view.
        async with self._render_lock:
            await self._render_snapshot()

    async def _render_snapshot(self):
        state, identity = self.data["state"], self.data["session"]
        current, completed = state["current_stage"], state["completed_stages"]
        self.query_one("#stage", Static).update("  →  ".join(
            f"{'▶' if n == current else '✓' if n in completed else '·'} {n} {name}"
            for n, name in STAGES.items()))
        goal = next((g for g in self.data["goals"] if g["goal_id"] == state.get("goal_id")), None)
        goal_text = f"{goal['title']} ({len(goal['evidence'])}/{len(goal['milestones'])})" if goal else "—"
        self.query_one("#status", Static).update(
            f"Session {identity['session_id']} · {identity['role']} · Goal {goal_text} · "
            f"风险 {(self.data.get('human_review') or {}).get('risk_score', '—')}")
        self.query_one("#defense", Static).update("防线\n" + "\n".join(
            f"{row['line']}: {row['status']}" for row in self.data["defense"]))
        table = self.query_one("#tasks", DataTable)
        table.clear()
        for task in self.data["tasks"]:
            table.add_row(task["type"], task["title"], task["status"] + (" / stale" if task["stale"] else ""),
                          "human-owned" if task["human_owned"] else task["claimed_by"] or "—", task["task_id"])
        human = identity["actor"] == "human" and identity["role"] in {"maintainer", "admin"}
        for button in ["#approve", "#reject"]:
            self.query_one(button, Button).disabled = not human
        self.query_one("#claim", Button).disabled = identity["actor"] != "human" or identity["role"] == "observer"
        preview = self.data.get("diff")
        if preview and (preview["review_id"], preview["status"], self.split) != self.diff_key:
            panel = self.query_one("#diff-panel", VerticalScroll)
            await panel.remove_children()
            await panel.mount(Static(f"{preview['path']} · {preview['status']} · {preview['review_id']}", markup=False))
            try:
                from textual_diff_view import DiffView
            except ImportError:
                from rich.syntax import Syntax
                await panel.mount(Static(Syntax(preview["diff"], "diff")))
            else:
                if preview.get("binary") or preview.get("truncated"):
                    await panel.mount(Static(preview["diff"], markup=False))
                else:
                    await panel.mount(DiffView(preview["path"], preview["path"],
                                               preview["old_content"], preview["new_content"],
                                               split=self.split, annotations=True))
            self.diff_key = (preview["review_id"], preview["status"], self.split)

    async def action_toggle_diff(self):
        self.split = not self.split
        if self.data:
            await self.render_snapshot()

    async def on_button_pressed(self, event: Button.Pressed):
        try:
            if event.button.id == "claim":
                operation = "task_transition"
                values = {"task_id": self.query_one("#task-id", Input).value, "status": "active"}
                task = next((t for t in self.data.get("tasks", []) if t["task_id"] == values["task_id"]), None)
                if not task or task["type"] != "btw":
                    self.notify("请填写已排队的 btw 任务 ID", severity="error")
                    return
            else:
                operation = str(self.query_one("#review-kind", Select).value)
                key = "review_id" if operation == "diff_decide" else "request_id"
                values = {key: self.query_one("#review-id", Input).value,
                          "approved": event.button.id == "approve",
                          "reason": self.query_one("#reason", Input).value}
            await asyncio.to_thread(self.client.control, operation, **values)
            self.notify("已记录")
            self.cursor = 0
            await self.refresh_control()
        except GateClientError as exc:
            self.notify(str(exc), severity="error")


def main():
    GateApp(GateClient(os.environ.get("PHASE_BARRIER_URL", "http://127.0.0.1:8080"),
                       token=os.environ.get("PHASE_BARRIER_SESSION_TOKEN"))).run()


if __name__ == "__main__":
    main()
