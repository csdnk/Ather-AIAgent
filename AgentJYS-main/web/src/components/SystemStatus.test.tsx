import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockSystemStatus } from "../mock/data";
import { deriveSystemStatus } from "../utils/status";
import { SystemStatusView } from "./SystemStatus";

describe("SystemStatusView", () => {
  it("derives healthy status from P3_NORMAL", () => {
    expect(deriveSystemStatus(mockSystemStatus)).toBe("Healthy");
  });

  it("renders the system status button", () => {
    render(<SystemStatusView data={mockSystemStatus} />);
    expect(screen.getByText("System")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
  });
});
