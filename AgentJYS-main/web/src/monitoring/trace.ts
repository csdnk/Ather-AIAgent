import type { Log } from "./api";

export type Span = {
  id: string;
  parent: string | null;
  node: string;
  flow: string;
  start: number;
  duration: number | null;
  status: string;
  rows: Log[];
  depth: number;
};

export function buildSpans(records: Log[]): Span[] {
  const groups = new Map<string, Log[]>();
  for (const row of records)
    groups.set(row.span_id, [...(groups.get(row.span_id) ?? []), row]);
  const spans: Span[] = [...groups].map(([id, rows]) => {
    rows.sort((a, b) => a.sequence - b.sequence);
    const started = rows.find((r) => r.phase === "started");
    const terminal = [...rows]
      .reverse()
      .find((r) => ["returned", "failed", "cancelled"].includes(r.phase));
    const anchor = started ?? rows[0];
    return {
      id,
      parent: anchor.parent_span_id,
      node: anchor.node,
      flow: anchor.flow,
      start: started
        ? Date.parse(started.occurred_at)
        : Date.parse(anchor.occurred_at) - (anchor.elapsed_ms ?? 0),
      duration: terminal?.elapsed_ms ?? null,
      status: !started ? "partial" : (terminal?.phase ?? "open"),
      rows,
      depth: 0,
    };
  });
  const byId = new Map(spans.map((s) => [s.id, s]));
  for (const span of spans) {
    const seen = new Set([span.id]);
    let parent = span.parent;
    while (parent && byId.has(parent) && !seen.has(parent) && span.depth < 12) {
      seen.add(parent);
      span.depth++;
      parent = byId.get(parent)!.parent;
    }
  }
  return spans.sort((a, b) => a.start - b.start || a.depth - b.depth);
}
