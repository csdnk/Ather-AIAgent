import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockScheduleRun } from "../mock/data";
import { ActionDrawer } from "./ActionDrawer";

describe("ActionDrawer", () => {
  it("renders B3 action details", () => {
    render(
      <ActionDrawer
        action={mockScheduleRun.actions[0]}
        feedback={mockScheduleRun.entries[0].feedback}
        open
        onClose={() => undefined}
      />,
    );
    expect(screen.getByText("heuristic-v1")).toBeInTheDocument();
    expect(screen.getByText("0.820")).toBeInTheDocument();
  });
});
