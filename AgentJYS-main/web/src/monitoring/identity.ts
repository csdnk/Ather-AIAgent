import Keycloak from "keycloak-js";

export type Identity = {
  principal_id: string;
  scope: { tenant_id: string; application_id: string; user_id: string; agent_id: string };
  permissions: string[];
  identity_source?: string;
  username?: string;
  management_url?: string;
  organization_name?: string;
  roles?: string[];
  organizations?: { tenant_id: string; organization_id: string; organization_name: string; roles: string[] }[];
};

// One initialization per page, including React StrictMode's double effect.
let initialization: Promise<Keycloak | null> | undefined;
let authenticated = false;
const authenticationListeners = new Set<() => void>();
export const getAuthenticationState = () => authenticated;
export function subscribeAuthentication(listener: () => void) {
  authenticationListeners.add(listener);
  return () => { authenticationListeners.delete(listener); };
}
function publishAuthentication(client: Keycloak) {
  const next = !!client.authenticated;
  if (next === authenticated) return;
  authenticated = next;
  authenticationListeners.forEach(listener => listener());
}
export function browserIdentity(): Promise<Keycloak | null> {
  initialization ??= (async () => {
    const response = await fetch("/p3/auth/config", { cache: "no-store", redirect: "error" });
    if (!response.ok) throw new Error("无法读取登录配置，请检查 P3 服务。");
    const config = await response.json();
    if (!config.enabled) return null;
    const client = new Keycloak({ url: config.url, realm: config.realm, clientId: config.client_id });
    client.onAuthSuccess = () => publishAuthentication(client);
    client.onAuthRefreshSuccess = () => publishAuthentication(client);
    client.onAuthLogout = () => publishAuthentication(client);
    // Recover the server's SSO session after reload; tokens remain in memory.
    await client.init({
      onLoad: "check-sso", pkceMethod: "S256", checkLoginIframe: false, responseMode: "query",
      silentCheckSsoRedirectUri: window.location.origin + "/silent-check-sso.html",
      redirectUri: loginReturnUrl(),
    });
    publishAuthentication(client);
    return client;
  })();
  return initialization;
}

export async function readIdentity(token: string, signal?: AbortSignal, tenant?: string): Promise<Identity> {
  const response = await fetch("/p3/auth/me", {
    headers: { Authorization: "Bearer " + token, ...(tenant ? { "X-P3-Tenant": tenant } : {}) }, cache: "no-store", redirect: "error", signal,
  });
  if (!response.ok) {
    throw new Error(response.status === 403
      ? "该身份或租户已停用，或没有访问权限。"
      : response.status === 401 ? "尚未获得 P3 访问资格，或登录已过期。新注册 / 受邀用户请联系管理员加入组织分组，再重新登录。" : "无法确认身份，请检查 P3 服务。");
  }
  return response.json();
}

export function loginReturnUrl() {
  const url = new URL(window.location.pathname, window.location.origin);
  const view = new URLSearchParams(window.location.search).get("view");
  if (view === "demo" || view === "monitor") url.searchParams.set("view", view);
  return url.toString();
}
