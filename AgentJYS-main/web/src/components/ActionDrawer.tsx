import { Descriptions, Drawer, Typography } from "antd";
import type { ExecutionFeedback, ScheduleAction } from "../api/types";

interface ActionDrawerProps {
  action: ScheduleAction | null;
  feedback?: ExecutionFeedback | null;
  open: boolean;
  onClose: () => void;
}

export function ActionDrawer({ action, feedback, open, onClose }: ActionDrawerProps) {
  return (
    <Drawer title="B3 Action" width={520} open={open} onClose={onClose}>
      {action ? (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="Action ID">{action.action_id}</Descriptions.Item>
          <Descriptions.Item label="Memory ID">
            {action.metadata?.memory_id ?? action.object_id}
          </Descriptions.Item>
          <Descriptions.Item label="Policy Version">{action.policy_version}</Descriptions.Item>
          <Descriptions.Item label="Current Tier">{action.source_tier}</Descriptions.Item>
          <Descriptions.Item label="Target Tier">{action.target_tier ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="Action">{action.action_type}</Descriptions.Item>
          <Descriptions.Item label="Heat Score">{action.score.toFixed(3)}</Descriptions.Item>
          <Descriptions.Item label="Reason">
            <Typography.Text>{action.reason}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            {feedback?.execute_status ?? "not executed"}
          </Descriptions.Item>
          <Descriptions.Item label="Execution Type">
            {action.metadata?.execution_type ?? "Control-plane"}
          </Descriptions.Item>
          <Descriptions.Item label="Execution Feedback">
            {feedback?.failure_reason ??
              feedback?.metadata?.route_mode ??
              action.metadata?.physical_migration ??
              "N/A"}
          </Descriptions.Item>
        </Descriptions>
      ) : null}
    </Drawer>
  );
}
