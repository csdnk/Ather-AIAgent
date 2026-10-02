import { useEffect, useState } from "react";
import Console from "./monitoring/Console";
import IdentityActions from "./monitoring/IdentityActions";
import DemoPage from "./demo/DemoPage";
import "./workspace.css";

type View = "demo" | "monitor";
function initialView(): View {
  const params = new URLSearchParams(window.location.search);
  // Leave the identity callback intact until the Keycloak SDK consumes it.
  if (params.get("view") === "demo") return "demo";
  const identityCallback = params.has("state") && (params.has("code") || params.has("error"));
  return identityCallback || params.get("view") === "monitor" ? "monitor" : "demo";
}

export default function App() {
  const [view, setView] = useState<View>(initialView);
  const [visited, setVisited] = useState(() => ({ demo: initialView() === "demo", monitor: initialView() === "monitor" }));

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", initialView());
    window.history.replaceState(window.history.state, "", url);
    const onPopState = () => {
      const next: View = new URLSearchParams(window.location.search).get("view") === "monitor" ? "monitor" : "demo";
      setVisited(current => ({ ...current, [next]: true }));
      setView(next);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function selectView(next: View) {
    if (next === view) return;
    const url = new URL(window.location.href);
    url.searchParams.set("view", next);
    window.history.pushState(window.history.state, "", url);
    setVisited(current => ({ ...current, [next]: true }));
    setView(next);
    window.scrollTo(0, 0);
  }

  return <>
    <header className="workspace-header">
      <div className="workspace-brand">
        <span className="workspace-mark" aria-hidden="true">a</span>
        <span>Aether<small>{view === "demo" ? "记忆演示空间" : "P3 运行监测"}</small></span>
      </div>
      <span className="workspace-context"><i />{view === "demo" ? "Native · 固定场景验证" : "P3 · 租户与运行诊断"}</span>
      <nav aria-label="工作空间" className="workspace-nav">
        <button aria-pressed={view === "demo"} onClick={() => selectView("demo")}>
          {view === "monitor" && <span aria-hidden="true">← </span>}{view === "monitor" ? "返回五场景演示" : "五场景演示"}
        </button>
        <button aria-pressed={view === "monitor"} onClick={() => selectView("monitor")}>监测控制台</button>
      </nav>
      <IdentityActions />
    </header>
    {/* Keep scenario progress, the selected organization and the session in memory. */}
    {visited.demo && <div hidden={view !== "demo"}><DemoPage /></div>}
    {visited.monitor && <div hidden={view !== "monitor"}><Console /></div>}
  </>;
}
