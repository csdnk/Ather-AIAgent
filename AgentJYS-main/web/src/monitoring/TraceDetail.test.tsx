import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { get, type LogPage } from "./api";
import { TraceDetail } from "./Console";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()), get: vi.fn(),
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("keeps an inspected node open during renewal and clears it when access is denied", async () => {
  const data: LogPage = {
    trace_id: "trace-a", next_after: null, coverage: "retained", local_dropped_records: 0,
    last_pruned_at: null, overview: { span_count: 1, failed_span_count: 0, open_span_count: 0 },
    records: ["started", "returned"].map((phase, sequence) => ({
      sequence, occurred_at: "2026-10-01T00:00:00.000Z", trace_id: "trace-a", span_id: "span-a",
      parent_span_id: null, node: "health_report", flow: "runtime", phase, level: "info",
      elapsed_ms: phase === "started" ? null : 2, reason_code: null,
      request_id: "request-a", operation_id: "operation-a", task_id: null,
    })),
  };
  vi.mocked(get).mockResolvedValue(data);
  const view = render(<TraceDetail id="trace-a" token="old-token" />);
  fireEvent.click(await screen.findByRole("button", { name: /health_report/ }));
  expect(await screen.findByRole("dialog")).toBeVisible();
  let finish!: (value: LogPage) => void;
  vi.mocked(get).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  view.rerender(<TraceDetail id="trace-a" token="renewed-token" />);
  expect(screen.getByRole("dialog")).toBeVisible();
  await act(async () => finish(data));
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(vi.mocked(get).mock.calls.at(-1)?.[1]).toBe("renewed-token");
  vi.mocked(get).mockRejectedValue(new Error("access denied"));
  view.rerender(<TraceDetail id="trace-a" token="revoked-token" />);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /health_report/ })).not.toBeInTheDocument();
});
