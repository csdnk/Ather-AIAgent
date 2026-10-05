import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import DemoPage from "./DemoPage";
import App from "../App";
import { id, reply, scenarios, snapshot } from "./test-fixtures";
import { fullScenarios, storySnapshot } from "./story-fixtures";

let posts: string[];
let reads: string[];
beforeEach(() => {
  sessionStorage.clear(); posts = []; reads = [];
  vi.mocked(window.getComputedStyle).mockImplementation(() =>
    Object.assign(document.createElement("div").style, { content: "none" }));
  vi.spyOn(crypto, "randomUUID").mockReturnValue(id);
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("scenarios")) return reply(scenarios);
    if (init?.method === "POST") { posts.push(String(init.body)); return reply(snapshot()); }
    reads.push(url); return reply(snapshot("passed"));
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("starts only on click, locks double clicks and renders actual recall text", async () => {
  render(<DemoPage />);
  const button = await screen.findByRole("button", { name: "开始演示" });
  await waitFor(() => expect(button).toBeEnabled());
  expect(posts).toHaveLength(0);
  fireEvent.click(button); fireEvent.click(button);
  expect(await screen.findByText("这是服务端实际返回的测试内容")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /演示进行中/ })).toBeDisabled();
  expect(screen.getAllByText("P3 实际召回").length).toBeGreaterThan(0);
  expect(posts).toHaveLength(1);
  expect(sessionStorage.getItem("p3-demo-run-id")).toBe(id);
  expect(screen.queryByText(/5 本.*30 天/)).not.toBeInTheDocument();
});

it("keeps the original ID after a lost POST response and only queries it", async () => {
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("scenarios")) return reply(scenarios);
    if (init?.method === "POST") { posts.push(String(init.body)); throw new TypeError("offline secret"); }
    reads.push(url); return new Promise(() => {});
  });
  render(<DemoPage />);
  await waitFor(() => expect(screen.getByRole("button", { name: "开始演示" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "开始演示" }));
  expect(await screen.findByText(/启动结果未确认/)).toBeInTheDocument();
  await waitFor(() => expect(reads).toEqual([`/p4-api/api/v1/demo/runs/${id}`]));
  expect(posts).toHaveLength(1);
  expect(sessionStorage.getItem("p3-demo-run-id")).toBe(id);
  expect(screen.queryByText(/offline secret/)).not.toBeInTheDocument();
});

it("keeps existing messages on read failure; manual recovery never POSTs", async () => {
  let failRead = true;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("scenarios")) return reply(scenarios);
    if (init?.method === "POST") { posts.push(String(init.body)); return reply(snapshot()); }
    reads.push(url);
    if (failRead) throw new TypeError("offline");
    return reply(snapshot("passed"));
  });
  render(<DemoPage />);
  await waitFor(() => expect(screen.getByRole("button", { name: "开始演示" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "开始演示" }));
  expect(await screen.findByText(/连接中断/)).toBeInTheDocument();
  expect(screen.getByText("这是服务端实际返回的测试内容")).toBeInTheDocument();
  failRead = false;
  fireEvent.click(screen.getByRole("button", { name: "重新查询" }));
  expect(await screen.findByRole("button", { name: "重新运行" })).toBeEnabled();
  expect(posts).toHaveLength(1);
});

it("recovers storage by GET on remount and aborts reads on unmount", async () => {
  sessionStorage.setItem("p3-demo-run-id", id);
  let pendingSignal: AbortSignal | null | undefined;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("scenarios")) return reply(scenarios);
    reads.push(url); pendingSignal = init?.signal;
    return new Promise(() => {});
  });
  const first = render(<DemoPage />);
  await waitFor(() => expect(reads).toHaveLength(1));
  first.unmount();
  expect(pendingSignal?.aborted).toBe(true);
  render(<DemoPage />);
  await waitFor(() => expect(reads).toHaveLength(2));
  expect(posts).toHaveLength(0);
});

it("does not erase an unknown old run or auto replay after a 404", async () => {
  sessionStorage.setItem("p3-demo-run-id", id);
  vi.stubGlobal("fetch", async (url: string) => url.endsWith("scenarios") ? reply(scenarios) : reply({}, 404));
  render(<DemoPage />);
  expect(await screen.findByText(/P4没有此轮记录，不能确认先前执行结果/)).toBeInTheDocument();
  expect(sessionStorage.getItem("p3-demo-run-id")).toBe(id);
  expect(screen.getByRole("button", { name: /待核对/ })).toBeDisabled();
});

it.each([
  ["blocked", "条件不足"], ["failed", "检查未通过"], ["unconfirmed", "结果未确认"],
] as const)("shows %s honestly and leaves skipped steps without fabricated replies", async (state, label) => {
  sessionStorage.setItem("p3-demo-run-id", id);
  const data = snapshot(state);
  data.steps[5].state = "skipped"; data.steps[5].response_text = "";
  vi.stubGlobal("fetch", async (url: string) => reply(url.endsWith("scenarios") ? scenarios : data));
  render(<DemoPage />);
  const status = await screen.findByRole("status");
  expect(within(status).getByText(label, { exact: true })).toBeInTheDocument();
  expect(screen.getByText("未执行")).toBeInTheDocument();
  if (state === "unconfirmed") {
    expect(screen.getByRole("button", { name: /待核对/ })).toBeDisabled();
    expect(screen.getByText(/后台可能仍在运行/)).toBeInTheDocument();
  }
});

it("renders upstream HTML as inert text and opens evidence with a native disclosure", async () => {
  sessionStorage.setItem("p3-demo-run-id", id);
  const data = snapshot("passed");
  data.steps[2].response_text = "<img src=x onerror=alert(1)>";
  vi.stubGlobal("fetch", async (url: string) => reply(url.endsWith("scenarios") ? scenarios : data));
  const view = render(<DemoPage />);
  expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  expect(view.container.querySelector("img")).toBeNull();
  expect(screen.getByText("查看证据").tagName).toBe("SUMMARY");
});

it("creates a fresh explicit run after completion", async () => {
  sessionStorage.setItem("p3-demo-run-id", id);
  render(<DemoPage />);
  const button = await screen.findByRole("button", { name: "重新运行" });
  const next = "a1100000-0000-4000-8000-000000000002";
  vi.mocked(crypto.randomUUID).mockReturnValue(next);
  fireEvent.click(button);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(JSON.parse(posts[0]).request_id).toBe(next);
});

it("retains the real monitoring entry without auto starting a demo", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "监测控制台" }));
  expect(await screen.findByRole("navigation", { name: "监测导航" })).toBeInTheDocument();
  expect(posts).toHaveLength(0);
});

it("keeps observing after ten minutes and stops at two hours without cancelling work", async () => {
  vi.useFakeTimers();
  sessionStorage.setItem("p3-demo-run-id", id);
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.endsWith("scenarios")) return reply(scenarios);
    reads.push(url); return reply(snapshot());
  });
  render(<DemoPage />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const started = Date.now();
  vi.setSystemTime(started + 600_000);
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(screen.queryByText(/观察已超时/)).not.toBeInTheDocument();
  vi.setSystemTime(started + 7_200_000);
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(screen.getByText(/观察已超时.*未取消后台任务/)).toBeInTheDocument();
  const count = reads.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(reads).toHaveLength(count);
});

it("selects S2 and locks scene switching while that same run is active", async () => {
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("scenarios")) return reply(fullScenarios);
    if (init?.method === "POST") posts.push(String(init.body));
    return reply(storySnapshot());
  });
  render(<DemoPage />);
  const weather = await screen.findByRole("button", { name: /雨天的周末计划/ });
  fireEvent.click(weather);
  expect(weather).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "开始演示" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(JSON.parse(posts[0]).scenario_id).toBe("weather-weekend");
  expect(await screen.findByText(/步骤 3 \/ 8/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /图书馆里的记忆/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /演示进行中/ })).toBeDisabled();
});

it("restores the actual scene and separates called, verified and missing coverage", async () => {
  sessionStorage.setItem("p3-demo-run-id", id);
  const data = storySnapshot("learning-review", "blocked");
  data.error = { code: "provider_unavailable", status: 503, message: "not available",
    operation_id: null, write_outcome: "not_applicable" };
  data.steps[6].state = "blocked"; data.steps[7].state = "skipped";
  vi.stubGlobal("fetch", async (url: string) => reply(url.endsWith("scenarios") ? fullScenarios : data));
  render(<DemoPage />);
  expect(await screen.findByRole("button", { name: /让学习留下线索/ })).toHaveAttribute("aria-pressed", "true");
  expect(await screen.findByText(/未配置可用的提炼模型/)).toBeInTheDocument();
  const panel = await screen.findByRole("region", { name: "接口覆盖" });
  expect(within(panel).getByText("已调用 3 / 4")).toBeInTheDocument();
  expect(within(panel).getByText("检查通过 1 / 4")).toBeInTheDocument();
  expect(within(panel).getByText("仅限隔离运维验收")).toBeInTheDocument();
  expect(within(panel).getByText(/非全量审计/)).toBeInTheDocument();
  expect(posts).toHaveLength(0);
});

it("uses the compact scene selector without auto-starting or diverging from the sidebar", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push(String(init.body));
      return reply(storySnapshot("learning-review"));
    }
    if (url.endsWith("scenarios")) return reply(fullScenarios);
    reads.push(url);
    return reply(storySnapshot("learning-review"));
  });
  render(<DemoPage />);
  const chooser = await screen.findByRole("combobox", { name: "切换场景" });
  await waitFor(() => expect(chooser).toHaveValue("library-full"));
  await user.selectOptions(chooser, "weather-weekend");
  expect(screen.getByRole("heading", { name: "雨天的周末计划" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /雨天的周末计划/ })).toHaveAttribute("aria-pressed", "true");
  await user.click(screen.getByRole("button", { name: /让学习留下线索/ }));
  expect(chooser).toHaveValue("learning-review");
  expect(sessionStorage.getItem("p3-demo-run-id")).toBeNull();
  expect(posts).toHaveLength(0);

  // Prove the request recorder is live: selection is read-only, explicit start is not.
  await user.click(screen.getByRole("button", { name: "开始演示" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(JSON.parse(posts[0]).scenario_id).toBe("learning-review");
});

it.each(["running", "unconfirmed"] as const)("locks the compact selector for a %s run", async state => {
  sessionStorage.setItem("p3-demo-run-id", id);
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") posts.push(String(init.body));
    else if (!url.endsWith("scenarios")) reads.push(url);
    return reply(url.endsWith("scenarios") ? fullScenarios : storySnapshot("weather-weekend", state));
  });
  render(<DemoPage />);
  const chooser = await screen.findByRole("combobox", { name: "切换场景" });
  await waitFor(() => expect(chooser).toHaveValue("weather-weekend"));
  expect(chooser).toBeDisabled();
  expect(screen.getByRole("button", { name: /图书馆里的记忆/ })).toBeDisabled();
  fireEvent.change(chooser, { target: { value: "library-full" } });
  expect(chooser).toHaveValue("weather-weekend");
  expect(sessionStorage.getItem("p3-demo-run-id")).toBe(id);
  expect(posts).toHaveLength(0);
});

it("reports passed-step progress rather than counting blocked or skipped steps as completed checks", async () => {
  sessionStorage.setItem("p3-demo-run-id", id);
  const data = snapshot("blocked");
  data.current_step = 4;
  data.steps[3].state = "blocked";
  data.steps[4].state = "skipped";
  data.steps[5].state = "skipped";
  vi.stubGlobal("fetch", async (url: string) => reply(url.endsWith("scenarios") ? scenarios : data));
  render(<DemoPage />);
  const progress = await screen.findByRole("progressbar", { name: "检查通过进度" });
  expect(progress).toHaveAttribute("max", "6");
  expect(progress).toHaveAttribute("value", "3");
  expect(progress).toHaveAttribute("aria-valuetext", "3 / 6 个步骤检查通过");
  expect(within(screen.getByRole("status")).getByText("条件不足")).toBeInTheDocument();
});

it("distinguishes a step identity from the operation of each actual HTTP request", async () => {
  const user = userEvent.setup();
  sessionStorage.setItem("p3-demo-run-id", id);
  const data = snapshot("passed");
  data.steps[2].evidence!.calls = [{
    method: "POST", path: "/p3/recall", status_code: 200, elapsed_ms: 12,
    operation_id: "op_actual_recall_request",
  }];
  vi.stubGlobal("fetch", async (url: string) => reply(url.endsWith("scenarios") ? scenarios : data));
  render(<DemoPage />);
  await user.click(await screen.findByText("查看证据"));
  const label = screen.getByText("步骤标识");
  expect(label.nextElementSibling).toHaveTextContent("op_demo_3");
  await user.click(screen.getByText(/本步实际调用/));
  expect(screen.getByText("请求 Operation").parentElement).toHaveTextContent("op_actual_recall_request");
});

it("lets keyboard users reach the horizontally scrollable coverage details", async () => {
  const user = userEvent.setup();
  sessionStorage.setItem("p3-demo-run-id", id);
  vi.stubGlobal("fetch", async (url: string) =>
    reply(url.endsWith("scenarios") ? fullScenarios : storySnapshot("weather-weekend", "passed")));
  render(<DemoPage />);
  await user.click(await screen.findByText("本轮接口覆盖与缺口"));
  await user.tab();
  expect(screen.getByRole("region", { name: "接口覆盖明细，可横向滚动" })).toHaveFocus();
});
