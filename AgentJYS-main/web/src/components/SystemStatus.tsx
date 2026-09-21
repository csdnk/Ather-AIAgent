import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  List,
  Space,
  Tag,
  Typography,
} from "antd";
import { useState } from "react";
import type { RuntimeComponentHealth, SystemStatus, SystemStatusResponse } from "../api/types";
import { isMockMode } from "../api/client";
import { deriveSystemStatus } from "../utils/status";

function componentTone(status?: string): "success" | "warning" | "error" | "default" {
  if (status === "HEALTHY") return "success";
  if (status === "DEGRADED" || status === "BUSY" || status === "UNKNOWN") return "warning";
  if (status === "UNAVAILABLE") return "error";
  return "default";
}

function statusClass(status: SystemStatus) {
  if (status === "Healthy") return "status-dot healthy";
  if (status === "Degraded") return "status-dot degraded";
  return "status-dot unavailable";
}

export function SystemStatusView({
  data,
  error,
}: {
  data?: SystemStatusResponse | null;
  error?: Error | null;
}) {
  const [open, setOpen] = useState(false);
  const status = error ? "Unavailable" : deriveSystemStatus(data);
  const components = data?.runtime_health?.components ?? [];
  const degraded = components.filter((item) => item.status !== "HEALTHY");
  const available = components.filter((item) => item.status === "HEALTHY");

  return (
    <>
      <Space size={8}>
        {isMockMode ? <Tag color="orange">MOCK MODE</Tag> : null}
        <Button type="text" className="system-button" onClick={() => setOpen(true)}>
          <span className={statusClass(status)} />
          <span>System</span>
          <Typography.Text strong>{status}</Typography.Text>
        </Button>
      </Space>
      <Drawer
        title="System Status"
        placement="right"
        width={460}
        open={open}
        onClose={() => setOpen(false)}
      >
        {error ? (
          <Alert type="error" showIcon message="P3 API unavailable" description={error.message} />
        ) : null}
        <Descriptions size="small" column={1} bordered className="status-descriptions">
          <Descriptions.Item label="Overall">{status}</Descriptions.Item>
          <Descriptions.Item label="Runtime Profile">
            {data?.runtime_health?.runtime_profile ?? data?.runtime_profile ?? "N/A"}
          </Descriptions.Item>
          <Descriptions.Item label="P2 Endpoint">{data?.p2_endpoint ?? "N/A"}</Descriptions.Item>
          <Descriptions.Item label="Checked At">
            {data?.runtime_health?.checked_at ?? "N/A"}
          </Descriptions.Item>
        </Descriptions>
        <List
          className="status-list"
          header="Components"
          dataSource={components}
          locale={{ emptyText: "No component health returned" }}
          renderItem={(item: RuntimeComponentHealth) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space>
                    <span>{item.component}</span>
                    <Tag color={componentTone(item.status)}>{item.status}</Tag>
                  </Space>
                }
                description={item.detail || "No detail"}
              />
              {item.latency_ms != null ? (
                <Typography.Text type="secondary">{item.latency_ms.toFixed(2)} ms</Typography.Text>
              ) : null}
            </List.Item>
          )}
        />
        {status === "Degraded" ? (
          <div className="status-summary">
            <Typography.Title level={5}>Available</Typography.Title>
            <Space wrap>
              {available.map((item) => (
                <Tag color="green" key={item.component}>
                  {item.component}
                </Tag>
              ))}
            </Space>
            <Typography.Title level={5}>Degraded</Typography.Title>
            <Space wrap>
              {degraded.map((item) => (
                <Tag color={componentTone(item.status)} key={item.component}>
                  {item.component}
                </Tag>
              ))}
            </Space>
          </div>
        ) : null}
      </Drawer>
    </>
  );
}
