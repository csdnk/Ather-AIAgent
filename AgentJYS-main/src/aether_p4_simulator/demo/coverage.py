"""An observed request is not proof that its business outcome was checked."""

from aether_p4_simulator.validation.calls import ROUTES, ApiCall

from .models import CoverageEntry

_ISOLATED = {
    ("POST", "/p3/recovery"),
    ("POST", "/p3/tasks/{task_id}/control"),
    ("GET", "/p3/controls/{operation_id}"),
    ("POST", "/p3/periodic/control"),
    ("PUT", "/p3/configuration"),
    ("POST", "/p3/backups"),
    ("POST", "/p3/restore-drills"),
}


class Coverage:
    def __init__(self) -> None:
        self._unanswered: set[tuple[str, str]] = set()
        self.rows = {
            key: CoverageEntry(
                method=key[0],
                path=key[1],
                detail="运行登记内部通信不计入故事业务覆盖；见恢复专项验收"
                if key[1].startswith("/p3/client-runs/")
                else "原操作回查；仅在恢复专项验证，不代表原业务重新执行"
                if key[1]
                in {
                    "/p3/operation-requests/{operation_id}",
                    "/p3/mutation-receipts/{operation_id}",
                    "/p3/mutation-receipts/{operation_id}/result",
                }
                else "仅限隔离运维验收，普通故事不执行"
                if key in _ISOLATED
                else "已退役；不代表维护执行成功"
                if key[1] == "/p3/maintenance/cycle"
                else "本轮未执行",
            )
            for key in ROUTES
        }

    def observe(self, call: ApiCall, step_id: int) -> None:
        row = self.rows.get((call.method, call.path))
        if row is None:
            return
        row.calls += 1
        if call.status_code is not None:
            row.http_statuses = sorted(set(row.http_statuses) | {call.status_code})
        else:
            self._unanswered.add((call.method, call.path))
        if step_id not in row.step_ids:
            row.step_ids.append(step_id)
        if row.state == "unexecuted":
            row.state, row.detail = "called", "已调用，尚未完成对应业务检查"

    def finish(self, step_id: int, state: str) -> None:
        for row in self.rows.values():
            if step_id in row.step_ids:
                self.mark(row.method, row.path, state)

    def mark(self, method: str, path: str, state: str) -> None:
        row = self.rows[method, path]
        if not row.calls:
            return
        # A later success cannot conceal an earlier failure in this same run.
        if row.state == "failed":
            return
        if state == "passed" and (method, path) in self._unanswered:
            row.state = "blocked"
            row.detail = "本轮存在未收到 HTTP 响应的调用，未计入通过"
            return
        if state == "passed":
            row.state = "passed"
        elif state == "failed":
            row.state = "failed"
        elif state == "blocked":
            row.state = "blocked"
        else:
            row.state = "called"
        row.detail = {
            "passed": "对应步骤的真实结果检查通过；不代表该接口全部能力已验收",
            "failed": "对应业务检查失败；HTTP 成功不等于业务成功",
            "blocked": "已调用，但依赖条件不足，未计入通过",
            "called": "已调用，结果尚未确认",
        }[row.state]

    def snapshot(self) -> list[CoverageEntry]:
        return [row.model_copy(deep=True) for row in self.rows.values()]
