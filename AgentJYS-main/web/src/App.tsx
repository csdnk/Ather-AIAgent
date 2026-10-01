import { useState } from "react";
import Console from "./monitoring/Console";
import DemoPage from "./demo/DemoPage";

export default function App() {
  const [view, setView] = useState<"demo" | "monitor">("demo");
  return <>
    <nav aria-label="工作空间" className={`demo-app-nav${view === "monitor" ? " demo-app-nav-monitor" : ""}`}>
      <button aria-pressed={view === "demo"} onClick={() => setView("demo")}>场景演示</button>
      <button aria-pressed={view === "monitor"} onClick={() => setView("monitor")}>监测控制台</button>
    </nav>
    {view === "demo" ? <DemoPage /> : <Console />}
  </>;
}
