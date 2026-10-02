import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { get } from "./api";
import { useMonitor } from "./useMonitor";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  get: vi.fn(),
}));
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

it("does not request protected data before connecting", () => {
  const hook = renderHook(() =>
    useMonitor("", 5, 0, null, "", null, "business"),
  );
  expect(get).not.toHaveBeenCalled();
  expect(hook.result.current.samples).toEqual([]);
});

it("clears failed snapshots, keeps gaps unknown, and recovers on the next poll", async () => {
  vi.useFakeTimers();
  vi.mocked(get).mockImplementation(async (path) =>
    path === "/p3/runtime"
      ? { pending_tasks: 3, unacknowledged_deliveries: 2 }
      : { items: [] },
  );
  const hook = renderHook(() =>
    useMonitor("test-token", 5, 0, null, "", null, "business"),
  );
  await act(async () => {});
  expect(hook.result.current.snapshot.runtime.data?.pending_tasks).toBe(3);
  vi.mocked(get).mockRejectedValue(new TypeError("network error"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(hook.result.current.snapshot.runtime.data).toBeUndefined();
  expect(hook.result.current.snapshot.runtime.error).toContain("无法连接");
  expect(hook.result.current.samples.at(-1)?.pending).toBeNull();
  vi.mocked(get).mockImplementation(async (path) =>
    path === "/p3/runtime"
      ? { pending_tasks: 1, unacknowledged_deliveries: 0 }
      : { items: [] },
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(hook.result.current.snapshot.runtime.data?.pending_tasks).toBe(1);
  expect(hook.result.current.samples.map((s) => s.pending)).toEqual([
    3,
    null,
    1,
  ]);
  hook.unmount();
  const calls = vi.mocked(get).mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(get).toHaveBeenCalledTimes(calls);
});

it("shows fast resources while a health probe is still pending", async () => {
  let resolveHealth!: (value: unknown) => void;
  vi.mocked(get).mockImplementation(async (path) => path === "/p3/health"
    ? new Promise((resolve) => { resolveHealth = resolve; })
    : path === "/p3/runtime" ? {pending_tasks: 2, unacknowledged_deliveries: 0} : {items: []});
  const hook = renderHook(() => useMonitor("test-token", 0, 0, null, "", null, "business"));
  await act(async () => {});
  expect(hook.result.current.snapshot.runtime.data?.pending_tasks).toBe(2);
  expect(hook.result.current.snapshot.health.data).toBeUndefined();
  expect(hook.result.current.busy).toBe(true);
  await act(async () => resolveHealth({readiness: "ready"}));
  expect(hook.result.current.snapshot.health.data?.readiness).toBe("ready");
  hook.unmount();
});


it("preserves chart history during renewal but clears it for another identity", async () => {
  vi.mocked(get).mockImplementation(async (path) => path === "/p3/runtime"
    ? { pending_tasks: 3, unacknowledged_deliveries: 0 } : { items: [] });
  const hook = renderHook(({ token, identity }) => useMonitor(token, 0, 0, null, "", null, "business", identity), {
    initialProps: { token: "old-token", identity: "tenant-a" },
  });
  await act(async () => {});
  expect(hook.result.current.samples).toHaveLength(1);
  hook.rerender({ token: "renewed-token", identity: "tenant-a" });
  await act(async () => {});
  expect(hook.result.current.samples).toHaveLength(2);
  expect(vi.mocked(get).mock.calls.at(-1)?.[1]).toBe("renewed-token");
  hook.rerender({ token: "b-token", identity: "tenant-b" });
  await act(async () => {});
  expect(hook.result.current.samples).toHaveLength(1);
  hook.rerender({ token: "", identity: "" });
  expect(hook.result.current.samples).toEqual([]);
  expect(hook.result.current.snapshot.runtime.data).toBeUndefined();
  hook.unmount();
});
