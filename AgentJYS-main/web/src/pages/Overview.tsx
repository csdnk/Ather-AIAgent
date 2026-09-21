import { Alert, Card, Col, Row, Space, Spin, Tag, Tooltip, Typography } from "antd";
import { useEffect, useState } from "react";
import { getB1Status } from "../api/b1";
import type { B1Status, RuntimeComponentHealth, SystemStatusResponse } from "../api/types";
import { MetricCard } from "../components/MetricCard";
import { PipelineStatus } from "../components/PipelineStatus";
import { deriveSystemStatus } from "../utils/status";

function component(
  health: SystemStatusResponse | null | undefined,
  name: string,
): RuntimeComponentHealth | undefined {
  return health?.runtime_health?.components?.find((item) => item.component === name);
}

function statusColor(status?: string) {
  if (status === "HEALTHY" || status === "ready") return "green";
  if (status === "UNAVAILABLE") return "red";
  return "gold";
}

function valueOrNA(value: unknown) {
  return value === undefined || value === null || value === "" ? "N/A" : String(value);
}

export function Overview({ health }: { health?: SystemStatusResponse | null }) {
  const [b1, setB1] = useState<B1Status | null>(null);
  const [b1Error, setB1Error] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getB1Status()
      .then((data) => {
        if (active) {
          setB1(data);
          setB1Error(null);
        }
      })
      .catch((exc) => {
        if (active) setB1Error(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      active = false;
    };
  }, []);

  const b1Health = b1?.health ?? {};
  const b2Redis = component(health, "Redis");
  const b2Milvus = component(health, "Milvus");
  const b2Celery = component(health, "Celery");
  const b3 = component(health, "B3");
  const system = deriveSystemStatus(health);

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <div>
        <Typography.Title level={3}>Overview</Typography.Title>
        <Typography.Text type="secondary">
          P3 runtime view for B1 computation, B2 memory, and B3 scheduling.
        </Typography.Text>
      </div>

      {b1Error ? <Alert type="warning" showIcon message="B1 status unavailable" description={b1Error} /> : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <Card title="B1 Embedding Sidecar" className="status-card">
            {!b1 ? (
              <Spin />
            ) : (
              <Space direction="vertical" size={10} className="full-width">
                <Space>
                  <Typography.Text>Status</Typography.Text>
                  <Tag color={statusColor(b1.status)}>{b1.status}</Tag>
                </Space>
                <MetricCard title="Model" value={valueOrNA(b1Health.model)} />
                <MetricCard title="Backend" value={valueOrNA(b1Health.backend)} />
                <MetricCard
                  title="Effective QPS"
                  value={valueOrNA(b1.metrics?.effective_item_qps)}
                  tooltip="successful embedding items / second"
                />
                <MetricCard
                  title="Dynamic Batch"
                  value={b1Health.dynamic_batch_enabled ? "ON" : "N/A"}
                />
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card title="B2 Agent Memory" className="status-card">
            <Space direction="vertical" size={10} className="full-width">
              <Space>
                <Typography.Text>Redis</Typography.Text>
                <Tag color={statusColor(b2Redis?.status)}>{b2Redis?.status ?? "N/A"}</Tag>
              </Space>
              <Space>
                <Typography.Text>Milvus</Typography.Text>
                <Tag color={statusColor(b2Milvus?.status)}>{b2Milvus?.status ?? "N/A"}</Tag>
              </Space>
              <MetricCard title="Working P99" value="N/A" suffix="ms" />
              <Space>
                <Typography.Text>Celery</Typography.Text>
                <Tag color={statusColor(b2Celery?.status)}>{b2Celery?.status ?? "N/A"}</Tag>
              </Space>
              <MetricCard title="Pending Tasks" value="N/A" />
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card title="B3 Semantic Scheduler" className="status-card">
            <Space direction="vertical" size={10} className="full-width">
              <Space>
                <Typography.Text>Status</Typography.Text>
                <Tag color={statusColor(b3?.status)}>{b3?.status ?? "N/A"}</Tag>
              </Space>
              <MetricCard title="Policy" value="heuristic-v1" />
              <MetricCard title="Mode" value="Heuristic" />
              <MetricCard
                title="Pending Actions"
                value={health?.schedule_history?.length ?? "N/A"}
              />
              <MetricCard title="Prediction" value="Not Deployed" />
            </Space>
          </Card>
        </Col>
      </Row>

      <Card title="System Flow">
        <div className="system-flow">
          <Tooltip title={`System status: ${system}`}>
            <span className="flow-node">Agent</span>
          </Tooltip>
          <span className="flow-arrow">to</span>
          <span className="flow-node">B1 Embedding</span>
          <span className="flow-arrow">to</span>
          <span className="flow-node">B2 Memory</span>
          <span className="flow-arrow">to</span>
          <span className="flow-node">Redis / Milvus</span>
          <span className="flow-arrow">to</span>
          <span className="flow-node">B3 Scheduler</span>
        </div>
      </Card>

      <PipelineStatus
        title="Runtime Pipeline"
        nodes={[
          { title: "B1", description: "Embedding", status: b1?.status === "HEALTHY" ? "finish" : "wait" },
          { title: "B2", description: "Memory", status: b2Redis ? "finish" : "wait" },
          { title: "B3", description: "Scheduler", status: b3 ? "finish" : "wait" },
        ]}
      />
    </Space>
  );
}
