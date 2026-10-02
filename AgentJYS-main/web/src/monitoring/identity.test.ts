import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({ init: vi.fn(), constructor: vi.fn() }));
vi.mock("keycloak-js", () => ({
  default: class {
    constructor(config: unknown) { sdk.constructor(config); }
    authenticated = false;
    async init(options: unknown) { this.authenticated = await sdk.init(options); return this.authenticated; }
  },
}));
beforeEach(() => { vi.resetModules(); vi.clearAllMocks(); sdk.init.mockResolvedValue(false); });
afterEach(() => vi.unstubAllGlobals());
const config = { enabled: true, url: "https://identity.example", realm: "p3-demo", client_id: "monitor" };

describe("browser identity entry", () => {
  it("initializes once for concurrent consumers using PKCE S256 and no client secret", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(config)));
    vi.stubGlobal("fetch", fetcher);
    const { browserIdentity } = await import("./identity");
    const [first, second] = await Promise.all([browserIdentity(), browserIdentity()]);
    expect(first).toBe(second);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(sdk.constructor).toHaveBeenCalledExactlyOnceWith({ url: config.url, realm: config.realm, clientId: config.client_id });
    expect(sdk.init).toHaveBeenCalledExactlyOnceWith({ onLoad: "check-sso", pkceMethod: "S256", checkLoginIframe: false, responseMode: "query", silentCheckSsoRedirectUri: window.location.origin + "/silent-check-sso.html", redirectUri: window.location.origin + "/" });
  });
  it("restores a valid SSO session after a page reload and publishes logout", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(config))));
    sdk.init.mockResolvedValue(true);
    const auth = await import("./identity");
    const observer = vi.fn();
    const unsubscribe = auth.subscribeAuthentication(observer);
    const client = await auth.browserIdentity();
    expect(auth.getAuthenticationState()).toBe(true);
    expect(observer).toHaveBeenCalledOnce();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    client!.authenticated = false;
    client!.onAuthLogout!();
    expect(auth.getAuthenticationState()).toBe(false);
    expect(observer).toHaveBeenCalledTimes(2);
    unsubscribe();
    client!.authenticated = true;
    client!.onAuthSuccess!();
    expect(observer).toHaveBeenCalledTimes(2);
  });
  it("stays logged out when no server session can be restored", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(config))));
    const auth = await import("./identity");
    await auth.browserIdentity();
    expect(auth.getAuthenticationState()).toBe(false);
    expect(sdk.init).toHaveBeenCalledWith(expect.objectContaining({ onLoad: "check-sso" }));
  });
  it.each(["demo", "monitor"])("returns to %s after SSO without forwarding callback secrets", async (view) => {
    window.history.replaceState({}, "", "/?view=" + view + "&code=test&state=private&redirect=other#secret");
    const { loginReturnUrl } = await import("./identity");
    expect(loginReturnUrl()).toBe(window.location.origin + "/?view=" + view);
    window.history.replaceState({}, "", "/");
  });
  it("keeps manual login usable when identity entry is disabled", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"enabled":false}')));
    const { browserIdentity } = await import("./identity");
    expect(await browserIdentity()).toBeNull();
    expect(sdk.init).not.toHaveBeenCalled();
  });
  it("does not initialize from a failed config endpoint", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 503 })));
    const { browserIdentity } = await import("./identity");
    await expect(browserIdentity()).rejects.toThrow("P3");
    expect(sdk.init).not.toHaveBeenCalled();
  });
  it("surfaces adapter initialization failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(config))));
    sdk.init.mockRejectedValueOnce(new Error("identity unavailable"));
    const { browserIdentity } = await import("./identity");
    await expect(browserIdentity()).rejects.toThrow("identity unavailable");
  });
  it("uses server-confirmed identity and keeps the credential out of URLs and storage", async () => {
    const identity = { principal_id: "a", scope: { tenant_id: "tenant_a", application_id: "app", user_id: "a", agent_id: "agent" }, permissions: [] };
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(identity)));
    vi.stubGlobal("fetch", fetcher);
    const { readIdentity } = await import("./identity");
    const abort = new AbortController();
    expect(await readIdentity("test-token", abort.signal)).toEqual(identity);
    expect(fetcher).toHaveBeenCalledWith("/p3/auth/me", { headers: { Authorization: "Bearer test-token" }, cache: "no-store", redirect: "error", signal: abort.signal });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it("sends the selected organization in a header, never a URL or persistent storage", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"principal_id":"b"}'));
    vi.stubGlobal("fetch", fetcher);
    const { readIdentity } = await import("./identity");
    await readIdentity("test-token", undefined, "tenant_b");
    expect(fetcher).toHaveBeenCalledWith("/p3/auth/me", expect.objectContaining({
      headers: { Authorization: "Bearer test-token", "X-P3-Tenant": "tenant_b" },
    }));
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it.each([[401, "重新登录"], [403, "停用"], [503, "P3 服务"]])("handles HTTP %i without granting a tenant", async (status, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: Number(status) })));
    const { readIdentity } = await import("./identity");
    await expect(readIdentity("test-token")).rejects.toThrow(String(expected));
  });
  it("excludes authorization callback queries from the next redirect", async () => {
    window.history.replaceState({}, "", "/?code=test-code&state=test-state#private");
    const { loginReturnUrl } = await import("./identity");
    expect(loginReturnUrl()).toBe(window.location.origin + "/");
    window.history.replaceState({}, "", "/");
  });
});
