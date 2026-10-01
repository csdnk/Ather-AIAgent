import { afterEach, expect, it, vi } from "vitest";
import { getRun, listScenarios, startRun } from "./api";
import { id, reply, scenarios, snapshot } from "./test-fixtures";
import { fullScenarios, storySnapshot } from "./story-fixtures";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("uses only fixed relative routes and the supplied UUID, without credentials", async () => {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return reply(url.endsWith("scenarios") ? scenarios : snapshot());
  });
  expect((await listScenarios()).enabled).toBe(true);
  expect((await startRun(id)).run_id).toBe(id);
  await getRun(id);
  expect(calls.map(c => [c.url, c.init?.method])).toEqual([
    ["/p4-api/api/v1/demo/scenarios", "GET"],
    ["/p4-api/api/v1/demo/runs", "POST"],
    [`/p4-api/api/v1/demo/runs/${id}`, "GET"],
  ]);
  expect(JSON.parse(String(calls[1].init?.body))).toEqual({ scenario_id: "library-basic", request_id: id });
  expect(calls.every(c => c.init?.credentials === "omit")).toBe(true);
  expect(calls.every(c => !JSON.stringify(c.init?.headers).includes("Authorization"))).toBe(true);
});

it("rejects path injection before sending any request", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  await expect(getRun("../secret")).rejects.toMatchObject({ code: "invalid_id" });
  await expect(startRun("not-a-uuid")).rejects.toMatchObject({ code: "invalid_id" });
  expect(fetch).not.toHaveBeenCalled();
});

it("bounds fetch to five seconds and does not retry", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  }));
  vi.stubGlobal("fetch", fetch);
  const result = startRun(id).catch(error => error);
  await vi.advanceTimersByTimeAsync(5000);
  expect(await result).toMatchObject({ code: "connection", rejected: false });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("sanitizes server errors and distinguishes explicit rejection", async () => {
  vi.stubGlobal("fetch", async () => reply({ error: { code: "unsafe", message: "secret-token" } }, 403));
  await expect(startRun(id)).rejects.toMatchObject({ rejected: true, status: 403 });
  await expect(startRun(id)).rejects.not.toThrow("secret-token");
});

it("rejects a mismatched run response instead of displaying another run", async () => {
  vi.stubGlobal("fetch", async () => reply(snapshot("passed", "a1100000-0000-4000-8000-000000000002")));
  await expect(getRun(id)).rejects.toMatchObject({ code: "protocol" });
});

it("decodes five scenarios and posts the selected ID with dynamic steps", async () => {
  const sent: string[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("scenarios")) return reply(fullScenarios);
    sent.push(String(init?.body));
    return reply(storySnapshot());
  });
  expect((await listScenarios()).items).toHaveLength(5);
  const run = await startRun(id, undefined, "weather-weekend");
  expect(run.total_steps).toBe(8);
  expect(run.coverage[2].state).toBe("blocked");
  expect(JSON.parse(sent[0])).toEqual({ scenario_id: "weather-weekend", request_id: id });
});

it("rejects another scenario in a start response even if the UUID matches", async () => {
  vi.stubGlobal("fetch", async () => reply(storySnapshot("library-full")));
  await expect(startRun(id, undefined, "weather-weekend")).rejects.toMatchObject({ code: "protocol" });
});

it.each(["steps", "coverage", "diagnostics"])("rejects malformed %s instead of rendering it", async field => {
  const data = storySnapshot();
  if (field === "steps") data.steps.pop();
  if (field === "coverage") Object.assign(data.coverage[0], { calls: -1, state: "success" });
  if (field === "diagnostics") Object.assign(data.diagnostics, { notes: [{}] });
  vi.stubGlobal("fetch", async () => reply(data));
  await expect(getRun(id)).rejects.toMatchObject({ code: "protocol" });
});
