# AET-22: authentication and WRITE-only denial

Owner: A (Recall consumer tests). No identity, grant, RBAC or product policy changes.
Scope: RC-AUTH-01 and RC-AUTH-02 from the 2026-10-07 Recall QA document.

## Test boundary and shared preparation

The agreed boundary is HTTP admission, original operation polling and original
Recall Record/result retrieval, with authorized maintenance observations.
`tests/recall_authorization_support.py` carries forward AET-8's preparation,
polling and evidence responsibilities. Its replacement helpers bind exact receipt
Refs rather than selecting arbitrary catalog entries, keep F1 in Working, and
observe failure terminals without admission retries. Existing AET-8/11/12 files
are unchanged; their older long-term fixtures and permission strings are not
used as current product contracts.

Each of eight variants owns an independent scope, identity configuration, Azure
namespace/schema and F1 copy. F1 is exactly “用户喝咖啡不加糖。” and
“用户喜欢乌龙茶。”; version 1, Ready, body generation and SHA-256 are checked
through Remember HTTP reads. Legal READ controls retrieve both coffee and tea.
U01 has only `memory:read`; U05 has only `memory:write`, at the same home scope.
The maintainer has READ/WRITE/DIAGNOSE and explicit deployment task-maintenance
authority. This does not confer Ruoyi admin authority or access to other traces.

POST denial is compared with the complete paginated maintenance task view. A new
job must terminate unsuccessfully with the matching error and no retrievable
Pack. No new Recall task is recorded as `not_admitted`, not as a fabricated failed
Record. All variants then GET the known original Recall Record and result, and
the original operation and result. Assertions inspect raw responses before
allowlisted evidence is saved. An error code, empty result, or accepted POST
cannot establish success. The prior successful control Pack is distinguished
from any forbidden new Pack.

## Bound target contracts

For this source revision, `runtime/flows/http.py` maps UNAUTHENTICATED to 401 and
FORBIDDEN to 403; `runtime/contracts/models.py` defines the permission values.
`runtime/foundation/identity.py` rejects unknown/removed credentials, and
`Service.reload_identity()` applies the owned fixture's increasing configuration
revision. The revoked-credential variant first uses U09 successfully, removes
its credential from revision 2, confirms rejection, then tries its original Pack.
Credential removal proves this supported invalidation mechanism only. JWT expiry
and other Q01 policy details remain `blocked_requirement`.

## Separate execution lanes

Strict HTTP peers test the harness, **not** product authorization:

```sh
python -B -m pytest tests/unit/test_recall_authorization_support.py \
  -p no:cacheprovider --basetemp=<external-workspace>/strict-tmp
```

Real-storage component tests reuse `azure_component_service.Service` and
`ComponentConfiguration`: real PostgreSQL/Redis/Milvus/Ceph, a session-owned
Temporal server and controlled embeddings. Use the existing AKS runner and
dedicated `p3_test_` backend resources documented in CONTRIBUTING. Select:

```sh
python -B -m pytest tests/integration/test_recall_authentication_denial.py \
  -p no:cacheprovider --basetemp=<external-workspace>/component-tmp
```

Set `P3_RECALL_EVIDENCE_DIR` to an external workspace. The default is under the
system temporary directory, `aether-workspace-support/AET-22/executions`.
Each execution gets an exclusive evidence directory. Project-contained evidence
directories are rejected; credentials, raw正文 and free-form error messages are
not persisted. Formal acceptance summaries belong in `交付成果/验收/`.

Missing backend configuration fails setup as `blocked_fixture`, never a pass or
fallback. Even if HTTP assertions complete, this component lane ends as XFAIL
with `http_checks=passed`, `acceptance=blocked_fixture`, because it lacks:

- A deployed source SHA/image digest and actual native-model binding.
- Published **projection** generation via an authorized HTTP view. The captured
  body-location generation is explicitly distinct from that publication proof.
- Authorized permission-decision and body/model-call evidence for denied paths.

These gaps must be supplied by the target control plane before full security
acceptance. Do not convert the XFAIL to PASS by removing blockers or treating the
controlled embedding as a native-model run. This change leaves AET-11/AET-12 and
the parent issue untouched.
