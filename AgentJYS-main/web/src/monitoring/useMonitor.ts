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
    if (!token) return;
    setSnapshot((old) => ({ ...old, tasks: {}, traces: {} }));
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      setBusy(true);
      const resource = async <T>(path: string): Promise<Resource<T>> => {
        try {
          return { data: await get<T>(path, token, controller.signal) };
        } catch (error) {
          return {
            error: controller.signal.aborted
              ? "请求超时，请刷新重试。"
              : message(error),
          };
        }
      };
      const taskQuery = new URLSearchParams({ limit: "30" });
      if (taskCursor) taskQuery.set("cursor", taskCursor);
      if (taskState) taskQuery.set("state", taskState);
      const traceQuery = new URLSearchParams({ limit: "30" });
      if (traceFlow) traceQuery.set("flow", traceFlow);
      if (traceBefore) traceQuery.set("before", String(traceBefore));
      const [health, runtime, capabilities, incidents, tasks, traces] =
        await Promise.all([
          resource<Health>("/p3/health"),
          resource<Runtime>("/p3/runtime"),
          resource<Capabilities>("/p3/capabilities"),
          resource<Incident[]>("/p3/incidents"),
          resource<TaskPage>(`/p3/tasks?${taskQuery}`),
          resource<TracePage>(`/p3/traces?${traceQuery}`),
        ]);
      if (stopped) return;
      setSnapshot({ health, runtime, capabilities, incidents, tasks, traces });
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
