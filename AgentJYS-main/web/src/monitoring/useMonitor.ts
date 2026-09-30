import { useEffect, useState } from "react";
import {
  get,
  message,
  type Resource,
  type Health,
  type Runtime,
  type Capabilities,
  type Incident,
  type TaskPage,
  type TracePage,
  type Operation,
} from "./api";

export type Sample = {
  at: number;
  pending: number | null;
  deliveries: number | null;
};
export type Snapshot = {
  health: Resource<Health>;
  runtime: Resource<Runtime>;
  capabilities: Resource<Capabilities>;
  incidents: Resource<Incident[]>;
  tasks: Resource<TaskPage>;
  traces: Resource<TracePage>;
};
const empty: Snapshot = {
  health: {},
  runtime: {},
  capabilities: {},
  incidents: {},
  tasks: {},
  traces: {},
};

export function useOperation(id: string, token: string): Resource<{ operation: Operation; result?: unknown }> {
  const owner = `${id}:${token}`;
  const [snapshot, setSnapshot] = useState<{ owner: string; value: Resource<{ operation: Operation; result?: unknown }> }>({ owner, value: {} });
  useEffect(() => {
    setSnapshot({ owner, value: {} });
    if (!id || !token) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const path = `/p3/operations/${encodeURIComponent(id)}`;
        const operation = await get<Operation>(path, token, controller.signal);
        const result = operation.state === "succeeded" ? await get<unknown>(`${path}/result`, token, controller.signal) : undefined;
        if (!controller.signal.aborted) setSnapshot({ owner, value: { data: { operation, result } } });
      } catch (error) {
        if (!controller.signal.aborted) setSnapshot({ owner, value: { error: message(error) } });
      }
      // Revalidate terminal results too: revocation must clear an open details view.
      if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 2000);
    }
    void poll();
    return () => { clearTimeout(timer); controller.abort(); };
  }, [id, token, owner]);
  return snapshot.owner === owner ? snapshot.value : {};
}

export function useMonitor(
  token: string,
  seconds: number,
  revision: number,
  taskCursor: string | null,
  taskState: string,
  traceBefore: number | null,
  traceFlow: string,
) {
  const [snapshot, setSnapshot] = useState<Snapshot>(empty);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState<number | null>(null);
  useEffect(() => {
    setSnapshot(empty);
    setSamples([]);
    setChecked(null);
    if (!token) { setBusy(false); return; }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      setBusy(true);
      const resource = async <K extends keyof Snapshot>(key: K, path: string): Promise<Snapshot[K]> => {
        let result: Snapshot[K];
        try {
          result = { data: await get(path, token, controller.signal) } as Snapshot[K];
        } catch (error) {
          result = {
            error: controller.signal.aborted
              ? "请求超时，请刷新重试。"
              : message(error),
          } as Snapshot[K];
        }
        if (!stopped) setSnapshot((old) => ({ ...old, [key]: result }));
        return result;
      };
      const taskQuery = new URLSearchParams({ limit: "30" });
      if (taskCursor) taskQuery.set("cursor", taskCursor);
      if (taskState) taskQuery.set("state", taskState);
      const traceQuery = new URLSearchParams({ limit: "30" });
      if (traceFlow) traceQuery.set("flow", traceFlow);
      if (traceBefore) traceQuery.set("before", String(traceBefore));
      const [, runtime] =
        await Promise.all([
          resource("health", "/p3/health"),
          resource("runtime", "/p3/runtime"),
          resource("capabilities", "/p3/capabilities"),
          resource("incidents", "/p3/incidents"),
          resource("tasks", `/p3/tasks?${taskQuery}`),
          resource("traces", `/p3/traces?${traceQuery}`),
        ]);
      if (stopped) return;
      setChecked(Date.now());
      setBusy(false);
      setSamples((old) =>
        [
          ...old,
          {
            at: Date.now(),
            pending: runtime.data?.pending_tasks ?? null,
            deliveries: runtime.data?.unacknowledged_deliveries ?? null,
          },
        ].slice(-60),
      );
      if (seconds && !controller.signal.aborted)
        timer = setTimeout(poll, seconds * 1000);
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [token, seconds, revision, taskCursor, taskState, traceBefore, traceFlow]);
  return { snapshot, samples, checked, busy };
}
