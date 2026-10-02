import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import IdentityActions from "./IdentityActions";

const identity = vi.hoisted(() => ({ initialize: vi.fn(), register: vi.fn(), login: vi.fn(), account: vi.fn(), authenticated: false, listeners: new Set<() => void>() }));
vi.mock("./identity", () => ({ browserIdentity: identity.initialize,
  getAuthenticationState: () => identity.authenticated,
  subscribeAuthentication: (listener: () => void) => { identity.listeners.add(listener); return () => { identity.listeners.delete(listener); }; },
  loginReturnUrl: () => "http://localhost:3000/" }));
beforeEach(() => {
  vi.clearAllMocks(); identity.authenticated = false; identity.listeners.clear();
  identity.register.mockResolvedValue(undefined); identity.login.mockResolvedValue(undefined); identity.account.mockResolvedValue(undefined);
  identity.initialize.mockResolvedValue({ authServerUrl: "http://127.0.0.1:18080/", realm: "p3-demo", register: identity.register, login: identity.login, accountManagement: identity.account });
});
afterEach(cleanup);

describe("native account and organization entry", () => {
  it("shows all three main actions without opening a connection dialog", async () => {
    render(<IdentityActions />);
    expect(screen.getByRole("navigation", { name: "账号与组织" })).toBeVisible();
    await waitFor(() => expect(screen.getByRole("button", { name: "注册账号" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "登录 / 切换账号" })).toBeVisible();
    expect(screen.getByRole("button", { name: "邀请成员" })).toBeVisible();
  });
  it("hides registration and shows switch-account after restoring a login", async () => {
    identity.authenticated = true;
    render(<IdentityActions />);
    await waitFor(() => expect(screen.getByRole("button", { name: "切换账号" })).toBeEnabled());
    expect(screen.queryByRole("button", { name: "注册账号" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "切换账号" }));
    await waitFor(() => expect(identity.login).toHaveBeenCalledWith({ redirectUri: "http://localhost:3000/", prompt: "login" }));
  });
  it("updates both buttons when the account logs out without remounting", async () => {
    identity.authenticated = true;
    render(<IdentityActions />);
    await waitFor(() => expect(screen.getByRole("button", { name: "切换账号" })).toBeEnabled());
    act(() => { identity.authenticated = false; identity.listeners.forEach(listener => listener()); });
    expect(screen.getByRole("button", { name: "注册账号" })).toBeVisible();
    expect(screen.getByRole("button", { name: "登录 / 切换账号" })).toBeVisible();
  });
  it("does not flash registration while restoring the session", () => {
    identity.initialize.mockReturnValue(new Promise(() => {}));
    render(<IdentityActions />);
    expect(screen.queryByRole("button", { name: "注册账号" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登录 / 切换账号" })).toBeDisabled();
  });
  it("uses the native registration flow and the application's fixed callback", async () => {
    render(<IdentityActions />);
    await waitFor(() => expect(screen.getByRole("button", { name: "注册账号" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "注册账号" }));
    await waitFor(() => expect(identity.register).toHaveBeenCalledWith({ redirectUri: "http://localhost:3000/" }));
    expect(identity.login).not.toHaveBeenCalled();
  });
  it("asks for login explicitly so another person's existing SSO is not silently reused", async () => {
    render(<IdentityActions />);
    await waitFor(() => expect(screen.getByRole("button", { name: "登录 / 切换账号" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "登录 / 切换账号" }));
    await waitFor(() => expect(identity.login).toHaveBeenCalledWith({ redirectUri: "http://localhost:3000/", prompt: "login" }));
  });
  it("opens the native account console", async () => {
    render(<IdentityActions />);
    await waitFor(() => expect(screen.getByRole("button", { name: "个人账号" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "个人账号" }));
    await waitFor(() => expect(identity.account).toHaveBeenCalledOnce());
  });
  it("explains the delegation boundary and links to native organization management", async () => {
    render(<IdentityActions />);
    await waitFor(() => expect(screen.getByRole("button", { name: "注册账号" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "邀请成员" }));
    expect(screen.getByText("当前由平台管理员发起邀请")).toBeVisible();
    const link = screen.getByRole("link", { name: /打开组织后台/ });
    expect(link).toHaveAttribute("href", "http://127.0.0.1:18080/admin/master/console/#/p3-demo/organizations");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(identity.login).not.toHaveBeenCalled();
  });
  it("keeps unavailable account actions disabled and does not invent a management URL", async () => {
    identity.initialize.mockResolvedValue(null);
    render(<IdentityActions />);
    expect(await screen.findByRole("alert")).toHaveTextContent("未开启账号服务");
    expect(screen.getByRole("button", { name: "注册账号" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "邀请成员" }));
    expect(screen.queryByRole("link", { name: /打开组织后台/ })).not.toBeInTheDocument();
  });
  it("reports navigation failures without keeping actions stuck", async () => {
    identity.register.mockRejectedValue(new Error("offline"));
    render(<IdentityActions />);
    await waitFor(() => expect(screen.getByRole("button", { name: "注册账号" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "注册账号" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法打开账号页面");
    expect(screen.getByRole("button", { name: "注册账号" })).toBeEnabled();
  });
});
