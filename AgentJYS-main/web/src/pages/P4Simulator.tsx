import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  List,
  Row,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  FileAddOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createP4Session,
  getP4Health,
  getP4Task,
  sendP4Message,
  submitP4Document,
} from "../api/p4";
import type { P4Health, P4Session, P4Task, P4TurnResult } from "../api/p4";
import { MetricCard } from "../components/MetricCard";
import { PipelineStatus } from "../components/PipelineStatus";

const { TextArea } = Input;

function taskStatusColor(state?: string) {
  if (state === "SUCCEEDED") return "success";
  if (state === "FAILED") return "error";
  return "processing";
}

export function P4Simulator() {
  const { message } = AntApp.useApp();
  const initialized = useRef(false);
  const [health, setHealth] = useState<P4Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [session, setSession] = useState<P4Session | null>(null);
  const [turn, setTurn] = useState<P4TurnResult | null>(null);
  const [input, setInput] = useState("");
  const [durableMemory, setDurableMemory] = useState(false);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [documentName, setDocumentName] = useState("project-note.txt");
  const [documentText, setDocumentText] = useState("");
  const [task, setTask] = useState<P4Task | null>(null);
  const [submittingDocument, setSubmittingDocument] = useState(false);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getP4Health());
      setHealthError(null);
    } catch (exc) {
      setHealth(null);
      setHealthError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  const newSession = useCallback(async () => {
    setCreating(true);
    try {
      const created = await createP4Session();
      setSession(created);
      setTurn(null);
      setTask(null);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setCreating(false);
    }
  }, [message]);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    void refreshHealth();
    void newSession();
  }, [newSession, refreshHealth]);

  useEffect(() => {
    if (!task?.task_id || ["SUCCEEDED", "FAILED"].includes(task.state.toUpperCase())) return;
    const timer = window.setInterval(() => {
      getP4Task(task.task_id)
        .then(setTask)
        .catch((exc) => message.error(exc instanceof Error ? exc.message : String(exc)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [message, task]);

  async function send() {
    const content = input.trim();
    if (!content || !session) return;
    setSending(true);
    try {
      const result = await sendP4Message(session.session_id, content, durableMemory);
      setSession(result.session);
      setTurn(result);
      setInput("");
      message.success("P4 completed the P3 context and memory flow");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSending(false);
    }
  }

  async function submitDocument() {
    if (!session || !documentText.trim() || !documentName.trim()) return;
    setSubmittingDocument(true);
    try {
      const submitted = await submitP4Document(
        session.session_id,
        documentName.trim(),
        documentText.trim(),
      );
      setTask(submitted);
      message.success("Document submitted to P3 B2");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSubmittingDocument(false);
    }
  }

  const contextMemories = turn?.context.memories ?? [];
  const taskState = task?.state?.toUpperCase();

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <div className="page-heading-row">
        <div>
          <Space wrap>
            <Typography.Title level={3}>P4 Reference Agent</Typography.Title>
            <Tag color="blue">UPSTREAM SIMULATOR</Tag>
          </Space>
          <Typography.Text type="secondary">
            An independent Agent application consuming the frozen P3 HTTP contract.
          </Typography.Text>
        </div>
        <Space>
          <Tooltip title="Refresh P4 and P3 connectivity">
            <Button icon={<ReloadOutlined />} onClick={() => void refreshHealth()} />
          </Tooltip>
          <Button icon={<PlusOutlined />} loading={creating} onClick={() => void newSession()}>
            New Session
          </Button>
        </Space>
      </div>

      {healthError ? (
        <Alert type="error" showIcon message="P4 unavailable" description={healthError} />
      ) : health?.status === "degraded" ? (
        <Alert type="warning" showIcon message="P4 is running but P3 is degraded" description={health.detail} />
      ) : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}>
          <MetricCard title="P4 Application" value={health ? "Running" : "N/A"} />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="P3 Connection" value={health?.p3_reachable ? "Connected" : "N/A"} />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="Agent" value={session?.agent_id ?? "N/A"} />
        </Col>
        <Col xs={24} md={6}>
          <MetricCard title="Context Memories" value={contextMemories.length} />
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card
            title={<Space><MessageOutlined />Agent Conversation</Space>}
            extra={<Typography.Text code>{session?.session_id ?? "Creating session..."}</Typography.Text>}
            className="p4-chat-card"
          >
            <List
              dataSource={session?.messages ?? []}
              locale={{ emptyText: "Start a conversation to exercise P4 -> P3" }}
              renderItem={(item) => (
                <List.Item className={`p4-message ${item.role}`}>
                  <List.Item.Meta
                    title={
                      <Space>
                        <Tag color={item.role === "user" ? "blue" : "green"}>{item.role}</Tag>
                        {item.memory_id ? <Typography.Text copyable>{item.memory_id}</Typography.Text> : null}
                      </Space>
                    }
                    description={<Typography.Paragraph>{item.content}</Typography.Paragraph>}
                  />
                </List.Item>
              )}
            />
            <div className="p4-compose">
              <TextArea
                rows={3}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="Ask the reference Agent..."
                onPressEnter={(event) => {
                  if (!event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
              />
              <div className="p4-compose-actions">
                <Tooltip title="Store this message as cross-session semantic memory">
                  <Space>
                    <Switch checked={durableMemory} onChange={setDurableMemory} />
                    <Typography.Text>Durable memory</Typography.Text>
                  </Space>
                </Tooltip>
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  loading={sending}
                  disabled={!session || !input.trim()}
                  onClick={() => void send()}
                >
                  Send
                </Button>
              </div>
            </div>
          </Card>
        </Col>

        <Col xs={24} xl={9}>
          <Card title="P3 Context Evidence" className="p4-evidence-card">
            {turn ? (
              <Space direction="vertical" size={12} className="full-width">
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="Status">{turn.context.status ?? "N/A"}</Descriptions.Item>
                  <Descriptions.Item label="Complete">
                    {turn.context.complete == null ? "N/A" : String(turn.context.complete)}
                  </Descriptions.Item>
                  <Descriptions.Item label="Trace ID">
                    <Typography.Text copyable>{turn.context.trace_id ?? turn.assistant_message.trace_id}</Typography.Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="P3 Calls">
                    {turn.p3_calls.map((call) => <Tag key={call}>{call}</Tag>)}
                  </Descriptions.Item>
                </Descriptions>
                <List
                  size="small"
                  header={`Recalled memories (${contextMemories.length})`}
                  dataSource={contextMemories}
                  locale={{ emptyText: "P3 returned no matching memory" }}
                  renderItem={(item) => (
                    <List.Item>
                      <List.Item.Meta
                        title={String(item.id ?? "Memory")}
                        description={String(item.content ?? "No content")}
                      />
                    </List.Item>
                  )}
                />
              </Space>
            ) : (
              <Empty description="No P3 context request yet" />
            )}
          </Card>
        </Col>
      </Row>

      <PipelineStatus
        title="P4 Agent Turn"
        nodes={[
          { title: "User Request", status: sending ? "process" : turn ? "finish" : "wait" },
          { title: "P3 Context", status: sending ? "process" : turn ? "finish" : "wait" },
          { title: "Agent Response", status: turn ? "finish" : "wait" },
          { title: "P3 Memory", status: turn?.memory?.id ? "finish" : "wait" },
        ]}
      />

      <Card title={<Space><FileAddOutlined />Document Memory</Space>}>
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={8}>
            <Input
              value={documentName}
              onChange={(event) => setDocumentName(event.target.value)}
              placeholder="source-id.txt"
            />
          </Col>
          <Col xs={24} lg={12}>
            <TextArea
              rows={4}
              value={documentText}
              onChange={(event) => setDocumentText(event.target.value)}
              placeholder="Paste a real text document for the P3 B2 asynchronous pipeline"
            />
          </Col>
          <Col xs={24} lg={4}>
            <Button
              type="primary"
              icon={<FileAddOutlined />}
              block
              loading={submittingDocument}
              disabled={!session || !documentText.trim()}
              onClick={() => void submitDocument()}
            >
              Submit
            </Button>
          </Col>
        </Row>
        {task ? (
          <Descriptions size="small" column={{ xs: 1, md: 3 }} bordered className="p4-task-status">
            <Descriptions.Item label="Task ID">
              <Typography.Text copyable>{task.task_id}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="State">
              <Tag color={taskStatusColor(taskState)}>{taskState}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Chunks">{task.chunk_count ?? "N/A"}</Descriptions.Item>
          </Descriptions>
        ) : null}
      </Card>
    </Space>
  );
}
