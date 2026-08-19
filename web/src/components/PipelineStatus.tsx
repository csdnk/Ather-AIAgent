import { Card, Space, Steps, Tag } from "antd";

export type PipelineNodeStatus = "wait" | "process" | "finish" | "error";

export interface PipelineNode {
  title: string;
  description?: string;
  status?: PipelineNodeStatus;
}

export function PipelineStatus({
  title = "Pipeline",
  nodes,
}: {
  title?: string;
  nodes: PipelineNode[];
}) {
  return (
    <Card title={title} size="small">
      <Steps
        responsive
        size="small"
        items={nodes.map((node) => ({
          title: node.title,
          description: node.description,
          status: node.status ?? "wait",
        }))}
      />
    </Card>
  );
}

export function StatusTag({ status }: { status?: string }) {
  const normalized = (status ?? "UNKNOWN").toUpperCase();
  let color = "default";
  if (["HEALTHY", "SUCCESS", "SUCCEEDED", "READY", "RUNNING"].includes(normalized)) color = "green";
  if (["DEGRADED", "PROCESSING", "PENDING", "BUSY", "UNKNOWN"].includes(normalized)) {
    color = "gold";
  }
  if (["UNAVAILABLE", "FAILED", "ERROR"].includes(normalized)) color = "red";

  return (
    <Space size={4}>
      <span className={`status-dot ${color === "green" ? "healthy" : color === "red" ? "unavailable" : "degraded"}`} />
      <Tag color={color}>{status ?? "N/A"}</Tag>
    </Space>
  );
}
