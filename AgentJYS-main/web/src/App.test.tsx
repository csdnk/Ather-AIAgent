import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import App from "./App";

vi.mock("./monitoring/IdentityActions", () => ({ default: () => <nav aria-label="账号与组织">注册账号 / 登录 / 邀请成员</nav> }));

vi.mock("./demo/DemoPage", () => ({ default: function MockDemo() {
  const [scenario, setScenario] = useState("S01");
  return <><p>演示页面</p><button onClick={() => setScenario("S02")}>当前场景：{scenario}</button></>;
} }));
vi.mock("./monitoring/Console", () => ({ default: function MockConsole() {
  const [organization, setOrganization] = useState("A");
  return <button onClick={() => setOrganization("B")}>当前组织：{organization}</button>;
} }));

beforeEach(() => { window.history.replaceState(null, "", "/"); vi.spyOn(window, "scrollTo").mockImplementation(() => {}); vi.mocked(window.getComputedStyle).mockImplementation(() => ({ getPropertyValue: () => "" }) as unknown as CSSStyleDeclaration); });
afterEach(() => { cleanup(); window.history.replaceState(null, "", "/"); vi.restoreAllMocks(); });

describe("workspace navigation with identity callbacks", () => {
  it("opens the demo by default without initializing the monitor", () => {
    render(<App />);
    expect(screen.getByText("演示页面")).toBeVisible();
    expect(screen.queryByText("当前组织：A")).not.toBeInTheDocument();
  });
  it("supports a direct monitor entry and preserves unrelated query parameters", () => {
    window.history.replaceState(null, "", "/?view=monitor&test=value");
    render(<App />);
    expect(screen.getByText("当前组织：A")).toBeVisible();
    expect(new URLSearchParams(window.location.search).get("test")).toBe("value");
  });
  it.each(["code=authorization-code", "error=access_denied"])("opens the monitor for an OAuth callback: %s", (callback) => {
    window.history.replaceState(null, "", "/?state=callback-state&" + callback);
    render(<App />);
    expect(screen.getByText("当前组织：A")).toBeVisible();
    const params = new URLSearchParams(window.location.search);
    expect(params.get("state")).toBe("callback-state");
    expect(params.get(callback.split("=")[0])).toBe(callback.split("=")[1]);
    expect(params.get("view")).toBe("monitor");
  });
  it("keeps the demo open when SSO falls back to a top-level callback", () => {
    window.history.replaceState(null, "", "/?view=demo&state=callback-state&code=test-code");
    render(<App />);
    expect(screen.getByText("演示页面")).toBeVisible();
    expect(new URLSearchParams(window.location.search).get("code")).toBe("test-code");
    expect(new URLSearchParams(window.location.search).get("view")).toBe("demo");
  });
  it("preserves the selected organization while visiting the demo", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "监测控制台" }));
    fireEvent.click(screen.getByRole("button", { name: "当前组织：A" }));
    fireEvent.click(screen.getByRole("button", { name: "返回五场景演示" }));
    expect(screen.getByText("当前组织：B")).not.toBeVisible();
    expect(new URLSearchParams(window.location.search).get("view")).toBe("demo");
    fireEvent.click(screen.getByRole("button", { name: "监测控制台" }));
    expect(screen.getByRole("button", { name: "当前组织：B" })).toBeVisible();
    expect(new URLSearchParams(window.location.search).get("view")).toBe("monitor");
  });
  it("returns to the selected demo scenario without resetting it", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "当前场景：S01" }));
    fireEvent.click(screen.getByRole("button", { name: "监测控制台" }));
    fireEvent.click(screen.getByRole("button", { name: "返回五场景演示" }));
    expect(screen.getByRole("button", { name: "当前场景：S02" })).toBeVisible();
  });
  it("records a history entry only when changing workspaces", () => {
    render(<App />);
    const push = vi.spyOn(window.history, "pushState");
    fireEvent.click(screen.getByRole("button", { name: "五场景演示" }));
    expect(push).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "监测控制台" }));
    expect(push).toHaveBeenCalledTimes(1);
    expect(new URLSearchParams(window.location.search).get("view")).toBe("monitor");
  });
  it("follows browser history and retains the organization on return", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "监测控制台" }));
    fireEvent.click(screen.getByRole("button", { name: "当前组织：A" }));
    window.history.replaceState(null, "", "/?view=demo");
    fireEvent.popState(window);
    expect(screen.getByText("演示页面")).toBeVisible();
    expect(screen.getByText("当前组织：B")).not.toBeVisible();
    window.history.replaceState(null, "", "/?view=monitor");
    fireEvent.popState(window);
    expect(screen.getByRole("button", { name: "当前组织：B" })).toBeVisible();
    expect(screen.getByText("演示页面")).not.toBeVisible();
  });
});
