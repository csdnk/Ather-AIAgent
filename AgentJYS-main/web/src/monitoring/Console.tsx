import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import ConfigProvider from "antd/es/config-provider";
import Drawer from "antd/es/drawer";
import Modal from "antd/es/modal";
import theme from "antd/es/theme";
import ApartmentOutlined from "@ant-design/icons/ApartmentOutlined";
import ArrowRightOutlined from "@ant-design/icons/ArrowRightOutlined";
import CheckCircleOutlined from "@ant-design/icons/CheckCircleOutlined";
import ClockCircleOutlined from "@ant-design/icons/ClockCircleOutlined";
import CloseOutlined from "@ant-design/icons/CloseOutlined";
import DashboardOutlined from "@ant-design/icons/DashboardOutlined";
import DatabaseOutlined from "@ant-design/icons/DatabaseOutlined";
import DisconnectOutlined from "@ant-design/icons/DisconnectOutlined";
import ExclamationCircleOutlined from "@ant-design/icons/ExclamationCircleOutlined";
import ExperimentOutlined from "@ant-design/icons/ExperimentOutlined";
import LinkOutlined from "@ant-design/icons/LinkOutlined";
import ReloadOutlined from "@ant-design/icons/ReloadOutlined";
import SearchOutlined from "@ant-design/icons/SearchOutlined";
import ThunderboltFilled from "@ant-design/icons/ThunderboltFilled";
import UnorderedListOutlined from "@ant-design/icons/UnorderedListOutlined";
import {
  get,
  message,
  type LogPage,
  type Task,
  type Trace,
  type Incident,
  type Resource,
  type Observation,
} from "./api";
import { useMonitor, useOperation, type Sample } from "./useMonitor";
import { buildSpans } from "./trace";
import "./console.css";
import type Keycloak from "keycloak-js";
import { browserIdentity, readIdentity, loginReturnUrl, getAuthenticationState, subscribeAuthentication, type Identity } from "./identity";
import { setActiveTenant } from "./api";

type Page = "overview" | "services" | "tasks" | "traces" | "incidents";
type Selection =
  | { kind: "task" | "trace"; id: string }
  | { kind: "incident"; value: Incident };
const pages: { key: Page; title: string; sub: string; icon: ReactNode }[] = [
  {
    key: "overview",
    title: "运行总览",
    sub: "服务健康、执行状态与实时工作负载",
    icon: <DashboardOutlined />,
  },
  {
    key: "services",
    title: "服务与依赖",
    sub: "按能力与依赖检查实际可用性",
    icon: <DatabaseOutlined />,
  },
  {
    key: "tasks",
    title: "后台任务",
    sub: "追踪持久任务、执行尝试和恢复状态",
    icon: <UnorderedListOutlined />,
  },
  {
    key: "traces",
    title: "链路追踪",
    sub: "从请求进入节点，定位耗时与失败",
    icon: <ApartmentOutlined />,
  },
  {
    key: "incidents",
    title: "异常事件",
    sub: "查看异常处置与独立业务复验",
    icon: <ExclamationCircleOutlined />,
  },
];
const labels: Record<string, string> = {
  available: "可用",
  ready: "已就绪",
  not_ready: "未就绪",
  unavailable: "不可用",
  unknown: "未知",
  disabled: "未启用",
  degraded: "降级",
  running: "运行中",
  stopped: "已停止",
  starting: "启动中",
  pending: "等待执行",
  succeeded: "已完成",
  failed: "失败",
  retry_wait: "等待重试",
  recovery_wait: "等待恢复",
  attention_required: "需处理",
  cancelled: "已取消",
  returned: "已返回",
  open: "未闭合",
  partial: "记录不完整",
  confirmed: "已确认",
  not_started: "未开始",
  no_effect: "无副作用",
  recovering: "恢复中",
  verifying: "复验中",
  resolved: "已解决",
  passed: "通过",
  observed: "已观测",
  literal_baseline: "原文基线",
  model: "模型加工",
  local_sqlite: "本地 SQLite",
  p2_grpc: "P2 gRPC",
};
const color = (state: string) =>
  [
    "available",
    "ready",
    "succeeded",
    "returned",
    "passed",
    "resolved",
    "confirmed",
  ].includes(state)
    ? "green"
    : ["failed", "unavailable", "not_ready", "attention_required"].includes(
          state,
        )
      ? "red"
      : [
            "degraded",
            "retry_wait",
            "recovery_wait",
            "partial",
            "open",
            "recovering",
            "verifying",
          ].includes(state)
        ? "amber"
        : ["running", "starting", "pending"].includes(state)
          ? "blue"
          : "gray";
function Badge({ state }: { state: string }) {
  return (
    <span className={`badge ${color(state)}`}>
      <i />
      {labels[state] ?? state}
    </span>
  );
}
const date = (value: string | number | null | undefined) =>
  value == null
    ? "—"
    : new Date(value).toLocaleString("zh-CN", { hour12: false });
const short = (value: string) =>
  value.length > 22 ? `${value.slice(0, 12)}…${value.slice(-6)}` : value;
function Empty({
  title = "当前没有记录",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <DatabaseOutlined />
      <strong>{title}</strong>
      <p>{children ?? "执行一次业务请求后，相应的真实记录会出现在这里。"}</p>
    </div>
  );
}
function ErrorNotice({ text }: { text?: string }) {
  return text ? (
    <div className="error-note" role="alert">
      <ExclamationCircleOutlined />
      {text}
    </div>
  ) : null;
}
function Panel({
  title,
  extra,
  children,
  className = "",
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-head">
        <h2>{title}</h2>
        {extra}
      </div>
      {children}
    </section>
  );
}
function Json({ value }: { value: unknown }) {
  return <pre className="json-detail">{JSON.stringify(value, null, 2)}</pre>;
}
function IdLink({ id, onClick }: { id: string; onClick: () => void }) {
  return (
    <button className="id-link" onClick={onClick} title={id}>
      {short(id)}
      <ArrowRightOutlined />
    </button>
  );
}

function Trend({ samples }: { samples: Sample[] }) {
  const max = Math.max(
    1,
    ...samples.flatMap((s) => [s.pending ?? 0, s.deliveries ?? 0]),
  );
  const startAt = samples[0]?.at ?? 0;
  const windowMs = Math.max(1, (samples.at(-1)?.at ?? startAt) - startAt);
  const points = (key: "pending" | "deliveries") =>
    samples.reduce<string[]>(
      (segments, s, i) => {
        if (s[key] == null) segments.push("");
        else
          segments[segments.length - 1] +=
            `${i ? " " : ""}${40 + ((s.at - startAt) * 710) / windowMs},${148 - (s[key]! / max) * 110}`;
        return segments;
      },
      [""],
    );
  return (
    <>
      <div className="chart-key">
        <span>
          <i className="dot teal" />
          待处理任务
        </span>
        <span>
          <i className="dot purple" />
          未确认事件
        </span>
        <small>连接后采样 · 最近 {samples.length} 次</small>
      </div>
      {samples.length < 2 ? (
        <Empty title="等待采样">
          至少两次实际采样后绘制趋势；不会补造历史数据。
        </Empty>
      ) : (
        <div className="chart">
          <svg
            viewBox="0 0 790 190"
            role="img"
            aria-label="连接后的任务和事件数量趋势"
          >
            {[0, 0.5, 1].map((y) => (
              <g key={y}>
                <line
                  x1="40"
                  x2="750"
                  y1={148 - y * 110}
                  y2={148 - y * 110}
                  stroke="var(--line)"
                  strokeDasharray="3 5"
                />
                <text x="12" y={152 - y * 110}>
                  {(max * y).toFixed(y === 0.5 ? 1 : 0)}
                </text>
              </g>
            ))}
            {(["deliveries", "pending"] as const).map((key) =>
              points(key)
                .filter(Boolean)
                .map((p, i) => (
                  <polyline
                    key={key + i}
                    points={p}
                    fill="none"
                    stroke={key === "pending" ? "var(--accent)" : "#87639c"}
                    strokeWidth="2.5"
                  />
                )),
            )}
            <text x="40" y="180">
              {new Date(samples[0].at).toLocaleTimeString()}
            </text>
            <text x="750" y="180" textAnchor="end">
              {new Date(samples.at(-1)!.at).toLocaleTimeString()}
            </text>
          </svg>
        </div>
      )}
    </>
  );
}

function TaskTable({
  items,
  select,
}: {
  items: Task[];
  select: (s: Selection) => void;
}) {
  return items.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>任务 / 类型</th>
            <th>所属流程</th>
            <th>状态</th>
            <th>执行尝试</th>
            <th>副作用</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.task_id}>
              <td>
                <IdLink
                  id={t.task_id}
                  onClick={() => select({ kind: "task", id: t.task_id })}
                />
                <small>{t.kind}</small>
              </td>
              <td>
                <span className="flow-tag">{t.owner_flow}</span>
              </td>
              <td>
                <Badge state={t.state} />
              </td>
              <td className="mono">
                {t.attempt} / {t.max_attempts}
              </td>
              <td>
                <Badge state={t.effect_status} />
              </td>
              <td className="muted nowrap">{date(t.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="当前筛选下没有任务" />
  );
}
function TraceTable({
  items,
  select,
}: {
  items: Trace[];
  select: (s: Selection) => void;
}) {
  return items.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Trace / 入口节点</th>
            <th>流程</th>
            <th>节点数</th>
            <th>失败节点</th>
            <th>首次记录</th>
            <th>最近记录</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.trace_id}>
              <td>
                <IdLink
                  id={t.trace_id}
                  onClick={() => select({ kind: "trace", id: t.trace_id })}
                />
                <small>{t.entry_node}</small>
              </td>
              <td>
                <span className="flow-tag">{t.flow}</span>
              </td>
              <td className="mono">{t.span_count}</td>
              <td>
                <span className={t.failed_span_count ? "text-red" : "muted"}>
                  {t.failed_span_count}
                </span>
              </td>
              <td className="muted nowrap">{date(t.started_at)}</td>
              <td className="muted nowrap">{date(t.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="尚无可见 Trace">
      仅展示当前身份与作用域内保留的链路记录。
    </Empty>
  );
}
function Observations({
  items,
  checked,
}: {
  items: Observation[];
  checked: number;
}) {
  return (
    <div className="dependency-grid">
      {items.map((o) => (
        <div className="dependency" key={o.category + o.name}>
          <div>
            <span className="dependency-icon">
              <DatabaseOutlined />
            </span>
            <div>
              <strong>{o.name}</strong>
              <small>{o.reason_code}</small>
            </div>
            <Badge
              state={Date.parse(o.fresh_until) <= checked ? "unknown" : o.state}
            />
          </div>
          <footer>
            <span>
              {o.category === "dependency" ? (
                <>
                  探针耗时 <b>{o.elapsed_ms.toFixed(1)} ms</b>
                </>
              ) : (
                "基于依赖证据汇总"
              )}
            </span>
            <span title={date(o.checked_at)}>
              证据有效至 {new Date(o.fresh_until).toLocaleTimeString()}
            </span>
          </footer>
        </div>
      ))}
    </div>
  );
}

export function TraceDetail({ id, token }: { id: string; token: string }) {
  const [page, setPage] = useState<Resource<LogPage>>({});
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    // This component is keyed by trace within an identity-keyed Monitor.
    // Refresh credentials without dismissing the node currently being inspected.
    get<LogPage>(
      `/p3/logs/${encodeURIComponent(id)}?limit=200`,
      token,
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) setPage({ data });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setPage({ error: message(e) });
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [id, token, refresh]);
  async function more() {
    const current = page.data;
    if (!current || !current.next_after || busy) return;
    setBusy(true);
    try {
      const next = await get<LogPage>(
        `/p3/logs/${encodeURIComponent(id)}?limit=200&after=${current.next_after}`,
        token,
        AbortSignal.timeout(15000),
      );
      setPage({
        data: { ...next, records: [...current.records, ...next.records] },
      });
    } catch (e) {
      setPage({ data: current, error: message(e) });
    } finally {
      setBusy(false);
    }
  }
  const spans = buildSpans(page.data?.records ?? []);
  const start = Math.min(...spans.map((s) => s.start));
  const end = Math.max(...spans.map((s) => s.start + (s.duration ?? 0)));
  const width = Math.max(end - start, 1);
  const span = spans.find((s) => s.id === selected);
  return (
    <div>
      <div className="detail-toolbar">
        <code>{id}</code>
        <button
          className="btn"
          disabled={busy}
          onClick={() => setRefresh((r) => r + 1)}
        >
          <ReloadOutlined />
          重新查询
        </button>
      </div>
      <ErrorNotice text={page.error} />
      <div className="notice">
        仅显示保留的节点记录。未闭合可能表示仍在执行或曾被中断；业务结果应以任务状态为准。
        {page.data?.next_after ? " 当前仅加载部分记录。" : ""}
      </div>
      {page.data && (
        <div className="detail-metrics">
          <div>
            <b>{page.data.overview.span_count}</b>
            <span>保留节点</span>
          </div>
          <div>
            <b>{page.data.overview.failed_span_count}</b>
            <span>失败节点</span>
          </div>
          <div>
            <b>{page.data.overview.open_span_count}</b>
            <span>未闭合节点</span>
          </div>
          <div>
            <b>{page.data.local_dropped_records}</b>
            <span>本进程丢失日志</span>
          </div>
        </div>
      )}
      {spans.length ? (
        <>
          <div className="waterfall-head">
            <span>处理节点</span>
            <span>已记录时间范围 · {width.toFixed(1)} ms</span>
          </div>
          <div className="waterfall">
            {spans.map((s) => (
              <button
                key={s.id}
                className={`span-row ${selected === s.id ? "selected" : ""}`}
                onClick={() => setSelected(s.id)}
              >
                <div
                  className="span-name"
                  style={{ paddingLeft: 10 + s.depth * 12 }}
                >
                  <span className={`dot ${color(s.status)}`} />
                  <span title={s.node}>{s.node}</span>
                </div>
                <div className="span-track">
                  <div
                    className={`span-bar ${color(s.status)} ${s.duration == null ? "open-bar" : ""}`}
                    style={{
                      marginLeft: `${((s.start - start) / width) * 90}%`,
                      width: `${Math.max(0.5, ((s.duration ?? 0) / width) * 90)}%`,
                    }}
                  />
                  <span>
                    {s.duration == null
                      ? "未闭合"
                      : `${s.duration.toFixed(2)} ms`}
                  </span>
                </div>
              </button>
            ))}
          </div>
          <small className="muted">
            点击节点查看阶段与关联 ID。缺少 started 的节点标记为记录不完整。
          </small>
        </>
      ) : !busy && !page.error ? (
        <Empty title="未找到可见日志">
          检查 Trace ID、当前身份及日志保留范围。
        </Empty>
      ) : null}
      {busy && (
        <div className="loading" role="status">
          正在读取链路…
        </div>
      )}
      {page.data?.next_after && (
        <button
          className="btn load-more"
          disabled={busy}
          onClick={() => void more()}
        >
          继续加载节点
        </button>
      )}
      {span && (
        <Modal
          title={span.node}
          open
          onCancel={() => setSelected(null)}
          footer={null}
          width={800}
          zIndex={1200}
        >
          <Badge state={span.status} />
          <dl className="facts">
            <dt>Span ID</dt>
            <dd>
              <code>{span.id}</code>
            </dd>
            <dt>父节点</dt>
            <dd>
              <code>{span.parent ?? "—"}</code>
            </dd>
            <dt>所属流程</dt>
            <dd>{span.flow}</dd>
          </dl>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>阶段</th>
                  <th>记录时间</th>
                  <th>累计耗时</th>
                  <th>原因代码</th>
                </tr>
              </thead>
              <tbody>
                {span.rows.map((row) => (
                  <tr key={row.sequence}>
                    <td>{row.phase}</td>
                    <td>{date(row.occurred_at)}</td>
                    <td>
                      {row.elapsed_ms == null
                        ? "—"
                        : `${row.elapsed_ms.toFixed(2)} ms`}
                    </td>
                    <td>{row.reason_code ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className="raw-record">
            <summary>查看结构化记录与关联 ID</summary>
            <Json value={span.rows} />
          </details>
        </Modal>
      )}
    </div>
  );
}

type Progress = {
  stages?: { stage: string; completed_parts: number; updated_at: string }[];
  wait: {
    dependency_id: string;
    reason_code: string;
    next_check_at: string;
  } | null;
  checkpoints: {
    stage: string;
    committed_at: string;
    output_refs: unknown[];
  }[];
};

function TaskDetail({ id, token }: { id: string; token: string }) {
  const execution = useOperation(id, token);
  const [data, setData] = useState<
    Resource<{
      progress: Progress;
    }>
  >({});
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setData({});
    function refresh() { void get<Progress>(
        `/p3/tasks/${encodeURIComponent(id)}/progress`,
        token,
        controller.signal,
      )
      .then((progress) => {
        if (!controller.signal.aborted) setData({ data: { progress } });
        if (!controller.signal.aborted) timer = setTimeout(refresh, 2000);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setData({ error: message(e) });
        if (!controller.signal.aborted) timer = setTimeout(refresh, 5000);
      });
    }
    refresh();
    return () => { clearTimeout(timer); controller.abort(); };
  }, [id, token]);
  const task = data.error ? undefined : execution.data?.operation;
  const progress = task ? data.data?.progress : undefined;
  // Only IDs returned by an authorized diagnostic response can be followed.
  return (
    <>
      <ErrorNotice text={execution.error ?? data.error} />
      {task ? (
        <>
          <div className="detail-toolbar">
            <code>{id}</code>
            <Badge state={String(task.state)} />
          </div>
          <dl className="facts">
            <dt>处理器</dt>
            <dd>{String(task.kind)}</dd>
            <dt>副作用</dt>
            <dd>
              <Badge state={String(task.effect_status)} />
            </dd>
            <dt>执行尝试</dt>
            <dd>
              {String(task.attempt)} / {String(task.max_attempts)}
            </dd>
            <dt>错误代码</dt>
            <dd>{String(task.error_code ?? "—")}</dd>
            <dt>Temporal Workflow</dt>
            <dd>{task.temporal?.workflow_id ?? "等待启动确认"}</dd>
            <dt>当前 Run</dt>
            <dd>{task.temporal?.binding?.current_run_id ?? "等待启动确认"}</dd>
            <dt>执行核对</dt>
            <dd>{task.temporal?.diagnostic?.reason_code ?? "尚无核对记录"}</dd>
          </dl>
          {execution.data?.result !== undefined && <details><summary>已提交结果</summary><Json value={execution.data.result} /></details>}
          {progress?.wait && (
            <div className="notice">
              等待依赖 {progress.wait.dependency_id} ·{" "}
              {progress.wait.reason_code}
              <br />
              下次检查：{date(progress.wait.next_check_at)}
            </div>
          )}
          <Panel title="已提交的处理检查点">
            {progress?.stages?.map((stage) => <p key={stage.stage}>{({extraction: "内容抽取", compression: "内容压缩", embedding: "向量生成", summary: "工作摘要"} as Record<string, string>)[stage.stage] ?? stage.stage}：已保存 {stage.completed_parts} 个分块结果，可供恢复时复用。</p>)}
            {progress?.checkpoints.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>阶段</th>
                      <th>提交时间</th>
                      <th>输出引用</th>
                    </tr>
                  </thead>
                  <tbody>
                    {progress.checkpoints.map((point, index) => (
                      <tr key={index}>
                        <td>{point.stage}</td>
                        <td>{date(point.committed_at)}</td>
                        <td>{point.output_refs.length}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="尚无已提交检查点">
                以持久任务状态判断执行进度。
              </Empty>
            )}
          </Panel>
          <details className="raw-record">
            <summary>查看完整任务与恢复证据</summary>
            <Json value={{ task, progress }} />
          </details>
        </>
      ) : (
        !data.error && <div className="loading">读取任务状态…</div>
      )}
    </>
  );
}

function Monitor({
  token,
  disconnect,
  connect,
  identity,
  authError,
  organizationToolbar,
}: {
  token: string;
  identity: Identity | null;
  authError: string;
  organizationToolbar: ReactNode;
  disconnect: () => void;
  connect: () => void;
}) {
  const [page, setPage] = useState<Page>("overview");
  const [seconds, setSeconds] = useState(5);
  const [revision, setRevision] = useState(0);
  const [taskCursor, setTaskCursor] = useState<string | null>(null);
  const [taskState, setTaskState] = useState("");
  const [traceBefore, setTraceBefore] = useState<number | null>(null);
  const [traceFlow, setTraceFlow] = useState("business");
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<Selection | null>(null);
  const {
    snapshot: s,
    samples,
    checked,
    busy,
  } = useMonitor(
    token,
    seconds,
    revision,
    taskCursor,
    taskState,
    traceBefore,
    traceFlow,
    identity ? JSON.stringify([identity.principal_id, identity.scope, identity.permissions]) : "",
  );
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const health = s.health.data,
    runtime = s.runtime.data;
  const stale = !!health?.observations.some(
    (o) => Date.parse(o.fresh_until) <= now,
  );
  const readiness =
    !token || s.health.error || stale
      ? "unknown"
      : (health?.readiness ?? "unknown");
  const current = pages.find((p) => p.key === page)!;
  const select = (next: Selection) => setSelection(next);
  const taskItems = s.tasks.data?.items ?? [];
  const traces = s.traces.data?.items ?? [];
  const incidents = s.incidents.data;
  const activeIncidents = incidents?.filter((i) => i.state !== "resolved");
  const error = s.health.error ?? s.runtime.error;
  const navigate = (p: Page) => {
    setPage(p);
    setQuery("");
    if (p === "overview") {
      setTaskCursor(null);
      setTaskState("");
    }
  };
  return (
    <div className="monitor-shell">
      <aside className="sidebar">
        <div className="workspace">
          <span className="workspace-symbol">P3</span>
          <div>
            监测控制台
            <small>{s.capabilities.data?.profile ?? "尚未连接"}</small>
          </div>
          <span className="workspace-dot" />
        </div>
        <div className="nav-caption">监测工作空间</div>
        <nav aria-label="监测导航">
          {pages.map((p) => (
            <button
              key={p.key}
              aria-current={page === p.key ? "page" : undefined}
              className={page === p.key ? "active" : ""}
              onClick={() => navigate(p.key)}
            >
              {p.icon}
              {p.title}
              {p.key === "incidents" &&
                activeIncidents &&
                activeIncidents.length > 0 && <b>{activeIncidents.length}</b>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div>
            <span className="dot teal" />
            {identity ? `租户：${identity.scope.tenant_id}` : "统一 P3 服务"}<small>{identity?.principal_id ?? "当前身份可见范围"}</small>
          </div>
          <button onClick={token ? disconnect : connect}>
            {token ? <DisconnectOutlined /> : <LinkOutlined />}
            {token ? "断开连接" : "配置连接"}
          </button>
        </div>
      </aside>
      <div className="main">
        {organizationToolbar}
        <header className="topbar">
          <span>
            工作空间 <b>/</b> P3 <b>/</b> {current.title}
          </span>
          <div>
            <span className="environment">
              <i />
              {s.capabilities.data?.profile ?? "LOCAL"}
            </span>
            <button
              className="icon-btn"
              aria-label="配置连接"
              onClick={connect}
            >
              <LinkOutlined />
            </button>
            <span className="avatar">P3</span>
          </div>
        </header>
        <main className="content">
          {authError && <div className="identity-notice error" role="alert">{authError}</div>}
          {identity && <div className="identity-notice" role="status">
            当前租户：<strong>{identity.scope.tenant_id}</strong> · 身份：{identity.principal_id} · 用户：{identity.scope.user_id}
            <small>以下任务、日志与链路仅展示当前身份有权查看的记录。</small>
          </div>}
          <div className="page-heading">
            <div>
              <div className="eyebrow">AETHER / MONITORING</div>
              <h1>{current.title}</h1>
              <p>{current.sub}</p>
            </div>
            <div className="refresh-tools">
              <span className="checked">
                {checked
                  ? `最近请求 ${new Date(checked).toLocaleTimeString()}`
                  : "等待连接"}
              </span>
              <select
                aria-label="自动刷新间隔"
                value={seconds}
                onChange={(e) => setSeconds(Number(e.target.value))}
              >
                <option value={5}>每 5 秒</option>
                <option value={10}>每 10 秒</option>
                <option value={30}>每 30 秒</option>
                <option value={0}>暂停刷新</option>
              </select>
              <button
                className="btn"
                disabled={!token || busy}
                onClick={() => setRevision((r) => r + 1)}
              >
                <ReloadOutlined spin={busy} />
                刷新
              </button>
            </div>
          </div>
          {!token && (
            <div className="connect-banner">
              <div className="connect-symbol">
                <LinkOutlined />
              </div>
              <div>
                <strong>连接你的 P3 服务</strong>
                <p>使用具备诊断权限的凭据，查看真实健康、任务和调用链路。</p>
              </div>
              <button className="btn primary" onClick={connect}>
                配置连接
                <ArrowRightOutlined />
              </button>
            </div>
          )}
          <ErrorNotice text={error} />
          {stale && !error && (
            <div className="notice">
              健康证据已过期，当前可用性显示为未知。请刷新以重新探测。
            </div>
          )}
          {page === "overview" && (
            <>
              <div className="metric-grid">
                <div className="metric">
                  <span>
                    服务就绪状态
                    <CheckCircleOutlined />
                  </span>
                  <div className="metric-value">
                    <Badge state={readiness} />
                  </div>
                  <small>
                    {s.health.error
                      ? "探测请求未成功"
                      : "基于必需能力的实际探测"}
                  </small>
                </div>
                <div className="metric">
                  <span>
                    待处理任务
                    <UnorderedListOutlined />
                  </span>
                  <div className="metric-value">
                    {runtime?.pending_tasks ?? "—"}
                    <em>个</em>
                  </div>
                  <small>包含等待、运行、重试与恢复</small>
                </div>
                <div className="metric">
                  <span>
                    未确认事件
                    <ApartmentOutlined />
                  </span>
                  <div className="metric-value">
                    {runtime?.unacknowledged_deliveries ?? "—"}
                    <em>条</em>
                  </div>
                  <small>持久事件投递状态</small>
                </div>
                <div className="metric">
                  <span>
                    待处置异常
                    <ExclamationCircleOutlined />
                  </span>
                  <div
                    className={`metric-value ${activeIncidents?.length ? "text-amber" : ""}`}
                  >
                    {activeIncidents?.length ?? "—"}
                    <em>个</em>
                  </div>
                  <small>{s.incidents.error ?? "未解决的异常事件"}</small>
                </div>
              </div>
              <div className="overview-grid">
                <Panel
                  title="实时工作负载"
                  extra={
                    <span className="live-label">
                      <i />
                      {!token ? "等待连接" : seconds ? "实时采样" : "已暂停"}
                    </span>
                  }
                >
                  <Trend samples={samples} />
                </Panel>
                <Panel title="运行环境" extra={<ExperimentOutlined />}>
                  <dl className="environment-list">
                    <dt>编码模式</dt>
                    <dd>{s.capabilities.data?.embedding ?? "—"}</dd>
                    <dt>语义加工</dt>
                    <dd>
                      {labels[s.capabilities.data?.semantic_processing ?? ""] ??
                        "—"}
                    </dd>
                    <dt>正文存储</dt>
                    <dd>
                      {labels[s.capabilities.data?.object_storage ?? ""] ??
                        s.capabilities.data?.object_storage ??
                        "—"}
                    </dd>
                    <dt>身份配置</dt>
                    <dd>
                      {runtime ? (
                        <Badge state={runtime.identity_configuration} />
                      ) : (
                        "—"
                      )}
                    </dd>
                    <dt>最长任务等待</dt>
                    <dd>
                      {runtime
                        ? `${runtime.oldest_task_age_seconds.toFixed(1)} s`
                        : "—"}
                    </dd>
                    <dt>未知动作</dt>
                    <dd>{runtime?.unknown_action_ids.length ?? "—"}</dd>
                  </dl>
                  <ErrorNotice text={s.capabilities.error} />
                  <div className="panel-note">
                    当前实例配置 · 不代表生产验收
                  </div>
                </Panel>
              </div>
              <Panel
                title="后台执行器"
                extra={
                  runtime ? <Badge state={runtime.worker_state} /> : undefined
                }
              >
                {runtime ? (
                  <div className="workers">
                    {Object.entries(runtime.worker_lanes).map(
                      ([lane, state]) => {
                        const worker = runtime.workers
                          .filter((w) => w.execution_class === lane)
                          .sort((a, b) => a.age_seconds - b.age_seconds)[0];
                        return (
                          <div className="worker" key={lane}>
                            <div>
                              <span className="worker-symbol">
                                <ThunderboltFilled />
                              </span>
                              <strong>{lane}</strong>
                              <Badge state={state} />
                            </div>
                            <small>
                              {worker
                                ? `最近心跳 ${Math.max(0, (now - Date.parse(worker.last_seen)) / 1000).toFixed(1)} 秒前`
                                : "尚无心跳记录"}
                            </small>
                          </div>
                        );
                      },
                    )}
                  </div>
                ) : (
                  <Empty title="等待运行数据" />
                )}
              </Panel>
              {runtime &&
                (runtime.expired_lease_task_ids.length > 0 ||
                  runtime.attention_task_ids.length > 0 ||
                  runtime.unknown_action_ids.length > 0) && (
                  <Panel title="需关注的运行项">
                    <div className="attention-list">
                      {[
                        ...new Set([
                          ...runtime.expired_lease_task_ids,
                          ...runtime.attention_task_ids,
                        ]),
                      ].map((id) => (
                        <div key={id}>
                          <Badge
                            state={
                              runtime.expired_lease_task_ids.includes(id)
                                ? "recovery_wait"
                                : "attention_required"
                            }
                          />
                          <IdLink
                            id={id}
                            onClick={() => select({ kind: "task", id })}
                          />
                        </div>
                      ))}
                      {runtime.unknown_action_ids.map((id) => (
                        <div key={id}>
                          <Badge state="unknown" />
                          <code>{id}</code>
                          <span className="muted">原动作结果未知</span>
                        </div>
                      ))}
                    </div>
                  </Panel>
                )}
              <Panel
                title="最近任务"
                extra={
                  <button
                    className="text-btn"
                    onClick={() => navigate("tasks")}
                  >
                    全部任务
                    <ArrowRightOutlined />
                  </button>
                }
              >
                <ErrorNotice text={s.tasks.error} />
                {!s.tasks.error && (
                  <TaskTable items={taskItems.slice(0, 6)} select={select} />
                )}
              </Panel>
            </>
          )}
          {page === "services" && (
            <>
              <Panel title="业务能力" extra={<Badge state={readiness} />}>
                <ErrorNotice text={s.health.error} />
                {health ? (
                  <Observations
                    checked={now}
                    items={health.observations.filter(
                      (o) => o.category === "capability",
                    )}
                  />
                ) : (
                  <Empty title="等待健康探测" />
                )}
              </Panel>
              <Panel
                title="依赖检查"
                extra={
                  <span className="muted">耗时为探针耗时，不是业务延迟</span>
                }
              >
                {health && (
                  <Observations
                    checked={now}
                    items={health.observations.filter(
                      (o) => o.category === "dependency",
                    )}
                  />
                )}
              </Panel>
            </>
          )}
          {page === "tasks" && (
            <Panel
              title="持久任务"
              extra={<span className="muted">按创建时间倒序 · 每页 30 条</span>}
            >
              <div className="list-tools">
                <select
                  aria-label="任务状态"
                  value={taskState}
                  onChange={(e) => {
                    setTaskState(e.target.value);
                    setTaskCursor(null);
                  }}
                >
                  <option value="">全部状态</option>
                  {[
                    "pending",
                    "running",
                    "retry_wait",
                    "recovery_wait",
                    "succeeded",
                    "failed",
                    "cancelled",
                    "attention_required",
                  ].map((state) => (
                    <option key={state} value={state}>
                      {labels[state]}
                    </option>
                  ))}
                </select>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (query.trim())
                      select({ kind: "task", id: query.trim() });
                  }}
                >
                  <SearchOutlined />
                  <input
                    aria-label="查找任务 ID"
                    placeholder="输入完整任务 ID"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                  <button
                    className="text-btn"
                    disabled={!token || !query.trim()}
                  >
                    查看
                  </button>
                </form>
              </div>
              <ErrorNotice text={s.tasks.error} />
              {!s.tasks.error && (
                <TaskTable items={taskItems} select={select} />
              )}
              <div className="pagination">
                <button
                  className="btn"
                  disabled={!taskCursor || busy}
                  onClick={() => setTaskCursor(null)}
                >
                  回到最新
                </button>
                <span>仅含当前身份有权诊断的任务</span>
                <button
                  className="btn"
                  disabled={!s.tasks.data?.next_cursor || busy}
                  onClick={() => setTaskCursor(s.tasks.data!.next_cursor)}
                >
                  下一页
                </button>
              </div>
            </Panel>
          )}
          {page === "traces" && (
            <>
              <div className="notice">
                链路目录只覆盖仍在保留期内的日志。失败节点数用于诊断，不等同于业务失败次数。
              </div>
              <Panel
                title="最近链路"
                extra={<span className="muted">按首次保留记录倒序</span>}
              >
                <div className="list-tools">
                  <select
                    aria-label="Trace 流程"
                    value={traceFlow}
                    onChange={(e) => {
                      setTraceFlow(e.target.value);
                      setTraceBefore(null);
                    }}
                  >
                    <option value="business">业务链路</option>
                    <option value="">所有链路</option>
                    <option value="remember">Remember</option>
                    <option value="recall">Recall</option>
                    <option value="operate">Operate</option>
                    <option value="runtime">内部运行</option>
                  </select>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (query.trim())
                        select({ kind: "trace", id: query.trim() });
                    }}
                  >
                    <SearchOutlined />
                    <input
                      aria-label="查找 Trace ID"
                      placeholder="输入完整 Trace ID"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                    <button
                      className="text-btn"
                      disabled={!token || !query.trim()}
                    >
                      查看
                    </button>
                  </form>
                </div>
                <ErrorNotice text={s.traces.error} />
                {!s.traces.error && (
                  <TraceTable items={traces} select={select} />
                )}
                <div className="pagination">
                  <button
                    className="btn"
                    disabled={!traceBefore || busy}
                    onClick={() => setTraceBefore(null)}
                  >
                    回到最新
                  </button>
                  <span>每页 30 条 · 保留范围内</span>
                  <button
                    className="btn"
                    disabled={!s.traces.data?.next_before || busy}
                    onClick={() => setTraceBefore(s.traces.data!.next_before)}
                  >
                    下一页
                  </button>
                </div>
              </Panel>
            </>
          )}
          {page === "incidents" && (
            <Panel
              title="异常与复验记录"
              extra={<span className="muted">观察模式</span>}
            >
              <ErrorNotice text={s.incidents.error} />
              {incidents?.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>异常事件</th>
                        <th>规则</th>
                        <th>处置状态</th>
                        <th>业务复验</th>
                        <th>更新时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...incidents]
                        .sort((a, b) =>
                          b.updated_at.localeCompare(a.updated_at),
                        )
                        .map((i) => (
                          <tr key={i.incident_id}>
                            <td>
                              <IdLink
                                id={i.incident_id}
                                onClick={() =>
                                  select({ kind: "incident", value: i })
                                }
                              />
                            </td>
                            <td>{i.rule_id}</td>
                            <td>
                              <Badge state={i.state} />
                            </td>
                            <td>
                              <Badge state={i.verification} />
                            </td>
                            <td className="muted">{date(i.updated_at)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                !s.incidents.error && (
                  <Empty title={token ? "当前没有可见异常事件" : "等待连接"}>
                    异常处理与业务复验分别记录；此页面不会触发恢复操作。
                  </Empty>
                )
              )}
            </Panel>
          )}
          <footer className="content-footer">
            <span>
              <ClockCircleOutlined />
              所有时间按浏览器本地时区显示
            </span>
            <span>只读诊断 · Bearer 凭据仅保留在本页内存</span>
          </footer>
        </main>
      </div>
      <Drawer
        title={
          selection?.kind === "trace"
            ? "链路详情"
            : selection?.kind === "task"
              ? "任务详情"
              : "异常事件详情"
        }
        open={!!selection}
        onClose={() => setSelection(null)}
        width="min(1080px, 96vw)"
        destroyOnHidden
      >
        {selection?.kind === "trace" && (
          <TraceDetail key={selection.id} id={selection.id} token={token} />
        )}
        {selection?.kind === "task" && (
          <>
            <TaskDetail key={selection.id} id={selection.id} token={token} />
            {taskItems.find((t) => t.task_id === selection.id)?.trace_id && (
              <button
                className="btn"
                onClick={() =>
                  select({
                    kind: "trace",
                    id: taskItems.find((t) => t.task_id === selection.id)!
                      .trace_id!,
                  })
                }
              >
                查看关联 Trace
                <ArrowRightOutlined />
              </button>
            )}
          </>
        )}
        {selection?.kind === "incident" && (
          <>
            <div className="notice">
              处置完成不等于业务复验通过。证据引用与最终状态来自持久记录。
            </div>
            <dl className="facts">
              <dt>事件编号</dt>
              <dd>
                <code>{selection.value.incident_id}</code>
              </dd>
              <dt>规则</dt>
              <dd>{selection.value.rule_id}</dd>
              <dt>处置状态</dt>
              <dd>
                <Badge state={selection.value.state} />
              </dd>
              <dt>业务复验</dt>
              <dd>
                <Badge state={selection.value.verification} />
              </dd>
              <dt>发现时间</dt>
              <dd>{date(selection.value.opened_at)}</dd>
              <dt>更新时间</dt>
              <dd>{date(selection.value.updated_at)}</dd>
              <dt>原操作</dt>
              <dd>
                <code>{selection.value.operation_id ?? "—"}</code>
              </dd>
            </dl>
            <Panel title="证据引用">
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>用途</th>
                      <th>模块 / 对象</th>
                      <th>对象编号</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ...selection.value.evidence_refs.map((ref) => ({
                        ref,
                        label: "处置证据",
                      })),
                      ...selection.value.verification_refs.map((ref) => ({
                        ref,
                        label: "复验证据",
                      })),
                    ].map(({ ref, label }, i) => (
                      <tr key={i}>
                        <td>{label}</td>
                        <td>
                          {ref.owner} / {ref.object_type}
                        </td>
                        <td>
                          <code>{ref.object_id}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <details className="raw-record">
              <summary>查看完整异常记录</summary>
              <Json value={selection.value} />
            </details>
          </>
        )}
      </Drawer>
    </div>
  );
}

export default function Console() {
  const authenticated = useSyncExternalStore(subscribeAuthentication, getAuthenticationState);
  const [token, setToken] = useState("");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [authError, setAuthError] = useState("");
  const [loginReady, setLoginReady] = useState(false);
  const client = useRef<Keycloak | null>(null);
  const [tenant, setTenant] = useState("");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    browserIdentity().then((adapter) => {
      if (cancelled) return;
      client.current = adapter;
      setLoginReady(!!adapter);
      if (adapter?.token) setToken(adapter.token);
      if (adapter) timer = setInterval(async () => {
        if (!adapter.authenticated) return;
        try {
          await adapter.updateToken(40);
          if (!cancelled && adapter.token) setToken(adapter.token);
        } catch {
          if (!cancelled) {
            adapter.clearToken();
            setToken("");
            setIdentity(null);
            setAuthError("登录已过期，请重新登录。");
          }
        }
      }, 20000);
    }).catch(() => {
      if (!cancelled) setAuthError("身份服务暂不可用，请检查 Keycloak 和 P3 服务。");
    });
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    const check = async () => {
      try {
        const current = await readIdentity(token, controller.signal, tenant);
        if (!controller.signal.aborted) {
          setActiveTenant(current.scope.tenant_id);
          if (!tenant) setTenant(current.scope.tenant_id);
          setIdentity(current); setAuthError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setIdentity(null);
          setToken("");
          // P3 authorization failure must not discard a valid Keycloak login.
          setAuthError(error instanceof Error ? error.message : "身份验证失败，请重新登录。");
        }
      }
    };
    void check();
    const timer = setInterval(() => { void check(); }, 20000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [token, tenant]);

  const login = () => {
    setActiveTenant(""); setTenant(""); setIdentity(null);
    setAuthError("");
    void client.current?.login({ redirectUri: loginReturnUrl(), prompt: "login" })
      .catch(() => setAuthError("无法打开登录页，请检查身份服务。"));
  };
  const disconnect = () => {
    setActiveTenant(""); setTenant("");
    setToken("");
    setIdentity(null);
    setAuthError("");
    if (client.current?.authenticated) {
      void client.current.logout({ redirectUri: loginReturnUrl() })
        .catch(() => { client.current?.clearToken(); setAuthError("本页已断开；身份服务退出失败，请重新登录。"); });
    }
  };

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: "#2d654f", colorBgBase: "#fafbf8", colorBgContainer: "#ffffff", colorBgElevated: "#ffffff",
          colorText: "#242c27", colorTextSecondary: "#637067", colorBorder: "#dfe6dc", borderRadius: 12,
          fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif',
        },
      }}
    >
      <Monitor
        key={identity ? JSON.stringify([identity.principal_id, identity.scope, identity.permissions]) : "disconnected"}
        token={identity ? token : ""}
        identity={identity}
        authError={authError}
        organizationToolbar={identity?.organizations && <div className="organization-toolbar">
        <label htmlFor="organization-choice">当前组织</label>
        <select id="organization-choice" value={identity.scope.tenant_id} onChange={(event) => {
          const selected = event.target.value;
          setIdentity(null); setActiveTenant(selected); setTenant(selected);
        }}>
          {identity.organizations.map((org) => <option key={org.tenant_id} value={org.tenant_id}>
            {org.organization_name} · {org.tenant_id}
          </option>)}
        </select>
        <span>账号：{identity.username} · 角色：{identity.roles?.map(role => (
          ({ "organization-admin": "组织管理员", member: "普通成员", viewer: "只读成员" } as Record<string, string>)[role] ?? role
        )).join("、")}</span>
        {identity.roles?.includes("organization-admin") && <span className="organization-help">成员维护：由平台管理员在 Keycloak 中操作</span>}
      </div>}
        disconnect={disconnect}
        connect={() => { setDraft(""); setOpen(true); }}
      />
      <Modal
        title="登录 P3 监测台" open={open} footer={null}
        onCancel={() => { setOpen(false); setDraft(""); }}
        destroyOnHidden closeIcon={<CloseOutlined />}
      >
        {loginReady && <div className="identity-login">
          <p>使用 P3 身份平台登录。登录后由 P3 确认所属租户和访问权限。</p>
          <button className="btn primary" onClick={login}><LinkOutlined />{authenticated ? "切换账号" : "Keycloak 账号登录 / 切换账号"}</button>
        </div>}
        <form className="connection-form" onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim()) {
            client.current?.clearToken();
            setActiveTenant(""); setTenant("");
            setIdentity(null);
            setAuthError("");
            setToken(draft.trim());
            setDraft("");
            setOpen(false);
          }
        }}>
          <p>也可使用部署目录 credential 文件中的管理员凭据，需要 maintenance:diagnose 权限。</p>
          <label htmlFor="bearer">Bearer 凭据</label>
          <input id="bearer" type="password" autoComplete="off" placeholder="粘贴 credential 内容"
            value={draft} onChange={(e) => setDraft(e.target.value)} />
          <small>手动粘贴的凭据刷新后需重新输入。账号登录可在刷新后恢复有效会话，令牌仅保留在内存；断开连接会退出账号。</small>
          <button className="btn primary" disabled={!draft.trim()}><LinkOutlined />连接并读取监测</button>
        </form>
      </Modal>
    </ConfigProvider>
  );
}
