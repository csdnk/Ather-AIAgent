import { beforeEach, describe, expect, it, vi } from "vitest";
import { generateEmbedding } from "./b1";

describe("b1 api", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        text: async () =>
          JSON.stringify({
            request_id: "req",
            trace_id: "trace",
            source_id: "source",
            status: "success",
            input_type: "query",
            records: [{ vector: [0.1, 0.2], chunk_id: "c", chunk_text: "hello" }],
          }),
      })),
    );
  });

  it("sends query input_type to the B1 proxy", async () => {
    await generateEmbedding({ text: "hello", inputType: "query" });
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/api/v1/b1/embeddings");
    expect(JSON.parse(call[1].body).input_type).toBe("query");
  });
});
