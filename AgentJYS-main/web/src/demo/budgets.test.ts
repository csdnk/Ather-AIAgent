import { expect, it } from "vitest";
import { observationMilliseconds, requestMilliseconds } from "./budgets";

it("observes slow background work for two hours with finite network requests", () => {
  expect(observationMilliseconds).toBe(2 * 60 * 60 * 1000);
  expect(requestMilliseconds).toBe(60 * 1000);
  expect(observationMilliseconds).toBeGreaterThan(requestMilliseconds);
});
