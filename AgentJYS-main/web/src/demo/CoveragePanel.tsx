import type { CoverageState, RunSnapshot } from "./types";

const labels: Record<CoverageState, string> = {
  unexecuted: "未执行", called: "已调用待验证", passed: "检查通过",
  failed: "检查失败", blocked: "条件不足",
};

export default function CoveragePanel({ run }: { run: RunSnapshot }) {
  const rows = run.coverage;
  if (!rows.length) return null;
  const called = rows.filter(row => row.calls > 0).length;
  const passed = rows.filter(row => row.state === "passed").length;
  const diagnostics = run.diagnostics;
  return <section className="demo-coverage" aria-label="接口覆盖">
    <details>
      <summary>
        <span><strong>本轮接口覆盖与缺口</strong><small>真实调用与功能检查，分开计数</small></span>
        <span className="demo-coverage-counts">
          <span>已调用 {called} / {rows.length}</span>
          <span>检查通过 {passed} / {rows.length}</span>
        </span>
      </summary>
      <div className="demo-coverage-content">
        <p>仅统计这一轮；HTTP 200、任务已受理不等于业务完成。运维写操作不在普通演示中执行。</p>
        <div className="demo-coverage-table" tabIndex={0} role="region" aria-label="接口覆盖明细，可横向滚动">
          <table>
            <thead><tr><th>接口</th><th>状态</th><th>调用 / HTTP</th><th>检查与限制</th></tr></thead>
            <tbody>{rows.map(row => <tr key={`${row.method} ${row.path}`}>
              <td><span className="demo-method">{row.method}</span><code>{row.path}</code></td>
              <td><span className={`demo-status demo-status-${row.state}`}>{labels[row.state]}</span></td>
              <td>{row.calls} 次 <small>{row.http_statuses.join(" / ") || "—"}</small></td>
              <td>{row.detail}{row.step_ids.some(id => id > 0) && <small>
                步骤 {row.step_ids.filter(id => id > 0).join(" · ")}
              </small>}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <div className="demo-diagnostics">
          <strong>只读诊断 · {diagnostics.state === "complete" ? "采集完成" : diagnostics.state === "pending" ? "待采集" : "部分可用"}</strong>
          <p>本轮任务 {diagnostics.task_count} · 关联轨迹 {diagnostics.trace_count} · 日志记录 {diagnostics.log_record_count} · 事件 {diagnostics.incident_count}</p>
          {diagnostics.notes.map((note, i) => <p key={i}>{note}</p>)}
          <small>有界采集；不展示日志正文、凭据或其他运行的数据，计数不是业务结果。</small>
        </div>
      </div>
    </details>
  </section>;
}
