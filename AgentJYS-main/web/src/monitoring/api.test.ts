import { afterEach, describe, expect, it, vi } from "vitest";
import { get } from "./api";

afterEach(() => vi.unstubAllGlobals());
describe("unified monitor HTTP", () => {
  it("uses a Bearer header and disables cache/redirects without putting credentials in URLs", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response('{"items":[]}', {
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    expect(await get("/p3/tasks", "test-only-token")).toEqual({ items: [] });
    expect(fetcher.mock.calls[0][0]).toBe("/p3/tasks");
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      headers: { Authorization: "Bearer test-only-token" },
      cache: "no-store",
      redirect: "error",
    });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it.each([401, 403, 503])(
    "surfaces HTTP %i without falling back to simulated data",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response("{}", { status })),
      );
      await expect(get("/p3/health", "test-token")).rejects.toThrow();
    },
  );
  it("rejects a misconfigured proxy returning the SPA document", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html/>", { headers: { "content-type": "text/html" } }),
        ),
    );
    await expect(get("/p3/traces", "test-token")).rejects.toThrow("反向代理");
  });
});
