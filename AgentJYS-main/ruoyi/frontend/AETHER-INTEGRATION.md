# Aether frontend

The native Vue/Element Plus shell uses the existing tenant, user, role, access log, operation log, configuration and notification modules. `src/views/aether` provides real operations views; the home view is the Aether overview instead of upstream demo statistics.

Build with Node >=20.19 and pnpm >=8.6:

```sh
pnpm install --frozen-lockfile
node --test tests/operations.test.mjs
pnpm build:prod
```

Production defaults use `/ruoyi/` with same-origin `/ruoyi-api`. Deployment may override the Vite variables. Server-side tenancy stays enabled; browser login never asks for a tenant or sends a tenant-id. Native admin tenant visiting still requires its server permission. The default account/password fields are empty, and remembered login does not retain the password.

Operations page controls are additionally limited by the live Aether identity permission list. Unknown or accepted commands retain their original identifier and block duplicate submission. A read of the original receipt determines whether the operation finished. Tables show live metadata and exact unknown/unavailable states. No sample statistics or synthetic chart points are added.

Upstream source revision and MIT license remain in `AETHER-UPSTREAM.json` and `LICENSE`. Dependency caches and build output are external development artifacts, not application source.
