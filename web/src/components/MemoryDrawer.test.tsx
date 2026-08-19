import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockMemory } from "../mock/data";
import { MemoryDrawer } from "./MemoryDrawer";

describe("MemoryDrawer", () => {
  it("renders memory content and score", () => {
    render(<MemoryDrawer memory={mockMemory} score={0.91} open onClose={() => undefined} />);
    expect(screen.getByText(mockMemory.id)).toBeInTheDocument();
    expect(screen.getByText("0.910")).toBeInTheDocument();
  });
});
