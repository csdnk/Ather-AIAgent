import { Card, Space, Tooltip, Typography } from "antd";
import type { ReactNode } from "react";

interface MetricCardProps {
  title: string;
  value: ReactNode;
  suffix?: ReactNode;
  tooltip?: string;
  extra?: ReactNode;
}

export function MetricCard({ title, value, suffix, tooltip, extra }: MetricCardProps) {
  const content = (
    <Typography.Text type="secondary" className="metric-card-title">
      {title}
    </Typography.Text>
  );

  return (
    <Card size="small" className="metric-card">
      <Space direction="vertical" size={4} className="full-width">
        {tooltip ? <Tooltip title={tooltip}>{content}</Tooltip> : content}
        <Space align="baseline" size={6}>
          <Typography.Text className="metric-card-value">{value ?? "N/A"}</Typography.Text>
          {suffix ? <Typography.Text type="secondary">{suffix}</Typography.Text> : null}
        </Space>
        {extra}
      </Space>
    </Card>
  );
}
