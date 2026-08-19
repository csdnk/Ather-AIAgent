import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Radio,
  Row,
  Space,
  Tooltip,
  Typography,
  message,
} from "antd";
import ReactECharts from "echarts-for-react";
import { useEffect, useMemo, useState } from "react";
import { generateEmbedding, getB1Status } from "../api/b1";
import type { B1Status, EmbeddingResponse } from "../api/types";
import { MetricCard } from "../components/MetricCard";
import { PipelineStatus } from "../components/PipelineStatus";

const { TextArea } = Input;

function vectorPreview(result: EmbeddingResponse | null, expanded = false) {
  const vector = result?.records?.[0]?.vector ?? [];
  const values = expanded ? vector : vector.slice(0, 32);
  return `[${values.map((value) => Number(value).toFixed(6)).join(", ")}${expanded || vector.length <= 32 ? "" : ", ..."}]`;
}

export function B1Embedding() {
  const [form] = Form.useForm<{ text: string; inputType: "query" | "passage" }>();
  const [status, setStatus] = useState<B1Status | null>(null);
  const [result, setResult] = useState<EmbeddingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const refreshStatus = async () => {
    try {
      setStatus(await getB1Status());
      setStatusError(null);
    } catch (exc) {
      setStatusError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  useEffect(() => {
    void refreshStatus();
  }, []);

  const chartOption = useMemo(() => {
    const metrics = status?.metrics;
    return {
      grid: { left: 32, right: 16, top: 24, bottom: 32 },
      tooltip: {},
      xAxis: { type: "category", data: ["Effective", "Request", "Vector"] },
      yAxis: { type: "value" },
      series: [
        {
          type: "bar",
          data: [
            metrics?.effective_item_qps ?? 0,
            metrics?.http_request_qps ?? metrics?.requests_per_second ?? 0,
            metrics?.vector_qps ?? 0,
          ],
          itemStyle: { color: "#1677ff" },
        },
      ],
    };
  }, [status]);

  async function onGenerate(values: { text: string; inputType: "query" | "passage" }) {
    setLoading(true);
    try {
      const response = await generateEmbedding({
        text: values.text,
        inputType: values.inputType,
      });
      setResult(response);
      await refreshStatus();
      message.success("Embedding generated");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  const dimension = result?.dimension ?? result?.records?.[0]?.vector?.length;
  const metrics = status?.metrics;

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <div>
        <Typography.Title level={3}>B1 Embedding</Typography.Title>
        <Typography.Text type="secondary">
          Generate a real embedding through P3 and the B1 Sidecar.
        </Typography.Text>
      </div>

      {statusError ? <Alert type="warning" showIcon message="B1 status unavailable" description={statusError} /> : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card title="Input">
            <Form
              form={form}
              layout="vertical"
              initialValues={{
                text: "请记住，我们正在演示 AetherBrain P3 的 B1 向量化能力。",
                inputType: "query",
              }}
              onFinish={onGenerate}
            >
              <Form.Item
                name="text"
                label="Text"
                rules={[{ required: true, message: "Text is required" }]}
              >
                <TextArea rows={8} showCount maxLength={4000} />
              </Form.Item>
              <Form.Item name="inputType" label="Usage">
                <Radio.Group
                  options={[
                    { value: "query", label: "Query" },
                    { value: "passage", label: "Passage" },
                  ]}
                />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={loading}>
                Generate Embedding
              </Button>
            </Form>
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card
            title="Embedding Result"
            extra={
              result ? (
                <Space>
                  <Button onClick={() => setExpanded(true)}>Expand</Button>
                  <Button
                    onClick={() => {
                      void navigator.clipboard.writeText(vectorPreview(result, true));
                      message.success("Vector copied");
                    }}
                  >
                    Copy
                  </Button>
                </Space>
              ) : null
            }
          >
            {result ? (
              <Space direction="vertical" size={12} className="full-width">
                <Row gutter={[12, 12]}>
                  <Col span={12}>
                    <MetricCard title="Status" value={result.status} />
                  </Col>
                  <Col span={12}>
                    <MetricCard title="Dimension" value={dimension ?? "N/A"} />
                  </Col>
                  <Col span={12}>
                    <MetricCard title="Backend" value={result.backend ?? status?.health?.backend ?? "N/A"} />
                  </Col>
                  <Col span={12}>
                    <MetricCard title="Latency" value={result.latency_ms?.toFixed(2) ?? "N/A"} suffix="ms" />
                  </Col>
                  <Col span={12}>
                    <MetricCard title="Model" value={result.model ?? status?.health?.model ?? "N/A"} />
                  </Col>
                  <Col span={12}>
                    <MetricCard title="Input Type" value={result.input_type ?? "N/A"} />
                  </Col>
                </Row>
                <Typography.Text code className="vector-preview">
                  {vectorPreview(result)}
                </Typography.Text>
              </Space>
            ) : (
              <Typography.Text type="secondary">No embedding generated yet.</Typography.Text>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}>
          <MetricCard
            title="Effective QPS"
            value={metrics?.effective_item_qps ?? "N/A"}
            tooltip="successful embedding items / second"
          />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard
            title="Request QPS"
            value={metrics?.http_request_qps ?? metrics?.requests_per_second ?? "N/A"}
          />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="P99" value={metrics?.request_latency_p99_ms ?? "N/A"} suffix="ms" />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="Avg Batch" value={metrics?.dynamic_batch?.batch_items_avg ?? "N/A"} />
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="Runtime Throughput">
            {metrics ? (
              <ReactECharts option={chartOption} style={{ height: 240 }} />
            ) : (
              <Typography.Text type="secondary">No runtime benchmark data</Typography.Text>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Current Optimizations">
            <Space wrap>
              {[
                "Cross-request Dynamic Batching",
                "Length-aware Batching",
                "OpenVINO AsyncInferQueue",
                "INT8",
                "CPU SIMD Runtime",
              ].map((item) => (
                <Tooltip title="Reported by current B1 implementation or capability contract" key={item}>
                  <span className="optimization-pill">{item}</span>
                </Tooltip>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>

      <PipelineStatus
        title="B1 Flow"
        nodes={[
          { title: "Validate", status: result ? "finish" : "wait" },
          { title: "Dynamic Batch", status: loading ? "process" : result ? "finish" : "wait" },
          { title: "CPU Embed", status: loading ? "process" : result ? "finish" : "wait" },
          { title: "Normalize", status: result ? "finish" : "wait" },
          { title: "Return Vector", status: result ? "finish" : "wait" },
        ]}
      />

      <Modal open={expanded} footer={null} onCancel={() => setExpanded(false)} width={760}>
        <Typography.Title level={5}>Full Vector</Typography.Title>
        <Typography.Paragraph copyable className="vector-modal">
          {vectorPreview(result, true)}
        </Typography.Paragraph>
      </Modal>
    </Space>
  );
}
