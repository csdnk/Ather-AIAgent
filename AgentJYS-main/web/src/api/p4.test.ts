import { afterEach, describe, expect, it, vi } from "vitest";
import { createP4Session, sendP4Message } from "./p4";

describe("P4 API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("creates an upstream Agent session through the P4 proxy", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          session_id: "session-1",
          agent_id: "company-assistant",
          tenant_id: "demo-tenant",
          user_id: "demo-user",
          messages: [],
          created_at: "2026-08-24T00:00:00Z",
          updated_at: "2026-08-24T00:00:00Z",
        }),
        { status: 201 },
      ),
    );

    const session = await createP4Session();

    expect(session.session_id).toBe("session-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/p4-api/api/v1/sessions",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("keeps durable-memory intent explicit in a P4 turn", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ session: {}, context: {}, memory: {}, p3_calls: [] }), {
        status: 200,
      }),
    );

    await sendP4Message("session-1", "Remember this", true);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({
      content: "Remember this",
      durable_memory: true,
    });
  });
});
