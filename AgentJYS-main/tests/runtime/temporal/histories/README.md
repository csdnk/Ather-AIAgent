`task-plan-v1.json` is a real completed engineering Workflow history captured
with Temporal CLI 1.9.1 and Python SDK 1.33.0 from the plan-v1 implementation.
It contains 17 events and synthetic task references, without business text or
credentials. Worker identity strings are replaced with `p3-test-worker`.

Keep this fixture unchanged when changing Workflow commands. Offline SDK
Replayer checks both a compatible query-only change and an incompatible
Activity-to-Timer change. It never connects to Temporal or runs Activities.
Current production input remains `plan_version=1`; introducing version 2 needs
its own preserved implementation and history fixtures, rather than relabeling
in-flight work.

`periodic-plan-v1.json` was generated with the original periodic implementation
at commit `9e643bb4a7880a8b21ef4089db40aff8c9d37b03`, CLI 1.9.1 and SDK 1.33.0.
Its 21 real events include a timer and reconcile/cancel signals; identities are
sanitized as above. It has no `p3-periodic-control-wake-v1` patch marker. The
current periodic Workflow replays it using the preserved timer behavior, while
new executions let a reconcile control wake the next bounded scan.
