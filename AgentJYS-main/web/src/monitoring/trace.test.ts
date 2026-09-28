import { describe, expect, it } from "vitest";
import type { Log } from "./api";
import { buildSpans } from "./trace";

function log(
  sequence: number,
  span: string,
  phase: string,
  overrides: Partial<Log> = {},
): Log {
  return {
    sequence,
    span_id: span,
    phase,
    occurred_at: `2026-09-28T00:00:00.${String(sequence).padStart(3, "0")}Z`,
    trace_id: "trace",
    parent_span_id: null,
    node: "recall_read",
    flow: "recall",
    level: "info",
    elapsed_ms: phase === "started" ? null : 2,
    reason_code: null,
    request_id: "r",
    operation_id: "o",
    task_id: null,
    ...overrides,
  };
}
describe("trace waterfall evidence", () => {
  it("retains parent relationships and uses terminal elapsed time", () => {
    const spans = buildSpans([
      log(4, "child", "returned", { parent_span_id: "root", elapsed_ms: 7 }),
      log(1, "root", "started"),
      log(2, "child", "started", { parent_span_id: "root" }),
      log(5, "root", "returned", { elapsed_ms: 12 }),
    ]);
    expect(spans.map((s) => [s.id, s.depth, s.duration])).toEqual([
      ["root", 0, 12],
      ["child", 1, 7],
    ]);
  });
  it("does not turn a transaction commit into a completed span", () => {
    const [span] = buildSpans([
      log(1, "a", "started"),
      log(2, "a", "committed"),
    ]);
    expect(span.status).toBe("open");
    expect(span.duration).toBeNull();
  });
  it("marks a missing start as incomplete even if a return survived retention", () => {
    const [span] = buildSpans([log(5, "a", "returned")]);
    expect(span.status).toBe("partial");
  });
  it("handles interrupted calls, failure and malformed parent cycles", () => {
    const spans = buildSpans([
      log(1, "a", "started", { parent_span_id: "b" }),
      log(2, "b", "started", { parent_span_id: "a" }),
      log(3, "a", "failed"),
    ]);
    expect(spans.find((s) => s.id === "a")?.status).toBe("failed");
    expect(spans.find((s) => s.id === "b")?.duration).toBeNull();
    expect(spans.every((s) => s.depth <= 2)).toBe(true);
  });
});
