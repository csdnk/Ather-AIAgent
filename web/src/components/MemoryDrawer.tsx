import { Descriptions, Drawer, Typography } from "antd";
import type { MemoryRecord } from "../api/types";

interface MemoryDrawerProps {
  memory: MemoryRecord | null;
  score?: number;
  open: boolean;
  onClose: () => void;
}

export function MemoryDrawer({ memory, score, open, onClose }: MemoryDrawerProps) {
  return (
    <Drawer title="Memory Detail" width={520} open={open} onClose={onClose}>
      {memory ? (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="Memory ID">{memory.id}</Descriptions.Item>
          <Descriptions.Item label="Type">{memory.type}</Descriptions.Item>
          <Descriptions.Item label="Status">{memory.state ?? "N/A"}</Descriptions.Item>
          <Descriptions.Item label="Content">
            <Typography.Paragraph>{memory.content}</Typography.Paragraph>
          </Descriptions.Item>
          <Descriptions.Item label="Source">{memory.source ?? "N/A"}</Descriptions.Item>
          <Descriptions.Item label="Storage">
            {memory.metadata?.storage ??
              memory.metadata?.milvus_projection_status ??
              memory.metadata?.projection_status ??
              "Primary memory store"}
          </Descriptions.Item>
          <Descriptions.Item label="Semantic Score">
            {score != null ? score.toFixed(3) : "N/A"}
          </Descriptions.Item>
          <Descriptions.Item label="Temporal / Decay Score">N/A</Descriptions.Item>
          <Descriptions.Item label="Created At">{memory.created_at ?? "N/A"}</Descriptions.Item>
          <Descriptions.Item label="Last Access">
            {memory.last_accessed_at ?? "N/A"}
          </Descriptions.Item>
          <Descriptions.Item label="Projection">
            {memory.vector_projection_status ?? memory.metadata?.milvus_projection_status ?? "N/A"}
          </Descriptions.Item>
          <Descriptions.Item label="Compression">
            {memory.compression_status ?? "N/A"}
          </Descriptions.Item>
        </Descriptions>
      ) : null}
    </Drawer>
  );
}
