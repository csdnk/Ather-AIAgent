import {
  Alert,
  Button,
  Card,
  Col,
  Input,
  List,
  Row,
  Space,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import type { UploadProps } from "antd";
import { SendOutlined, UploadOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useState } from "react";
import { buildContext, getB2Task, submitLongText, writeMemory } from "../api/b2";
import type { ContextResponse, MemoryRecord, SystemStatusResponse, TaskStatus } from "../api/types";
import { MemoryDrawer } from "../components/MemoryDrawer";
import { MetricCard } from "../components/MetricCard";
import { PipelineStatus, StatusTag } from "../components/PipelineStatus";

const { TextArea } = Input;

const scope = {
  sessionId: "demo-session",
  agentId: "demo-agent",
  userId: "demo-user",
  tenantId: "demo-tenant",
};

interface ChatMessage {
  role: "User" | "Agent";
  content: string;
}

function componentStatus(health: SystemStatusResponse | null | undefined, name: string) {
  return health?.runtime_health?.components?.find((item) => item.component === name)?.status;
}

function groupMemories(context: ContextResponse | null) {
  const memories = context?.memories ?? [];
  return {
    working: memories.filter((item) => item.type === "working"),
    episodic: memories.filter((item) => item.type === "episodic"),
    semantic: memories.filter((item) => item.type === "semantic"),
  };
}

export function B2Memory({ health }: { health?: SystemStatusResponse | null }) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [task, setTask] = useState<TaskStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<MemoryRecord | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const grouped = useMemo(() => groupMemories(context), [context]);

  useEffect(() => {
    if (!task?.task_id || ["SUCCEEDED", "FAILED"].includes(String(task.state))) return;
    const timer = window.setInterval(() => {
      getB2Task(task.task_id)
        .then(setTask)
        .catch((exc) => message.error(exc instanceof Error ? exc.message : String(exc)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [task]);

  async function send() {
    const content = input.trim();
    if (!content) return;
    setLoading(true);
    setMessages((prev) => [...prev, { role: "User", content }]);
    try {
      const memory = await writeMemory(scope, content);
      const nextContext = await buildContext(scope, content);
      setContext(nextContext);
      setMessages((prev) => [
        ...prev,
        {
          role: "Agent",
          content: `Memory written: ${memory.id}. Context returned ${nextContext.memories.length} memories.`,
        },
      ]);
      setInput("");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  const uploadProps: UploadProps = {
    showUploadList: false,
    beforeUpload: async (file) => {
      const name = file.name.toLowerCase();
      if (!(name.endsWith(".txt") || name.endsWith(".md"))) {
        message.warning("Backend parser not available for this file type");
        return Upload.LIST_IGNORE;
      }
      try {
        const text = await file.text();
        const submitted = await submitLongText(scope, text, file.name);
        setTask(submitted);
        message.success("Long-text task submitted");
      } catch (exc) {
        message.error(exc instanceof Error ? exc.message : String(exc));
      }
      return Upload.LIST_IGNORE;
    },
  };

  function openMemory(memory: MemoryRecord) {
    setSelected(memory);
    setDrawerOpen(true);
  }

  const selectedScore = selected ? context?.recall_scores?.[selected.id] : undefined;
  const taskState = task?.state ? String(task.state).toUpperCase() : undefined;

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <div>
        <Typography.Title level={3}>B2 Memory</Typography.Title>
        <Typography.Text type="secondary">
          Session: {scope.sessionId} | Agent: {scope.agentId}
        </Typography.Text>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}>
          <MetricCard title="Working P99" value="N/A" suffix="ms" />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="Working Memory Count" value={grouped.working.length} />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard
            title="Long-term Memory Count"
            value={grouped.episodic.length + grouped.semantic.length}
          />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="Celery Pending" value="N/A" />
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card title="Conversation" className="conversation-card">
            <List
              dataSource={messages}
              locale={{ emptyText: "No messages yet" }}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta
                    title={<Tag color={item.role === "User" ? "blue" : "green"}>{item.role}</Tag>}
                    description={item.content}
                  />
                </List.Item>
              )}
            />
            <div className="chat-input-row">
              <Upload {...uploadProps}>
                <Button icon={<UploadOutlined />}>Upload File</Button>
              </Upload>
              <TextArea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                rows={2}
                placeholder="Ask something or write a memory..."
                onPressEnter={(event) => {
                  if (!event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
              />
              <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={() => void send()}>
                Send
              </Button>
            </div>
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card title="Current Memory">
            <Space direction="vertical" size={14} className="full-width">
              {(["working", "episodic", "semantic"] as const).map((type) => (
                <div key={type}>
                  <Typography.Title level={5} className="memory-group-title">
                    {type[0].toUpperCase()}
                    {type.slice(1)}
                  </Typography.Title>
                  <List
                    size="small"
                    dataSource={grouped[type]}
                    locale={{ emptyText: "No memories yet" }}
                    renderItem={(memory) => (
                      <List.Item className="memory-row" onClick={() => openMemory(memory)}>
                        <List.Item.Meta
                          title={<Typography.Text ellipsis>{memory.content}</Typography.Text>}
                          description={
                            <Space wrap>
                              <Tag>Score {(context?.recall_scores?.[memory.id] ?? 0).toFixed(3)}</Tag>
                              <Tag>{memory.source ?? "memory"}</Tag>
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                </div>
              ))}
              <Space wrap>
                <StatusTag status={`Redis ${componentStatus(health, "Redis") ?? "N/A"}`} />
                <StatusTag status={`Milvus ${componentStatus(health, "Milvus") ?? "N/A"}`} />
                <StatusTag status={`Celery ${componentStatus(health, "Celery") ?? "N/A"}`} />
              </Space>
            </Space>
          </Card>
        </Col>
      </Row>

      {task ? (
        <Card title={task.task_id}>
          <Space direction="vertical" size={12} className="full-width">
            <Space wrap>
              <StatusTag status={task.state} />
              <Tag>Memory {task.memory_id ?? "N/A"}</Tag>
              <Tag>Vectors {task.p2_vector_count ?? "N/A"}</Tag>
              <Tag>Compression {task.compression_status ?? "N/A"}</Tag>
            </Space>
            {task.error ? <Alert type="error" showIcon message={task.error} /> : null}
            <PipelineStatus
              title="B2 Long-text Pipeline"
              nodes={[
                { title: "Received", status: "finish" },
                {
                  title: "Celery",
                  status: taskState === "FAILED" ? "error" : taskState === "SUCCEEDED" ? "finish" : "process",
                },
                {
                  title: "B1 Embedding",
                  status: taskState === "SUCCEEDED" ? "finish" : taskState === "FAILED" ? "error" : "wait",
                },
                {
                  title: "Projection",
                  status: taskState === "SUCCEEDED" ? "finish" : taskState === "FAILED" ? "error" : "wait",
                },
              ]}
            />
          </Space>
        </Card>
      ) : null}

      <PipelineStatus
        title="B2 Memory Flow"
        nodes={[
          { title: "MemoryEvent", status: context ? "finish" : "wait" },
          { title: "Memory Store", status: context ? "finish" : "wait" },
          { title: "Context", status: context ? "finish" : "wait" },
          { title: "MemorySignal", status: context ? "finish" : "wait" },
          { title: "B3", status: context ? "finish" : "wait" },
        ]}
      />

      <MemoryDrawer
        memory={selected}
        score={selectedScore}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      />
    </Space>
  );
}
