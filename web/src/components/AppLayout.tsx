import { Layout, Menu, Typography } from "antd";
import type { ReactNode } from "react";
import type { SystemStatusResponse } from "../api/types";
import { SystemStatusView } from "./SystemStatus";

const { Header, Sider, Content } = Layout;

export type PageKey = "overview" | "b1" | "b2" | "b3" | "p4";

const menuItems = [
  { key: "overview", label: "Overview" },
  { key: "b1", label: "B1 Embedding" },
  { key: "b2", label: "B2 Memory" },
  { key: "b3", label: "B3 Scheduler" },
  { type: "divider" as const },
  { key: "p4", label: "P4 Reference Agent" },
];

export function AppLayout({
  page,
  onPageChange,
  health,
  healthError,
  children,
}: {
  page: PageKey;
  onPageChange: (page: PageKey) => void;
  health?: SystemStatusResponse | null;
  healthError?: Error | null;
  children: ReactNode;
}) {
  return (
    <Layout className="app-shell">
      <Header className="app-header">
        <Typography.Title level={4} className="app-title">
          AetherBrain
        </Typography.Title>
        <SystemStatusView data={health} error={healthError} />
      </Header>
      <Layout>
        <Sider width={220} className="app-sider">
          <Menu
            mode="inline"
            theme="dark"
            selectedKeys={[page]}
            items={menuItems}
            onClick={({ key }) => onPageChange(key as PageKey)}
          />
        </Sider>
        <Content className="app-content">{children}</Content>
      </Layout>
    </Layout>
  );
}
