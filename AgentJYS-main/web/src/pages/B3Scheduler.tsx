import { Button, Card, Col, Form, Input, Row, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { getB3Candidates, getScheduleHistory, runSchedule } from "../api/b3";
import type {
  B3CandidateResponse,
  ExecutionFeedback,
  ScheduleAction,
  ScheduleHistoryItem,
  ScheduleRunResult,
  SchedulableObject,
} from "../api/types";
import { ActionDrawer } from "../components/ActionDrawer";
import { MetricCard } from "../components/MetricCard";
import { PipelineStatus } from "../components/PipelineStatus";

const scope = {
  sessionId: "demo-session",
  agentId: "demo-agent",
  userId: "demo-user",
  tenantId: "demo-tenant",
};

interface ActionRow {
  key: string;
  memory: string;
  frequency?: number;
  semantic?: number;
  recency?: number;
  cost?: number;
  heat?: number;
  currentTier?: string;
  targetTier?: string | null;
  actionType: string;
  status?: string;
  action?: ScheduleAction;
  feedback?: ExecutionFeedback;
}

function rowFromAction(action: ScheduleAction, feedback?: ExecutionFeedback): ActionRow {
  return {
    key: action.action_id,
    memory: action.metadata?.memory_id ?? action.object_id,
    frequency: action.score_frequency,
    semantic: action.score_semantic,
    recency: action.score_decay,
    cost: action.score_cost,
    heat: action.score,
    currentTier: action.source_tier,
    targetTier: action.target_tier,
    actionType: action.action_type,
    status: feedback?.execute_status ?? "not executed",
    action,
    feedback,
  };
}

function rowFromHistory(item: ScheduleHistoryItem): ActionRow {
  return {
    key: item.action_id,
    memory: item.object_id,
    frequency: item.score_frequency,
    semantic: item.score_semantic,
    recency: item.score_decay,
    cost: item.score_cost,
    heat: item.score,
    currentTier: item.source_tier,
    targetTier: item.target_tier,
    actionType: item.action_type,
    status: item.execute_status,
  };
}

function formatScore(value?: number) {
  return value == null ? "N/A" : value.toFixed(3);
}

export function B3Scheduler() {
  const [history, setHistory] = useState<ScheduleHistoryItem[]>([]);
  const [candidates, setCandidates] = useState<B3CandidateResponse | null>(null);
  const [schedule, setSchedule] = useState<ScheduleRunResult | null>(null);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [loadingRun, setLoadingRun] = useState(false);
  const [query, setQuery] = useState("当前项目的 Web 展示前端和 P3 记忆能力");
  const [selectedAction, setSelectedAction] = useState<ScheduleAction | null>(null);
  const [selectedFeedback, setSelectedFeedback] = useState<ExecutionFeedback | null>(null);

  async function loadHistory() {
    try {
      const response = await getScheduleHistory();
      setHistory(response.items ?? []);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    }
  }

  useEffect(() => {
    void loadHistory();
  }, []);

  async function loadCandidates() {
    setLoadingCandidates(true);
    try {
      const response = await getB3Candidates(scope, query);
      setCandidates(response);
      message.success(`Loaded ${response.objects.length} candidates`);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoadingCandidates(false);
    }
  }

  async function evaluate(objects?: SchedulableObject[]) {
    const sourceObjects = objects ?? candidates?.objects ?? [];
    if (!sourceObjects.length || !candidates?.resource_state) {
      message.warning("No scheduler candidates yet");
      return;
    }
    setLoadingRun(true);
    try {
      const result = await runSchedule(sourceObjects, candidates.resource_state);
      setSchedule(result);
      await loadHistory();
      message.success(`B3 returned ${result.actions.length} actions`);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoadingRun(false);
    }
  }

  const rows = useMemo<ActionRow[]>(() => {
    if (schedule?.entries?.length) {
      return schedule.entries.map((entry) => rowFromAction(entry.action, entry.feedback));
    }
    if (schedule?.actions?.length) return schedule.actions.map((action) => rowFromAction(action));
    return history.map(rowFromHistory);
  }, [history, schedule]);

  const columns: ColumnsType<ActionRow> = [
    { title: "Memory", dataIndex: "memory", ellipsis: true },
    { title: "Frequency", dataIndex: "frequency", render: formatScore },
    { title: "Semantic", dataIndex: "semantic", render: formatScore },
    { title: "Recency", dataIndex: "recency", render: formatScore },
    { title: "Cost", dataIndex: "cost", render: formatScore },
    { title: "Heat Score", dataIndex: "heat", render: formatScore },
    { title: "Current Tier", dataIndex: "currentTier" },
    {
      title: "Action",
      dataIndex: "actionType",
      render: (value: string) => <Tag color={value === "keep" ? "default" : "blue"}>{value}</Tag>,
    },
    { title: "Status", dataIndex: "status", render: (value?: string) => value ?? "-" },
  ];

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <div>
        <Typography.Title level={3}>B3 Scheduler</Typography.Title>
        <Typography.Text type="secondary">
          Semantic value, access frequency, recency, and migration cost become heat and action.
        </Typography.Text>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}>
          <MetricCard title="Policy" value="heuristic-v1" />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="Mode" value="Heuristic" />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="Status" value="Running" />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="Prediction Model" value="Not Deployed" />
        </Col>
      </Row>

      <Card title="Context Candidates">
        <Form layout="inline" className="b3-toolbar">
          <Form.Item className="b3-query">
            <Input value={query} onChange={(event) => setQuery(event.target.value)} />
          </Form.Item>
          <Form.Item>
            <Button loading={loadingCandidates} onClick={() => void loadCandidates()}>
              Load Candidates
            </Button>
          </Form.Item>
          <Form.Item>
            <Button type="primary" loading={loadingRun} onClick={() => void evaluate()}>
              Run Scheduler
            </Button>
          </Form.Item>
        </Form>
        <Space wrap className="candidate-summary">
          <Tag>Source {candidates?.source ?? "N/A"}</Tag>
          <Tag>Candidates {candidates?.objects.length ?? 0}</Tag>
          <Tag>Cost {candidates?.resource_state?.migration_cost_score ?? "N/A"}</Tag>
        </Space>
      </Card>

      <Card title="Scheduler Actions">
        <Table
          rowKey="key"
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 8 }}
          locale={{ emptyText: "No scheduler actions yet" }}
          onRow={(record) => ({
            onClick: () => {
              if (record.action) {
                setSelectedAction(record.action);
                setSelectedFeedback(record.feedback ?? null);
              }
            },
          })}
        />
      </Card>

      <PipelineStatus
        title="B3 Evaluation Flow"
        nodes={[
          { title: "Context", status: candidates ? "finish" : "wait" },
          { title: "Candidates", status: candidates ? "finish" : "wait" },
          { title: "Heuristic", status: loadingRun ? "process" : schedule ? "finish" : "wait" },
          { title: "Action", status: schedule ? "finish" : "wait" },
        ]}
      />

      <ActionDrawer
        action={selectedAction}
        feedback={selectedFeedback}
        open={selectedAction !== null}
        onClose={() => {
          setSelectedAction(null);
          setSelectedFeedback(null);
        }}
      />
    </Space>
  );
}
