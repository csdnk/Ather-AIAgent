import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRightOutlined, BookOutlined, BulbOutlined, CloudOutlined, DeleteOutlined, EditOutlined } from "@ant-design/icons";
import { DemoApiError, getRun, listScenarios, startRun } from "./api";
import { observationMilliseconds } from "./budgets";
import CoveragePanel from "./CoveragePanel";
import type { RunSnapshot, RunState, ScenarioId, Scenarios, StepSnapshot, StepState } from "./types";
import "./demo.css";

const storageKey = "p3-demo-run-id";
const active = (state?: RunState) => state === "queued" || state === "running";
const labels: Record<RunState | StepState, string> = {
  queued: "等待执行", pending: "等待执行", running: "执行中", passed: "已通过",
  failed: "检查未通过", blocked: "条件不足", unconfirmed: "结果未确认", skipped: "未执行",
};
const scenarioIcons = {
  "library-basic": <BookOutlined />, "library-full": <BookOutlined />,
  "weather-weekend": <CloudOutlined />, "preference-update": <EditOutlined />,
  "learning-review": <BulbOutlined />, "forget-sources": <DeleteOutlined />,
};
const errorText: Record<string, string> = {
  not_ready: "P3 未就绪，本轮没有发送业务写入。请检查服务后手动开启新一轮。",
  recall_check_failed: "返回内容没有通过本轮检查，已停止后续步骤。",
  processing_failed: "记忆处理未成功，已停止后续步骤。",
  operation_failed: "P3 报告操作失败，已停止后续步骤。",
  check_failed: "真实返回未满足本轮检查，已停止后续步骤。展开证据查看失败项。",
  provider_unavailable: "未配置可用的提炼模型。本轮已记录真实任务结果，不会生成预写总结；这不是提炼通过。",
};
type Observation = { controller: AbortController; timer?: number };

function EvidenceDetails({ step }: { step: StepSnapshot }) {
  const e = step.evidence;
  if (!e) return null;
  return <details className="demo-evidence">
    <summary aria-label={`查看步骤 ${step.id} 的证据`}>查看证据</summary>
    <div className="demo-evidence-body">
      <div className="demo-endpoint">{e.method} {e.path}<span>{Math.round(e.elapsed_ms)} ms</span></div>
      <ul className="demo-checks">{step.checks.map((c, i) =>
        <li key={i} className={c.passed ? "demo-check-pass" : "demo-check-fail"}>
          <span aria-hidden="true">{c.passed ? "✓" : "!"}</span> {c.name} · {c.detail}
        </li>)}</ul>
      <dl>
        <dt>步骤标识</dt><dd>{e.operation_id}</dd>
        {e.job_id && <><dt>Temporal Job</dt><dd>{e.job_id}</dd></>}
        {e.recall_id && <><dt>Recall</dt><dd>{e.recall_id}</dd></>}
        {e.memories.map(m => <div className="demo-ref" key={`${m.memory_id}:${m.version}`}>
          <dt>Memory · v{m.version}</dt><dd>{m.memory_id}</dd>
        </div>)}
        {e.source_ids.length > 0 && <><dt>Sources</dt><dd>{e.source_ids.join(" · ")}</dd></>}
        {e.task_ids.length > 0 && <><dt>Tasks</dt><dd>{e.task_ids.join(" · ")}</dd></>}
      </dl>
      {e.processing.map(p => <p key={p.memory_id} className="demo-processing">
        {p.memory_id} · {p.memory_status} / {p.projection_state} / {p.processing_state}
      </p>)}
      {e.tasks.map(t => <p key={t.task_id} className="demo-processing">
        {t.kind} · {t.state} / {t.effect_status} · 完成分段 {t.completed_parts}
        {t.error_code && ` · ${t.error_code}`}
      </p>)}
      {e.cleanup_state && <p className="demo-processing">清理状态 · {e.cleanup_state}</p>}
      {e.calls.length > 0 && <details className="demo-call-list">
        <summary>本步实际调用 · {e.calls.length} 次</summary>
        <ul>{e.calls.map((call, i) => <li key={i}>
          <div className="demo-call-info"><code>{call.method} {call.path}</code>
            {call.operation_id && <small><span>请求 Operation</span> · <code>{call.operation_id}</code></small>}
          </div>
          <span className="demo-call-timing">{call.status_code ?? "未收到响应"} · {Math.round(call.elapsed_ms)} ms</span>
        </li>)}</ul>
      </details>}
      <details className="demo-raw"><summary>结构化证据 JSON</summary><pre>{JSON.stringify(e, null, 2)}</pre></details>
    </div>
  </details>;
}

export default function DemoPage() {
  const [scenarios, setScenarios] = useState<Scenarios | null>(null);
  const [run, setRun] = useState<RunSnapshot | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<ScenarioId | null>(null);
  const [recent, setRecent] = useState<Partial<Record<ScenarioId, RunState>>>({});
  const [notice, setNotice] = useState("");
  const [setupError, setSetupError] = useState("");
  const [starting, setStarting] = useState(false);
  const [querying, setQuerying] = useState(false);
  const startLock = useRef(false);
  const locked = useRef(false);
  const observation = useRef<Observation | null>(null);

  const stop = useCallback(() => {
    observation.current?.controller.abort();
    window.clearTimeout(observation.current?.timer);
    observation.current = null;
  }, []);

  const observe = useCallback((id: string, initial?: RunSnapshot) => {
    stop();
    const entry: Observation = { controller: new AbortController() };
    observation.current = entry;
    const deadline = Date.now() + observationMilliseconds;
    const apply = (value: RunSnapshot) => {
      setRun(value);
      setSelectedId(value.scenario_id);
      setRecent(prior => ({ ...prior, [value.scenario_id]: value.state }));
      locked.current = active(value.state) || value.state === "unconfirmed";
    };
    const poll = async () => {
      if (entry.controller.signal.aborted) return;
      if (Date.now() >= deadline) {
        setNotice("观察已超时，未取消后台任务。请重新查询原运行。");
        setQuerying(false);
        return;
      }
      setQuerying(true);
      try {
        const value = await getRun(id, entry.controller.signal);
        if (entry.controller.signal.aborted) return;
        apply(value); setNotice(""); setQuerying(false);
        if (active(value.state)) entry.timer = window.setTimeout(poll, 500);
      } catch (error) {
        if (entry.controller.signal.aborted) return;
        setQuerying(false);
        setNotice(error instanceof DemoApiError && error.status === 404
          ? "P4没有此轮记录，不能确认先前执行结果。请人工核对，不能自动重放。"
          : "连接中断，已保留当前步骤。请重新查询原运行；后台可能仍在运行。");
      }
    };
    if (initial) {
      apply(initial);
      if (active(initial.state)) entry.timer = window.setTimeout(poll, 500);
    } else void poll();
  }, [stop]);

  useEffect(() => {
    const controller = new AbortController();
    void listScenarios(controller.signal).then(value => {
      if (!controller.signal.aborted) {
        setScenarios(value);
        setSelectedId(prior => prior ?? value.items[0]?.id ?? null);
        if (!value.enabled) setSetupError("演示未启用，请检查 P4 的本地演示配置。");
      }
    }).catch(() => {
      if (!controller.signal.aborted) setSetupError("无法连接 P4，请先按启动说明开启本地服务，再刷新页面。");
    });
    try {
      const saved = sessionStorage.getItem(storageKey);
      if (saved) {
        locked.current = true; setRunId(saved); setNotice("正在核对原运行，不会重新发送开始请求。");
        observe(saved);
      }
    } catch { setSetupError("浏览器会话存储不可用，为防止重复写入，暂不能开始演示。"); }
    return () => { controller.abort(); stop(); };
  }, [observe, stop]);

  const begin = async () => {
    if (startLock.current || locked.current || !scenarios?.enabled || !selectedId || setupError) return;
    startLock.current = true; locked.current = true; setStarting(true);
    stop();
    const controller = new AbortController();
    observation.current = { controller };
    let id: string;
    try {
      id = crypto.randomUUID();
      sessionStorage.setItem(storageKey, id);
    } catch {
      setSetupError("无法保存本轮标识，为防止重复写入，未开始演示。");
      startLock.current = false; locked.current = false; setStarting(false);
      return;
    }
    setRunId(id); setRun(null); setNotice("");
    try {
      const value = await startRun(id, controller.signal, selectedId);
      if (!controller.signal.aborted) observe(id, value);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof DemoApiError && error.rejected) {
        locked.current = false; setRunId(null); setNotice(error.message);
        try { sessionStorage.removeItem(storageKey); } catch { /* no write was accepted */ }
      } else {
        setNotice("启动结果未确认，正在查询原运行；不会重新发送开始请求。");
        observe(id);
      }
    } finally {
      startLock.current = false;
      setStarting(false);
    }
  };

  const uncertain = !!runId && (!run || run.state === "unconfirmed" || !!notice);
  const running = starting || active(run?.state);
  const disabled = running || uncertain || !scenarios?.enabled || !selectedId || !!setupError;
  const selected = scenarios?.items.find(s => s.id === selectedId);
  const select = (id: ScenarioId) => {
    if (locked.current || startLock.current || running || uncertain || selectedId === id) return;
    stop();
    try { sessionStorage.removeItem(storageKey); }
    catch { setSetupError("无法更新会话标识，为防止重复写入，暂不能切换场景。"); return; }
    setSelectedId(id); setRun(null); setRunId(null); setNotice("");
  };
  const progressed = run?.steps.filter(s => s.state === "passed").length ?? 0;
  return <div className="demo-shell">
    <a className="demo-skip-link" href="#demo-content">跳到演示内容</a>
    <div className="demo-layout">
      <aside className="demo-sidebar" aria-label="预设场景">
        <div className="demo-sidebar-content">
        <p className="demo-eyebrow">演示场景 <span>{String(scenarios?.items.length ?? 5).padStart(2, "0")}</span></p>
        <div className="demo-compact-selector">
          <label htmlFor="demo-scenario-select">切换场景</label>
          <select id="demo-scenario-select" value={selectedId ?? ""}
            disabled={running || uncertain || !scenarios?.items.length || !!setupError}
            onChange={event => select(event.target.value as ScenarioId)}>
            {!selected && <option value={selectedId ?? ""}>正在读取场景…</option>}
            {scenarios?.items.map((s, i) => <option key={s.id} value={s.id}>
              S{String(i + 1).padStart(2, "0")} · {s.title}
            </option>)}
          </select>
        </div>
        <div className="demo-scenario-list">
          {scenarios?.items.map((s, i) => <button type="button" key={s.id}
            className="demo-scenario" aria-pressed={selectedId === s.id}
            disabled={running || uncertain} onClick={() => select(s.id)}>
            <span className="demo-card-top"><span className="demo-book" aria-hidden="true">{scenarioIcons[s.id]}</span>
              <span className="demo-small-tag">S{String(i + 1).padStart(2, "0")}</span></span>
            <span className="demo-card-title">{s.title}</span>
            <span className="demo-card-description">{s.description}</span>
            <span className="demo-scenario-meta"><span>{s.total_steps} 个步骤</span>
              <span className={`demo-status demo-status-${recent[s.id] ?? "pending"}`}>{recent[s.id] ? labels[recent[s.id]!] : "待验证"}</span>
            </span>
          </button>)}
        </div>
        <div className="demo-sidebar-note"><strong>预设对话，真实结果</strong><p>不是自由聊天。每轮使用独立数据范围，不用预写答案替代接口结果。</p></div>
        <div className="demo-pipeline"><span>Web</span><b>→</b><span>P4</span><b>→</b><span>P3</span></div>
        </div>
      </aside>
      <main className="demo-main" id="demo-content" tabIndex={-1}>
        <div className="demo-thread">
          <div className="demo-heading"><div><p className="demo-eyebrow">MEMORY IN CONTEXT</p><h1>让记忆有据可查。</h1></div><span className="demo-version">P3 / LIVE EVIDENCE</span></div>
          <p className="demo-intro">{selected?.description ?? "看见保存、等待与召回，沿着真实证据走完一段对话。"}</p>
          <p className="demo-intro">演示使用 P4 服务端配置的身份；在监测控制台切换组织，不会改变本页演示所用的租户。</p>
          <section className="demo-mode" aria-label="实际运行模式">
            {run?.mode ? <>
              <span>环境 <b>{run.mode.profile}</b></span><span>Embedding <b>{run.mode.embedding}</b></span>
              <span>调度 <b>{run.mode.scheduling}</b></span>
              <span>语义 <b>{run.mode.semantic_processing}</b></span>
              <span>存储 <b>{run.mode.object_storage}</b></span><span>执行器 <b>{run.mode.executor}</b></span>
            </> : <span>运行模式待预检 · 未开始前不假定服务就绪</span>}
          </section>
          {run?.mode && <p className="demo-baseline-note">当前展示的是上述实际模式；lexical / literal_baseline 不代表真实向量、P2 或模型提炼已验证。</p>}
          {setupError && <div role="alert" className="demo-notice">{setupError}</div>}
          {notice && <div role="alert" className="demo-notice">{notice}</div>}
          {run?.error && <div className="demo-notice" role="alert">
            {errorText[run.error.code] ?? "执行结果尚未确认，请核对原任务。不会重发写入。"}
          </div>}
          {run?.state === "unconfirmed" && <p className="demo-notice">后台可能仍在运行；停止观察不代表取消，请核对原任务。</p>}
          {run ? <>
            <div className={`demo-run-overview demo-run-${run.state}`}>
              <div className="demo-run-status" role="status">
                <span className={`demo-status demo-status-${run.state}`}>{labels[run.state]}</span>
                <span>步骤 {run.current_step} / {run.total_steps} · {progressed} 项通过</span>
              </div>
              <progress className="demo-progress" aria-label="检查通过进度"
                aria-valuetext={`${progressed} / ${run.total_steps} 个步骤检查通过`}
                value={progressed} max={run.total_steps} />
            </div>
            <ol className="demo-conversation">{run.steps.map(step => <li key={step.id} className={`demo-turn demo-turn-${step.state}`}>
              <div className="demo-turn-heading"><span>STEP {String(step.id).padStart(2, "0")}</span><span className={`demo-status demo-status-${step.state}`}>{labels[step.state]}</span></div>
              <div className="demo-message demo-user"><span className="demo-message-label">预设用户发言</span><p>{step.user_text}</p></div>
              {step.state !== "pending" && step.state !== "skipped" && <div className="demo-result">
                <span className="demo-result-mark" aria-hidden="true">a</span>
                <div className="demo-result-content">
                  <span className="demo-message-label">{step.evidence?.path === "/p3/recall" ? "P3 实际召回" : "模拟 Agent · 基于 P3 实际结果"}</span>
                  {step.response_text ? <p className="demo-response">{step.response_text}</p>
                    : <p className="demo-muted">{step.state === "running" ? "正在等待服务端确认…" : "没有已确认的响应内容。"}</p>}
                  <EvidenceDetails step={step} />
                </div>
              </div>}
            </li>)}</ol>
            <CoveragePanel run={run} />
            <p className="demo-run-id">RUN · {run.run_id}</p>
          </> : <section className="demo-empty">
            <div className="demo-empty-symbol" aria-hidden="true">{selected ? scenarioIcons[selected.id] : <BookOutlined />}</div>
            <p className="demo-empty-caption">从一段故事，看见记忆如何工作</p>
            <h2>{selected?.title ?? "选择一段记忆故事"}</h2><p>{selected ? `${selected.total_steps} 个固定步骤，从真实调用走到结果核验。` : "从左侧选择场景，开始一次有证据的验证。"}</p>
            <div className="demo-empty-flow"><span><i>01</i>预设故事</span><b>→</b><span><i>02</i>真实调用</span><b>→</b><span><i>03</i>结果核验</span></div>
            <small>不是自由聊天 · 不生成预写答案 · 不具备条件时明确停止</small>
          </section>}
        </div>
        <footer className="demo-actions">
          <div><strong>{running ? "正在执行真实接口调用" : uncertain ? "请先核对此轮结果" : run ? `本轮${labels[run.state]}` : "准备好，开始验证记忆"}</strong>
            <p>{running ? "离开页面不会取消后台任务；返回后只查询原运行。" : uncertain ? "只查询原运行，不会重新发送写入。" : run ? "展开证据核对结果，或手动开启新一轮。" : "每次手动开始，创建一轮独立演示。"}</p></div>
          <div className="demo-action-buttons">
            {runId && (notice || run?.state === "unconfirmed") && <button className="demo-secondary" disabled={querying || starting}
              onClick={() => { setNotice("正在重新查询原运行…"); observe(runId); }}>{querying ? "查询中…" : "重新查询"}</button>}
            <button className="demo-primary" disabled={disabled} onClick={() => void begin()}>
              {running ? "演示进行中…" : uncertain ? "待核对原运行" : run ? "重新运行" : "开始演示"}
              <span aria-hidden="true"><ArrowRightOutlined /></span>
            </button>
          </div>
        </footer>
      </main>
    </div>
  </div>;
}
