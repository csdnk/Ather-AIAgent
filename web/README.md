# AetherBrain P3 Web

Lightweight React frontend for demonstrating the current P3 runtime:

- B1 really computes embeddings.
- B2 really writes and recalls memory.
- B3 really evaluates heuristic scheduling actions.
- P4 is simulated as an independent upstream Agent application that only calls P3 over HTTP.

## Stack

React, Vite, TypeScript, Ant Design, and ECharts for the small B1 runtime chart.

## Install

```bash
cd web
npm install
```

## Development

```bash
cd web
npm run dev
```

Open `http://localhost:5173`.

The dev server proxies `/api` and `/health` to `VITE_AETHER_PROXY_TARGET`, and
`/p4-api` to `VITE_P4_PROXY_TARGET`.
The default proxy target is `http://localhost:8080`, matching `scripts/p3_service.py`.
The default P4 target is `http://localhost:8090`.

## API Base URL

For local dev, leave `VITE_AETHER_API_BASE_URL` empty and use the Vite proxy.
For a deployed frontend, set it to the P3 API origin, for example:

```bash
VITE_AETHER_API_BASE_URL=http://localhost:8080
VITE_P4_API_BASE_URL=http://localhost:8090
```

## Mock Mode

Mock mode is opt-in only:

```bash
VITE_USE_MOCK=true
```

When enabled, the header displays `MOCK MODE`. The app never silently falls back
to mock data when a real API call fails.

## Backend Requirements

Start the P3 service and its dependencies first. Minimal local command:

```bash
$env:PYTHONPATH='src;.'
python scripts/p3_service.py
$env:AETHER_P3_BASE_URL='http://localhost:8080'
python scripts/p4_simulator.py
```

For the full compose path:

```bash
docker compose up p3 b1-sidecar redis engine celery-worker
```

Milvus is optional unless `AETHER_B2_MILVUS_PROJECTION=true`.

## Pages

- Overview: P3 runtime health, B1/B2/B3 cards, and a simple pipeline view.
- B1 Embedding: sends text to P3 `/api/v1/b1/embeddings`, which calls the real B1 Sidecar.
- B2 Memory: writes `MemoryEvent`, queries `ContextRequest`, and polls long-text task status.
- B3 Scheduler: shows real schedule history and runs heuristic scheduling on context-derived candidates.
- P4 Reference Agent: creates a P4 session, gets P3 context before a turn, writes memory after
  the turn, and submits long-text tasks without importing P3 internals.

## Real APIs Used

- `GET /health`
- `GET /api/status`
- `GET /api/v1/b1/status`
- `POST /api/v1/b1/embeddings`
- `POST /api/v1/memory/events`
- `POST /api/v1/context`
- `POST /api/v1/b2/long-text`
- `GET /api/v1/b2/tasks/{task_id}`
- `POST /api/v1/b2/search`
- `POST /api/v1/b3/candidates`
- `GET /api/schedules`
- `POST /api/v1/schedules`

The P4 page calls the separate P4 Simulator API at port `8090`; the simulator then consumes only
the stable P3 endpoints documented in `../docs/P3_NORTHBOUND_API_V1.md`.

## Fields Not Yet Directly Available

- Global Working/Long-term memory counts are not exposed; the UI shows current ContextPack counts.
- Celery pending count is not exposed; task state is shown per submitted task.
- Physical Milvus to Redis prewarm is not asserted by the B3 adapter.
- Prediction model metrics are reserved because the prediction model is not deployed.

## Known Limits

- This is a demo frontend, not an admin platform.
- PDF/DOCX upload is blocked unless a backend parser endpoint is added.
- Runtime compression status is shown only when the task API returns it.
- B3 candidates are derived from current context recall; heat and action are still computed by B3.
