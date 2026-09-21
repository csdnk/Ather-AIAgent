import { isMockMode, newId, postJson, requestJson } from "./client";
import type { B1Status, EmbeddingResponse } from "./types";
import { mockB1Embedding, mockB1Status } from "../mock/data";

export interface GenerateEmbeddingInput {
  text: string;
  inputType: "query" | "passage";
  tenantId?: string;
}

export function getB1Status(): Promise<B1Status> {
  if (isMockMode) return Promise.resolve(mockB1Status);
  return requestJson<B1Status>("/api/v1/b1/status");
}

export function generateEmbedding(input: GenerateEmbeddingInput): Promise<EmbeddingResponse> {
  if (isMockMode) return Promise.resolve(mockB1Embedding);
  return postJson<EmbeddingResponse>("/api/v1/b1/embeddings", {
    text: input.text,
    input_type: input.inputType,
    tenant_id: input.tenantId || "web-demo",
    request_id: newId("b1-req"),
    trace_id: newId("b1-trace"),
    source_id: newId("b1-source"),
  });
}
