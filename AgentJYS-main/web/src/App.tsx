import { App as AntApp, ConfigProvider, theme } from "antd";
import { useState } from "react";
import { AppLayout, type PageKey } from "./components/AppLayout";
import { useHealth } from "./hooks/useHealth";
import { B1Embedding } from "./pages/B1Embedding";
import { B2Memory } from "./pages/B2Memory";
import { B3Scheduler } from "./pages/B3Scheduler";
import { Overview } from "./pages/Overview";
import { P4Simulator } from "./pages/P4Simulator";

function pageContent(page: PageKey, health: ReturnType<typeof useHealth>["data"]) {
  if (page === "b1") return <B1Embedding />;
  if (page === "b2") return <B2Memory health={health} />;
  if (page === "b3") return <B3Scheduler />;
  if (page === "p4") return <P4Simulator />;
  return <Overview health={health} />;
}

export default function App() {
  const [page, setPage] = useState<PageKey>("overview");
  const { data, error } = useHealth();

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: "#53c5ae",
          colorInfo: "#78b5ed",
          colorSuccess: "#53c5ae",
          colorWarning: "#efbd62",
          colorError: "#ec7c72",
          colorBgBase: "#111819",
          colorBgContainer: "#1b2526",
          colorBgElevated: "#1b2526",
          colorBgLayout: "#111819",
          colorBorder: "#36484a",
          colorBorderSecondary: "#36484a",
          colorText: "#e8efed",
          colorTextSecondary: "#9aaead",
          colorTextTertiary: "#9aaead",
          borderRadius: 8,
          fontFamily:
            "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          Layout: {
            headerBg: "#182123",
            siderBg: "#182123",
            bodyBg: "#111819",
          },
          Menu: {
            darkItemBg: "transparent",
            darkSubMenuItemBg: "#182123",
            darkItemColor: "#9aaead",
            darkItemHoverColor: "#e8efed",
            darkItemHoverBg: "rgba(83, 197, 174, 0.08)",
            darkItemSelectedBg: "rgba(83, 197, 174, 0.18)",
            darkItemSelectedColor: "#53c5ae",
          },
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
