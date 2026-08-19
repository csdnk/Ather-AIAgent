import { App as AntApp, ConfigProvider, theme } from "antd";
import { useState } from "react";
import { AppLayout, type PageKey } from "./components/AppLayout";
import { useHealth } from "./hooks/useHealth";
import { B1Embedding } from "./pages/B1Embedding";
import { B2Memory } from "./pages/B2Memory";
import { B3Scheduler } from "./pages/B3Scheduler";
import { Overview } from "./pages/Overview";

function pageContent(page: PageKey, health: ReturnType<typeof useHealth>["data"]) {
  if (page === "b1") return <B1Embedding />;
  if (page === "b2") return <B2Memory health={health} />;
  if (page === "b3") return <B3Scheduler />;
  return <Overview health={health} />;
}

export default function App() {
  const [page, setPage] = useState<PageKey>("overview");
  const { data, error } = useHealth();

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: "#1677ff",
          borderRadius: 6,
          fontFamily:
            "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        },
      }}
    >
      <AntApp>
        <AppLayout page={page} onPageChange={setPage} health={data} healthError={error}>
          {pageContent(page, data)}
        </AppLayout>
      </AntApp>
    </ConfigProvider>
  );
}
