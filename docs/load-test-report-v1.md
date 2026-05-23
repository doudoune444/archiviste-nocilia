# Load Test Report V1 — Archiviste Nocilia

**STATUS: PENDING LIVE DEPLOY (OPS-001b)**

All sections below are named and structured. Metrics tables contain `TBD` placeholders.
This skeleton is committed as part of OPS-001a. OPS-001b fills in real numbers after
INFRA-002 is live and the k6 run executes against the production endpoint.

---

## Run metadata

| Field              | 100-user run | 500-user run |
|--------------------|--------------|--------------|
| Date               | TBD          | TBD          |
| Gateway commit SHA | TBD          | TBD          |
| Workers commit SHA | TBD          | TBD          |
| Cloud Run region   | TBD          | TBD          |
| Instance size      | TBD          | TBD          |
| k6 version         | TBD          | TBD          |
| Run origin IP      | TBD          | TBD          |
| Summary JSON       | TBD          | TBD          |

---

## Metrics table

### 100 users (`chat_100_users` scenario)

| Metric                    | p50   | p95   | p99   | Total / Rate |
|---------------------------|-------|-------|-------|--------------|
| `http_req_duration` (ms)  | TBD   | TBD   | TBD   | TBD reqs     |
| `http_reqs` (rate)        | —     | —     | —     | TBD req/s    |
| `http_req_failed` (rate)  | —     | —     | —     | TBD %        |
| `gateway_overhead_ms`     | TBD   | TBD   | —     | —            |

### 500 users (`chat_500_users` scenario)

| Metric                    | p50   | p95   | p99   | Total / Rate |
|---------------------------|-------|-------|-------|--------------|
| `http_req_duration` (ms)  | TBD   | TBD   | TBD   | TBD reqs     |
| `http_reqs` (rate)        | —     | —     | —     | TBD req/s    |
| `http_req_failed` (rate)  | —     | —     | —     | TBD %        |
| `gateway_overhead_ms`     | TBD   | TBD   | —     | —            |

---

## SLO verdicts

| SLO                                              | Target        | 100 users | 500 users |
|--------------------------------------------------|---------------|-----------|-----------|
| `http_req_duration` p95                          | < 3 000 ms    | TBD       | TBD       |
| `gateway_overhead_ms` p95                        | < 80 ms       | N/A       | TBD       |
| `http_req_failed` rate                           | < 1 %         | TBD       | TBD       |
| 5xx count (strict)                               | 0             | TBD       | TBD       |

V1 promotion status: **TBD** (blocked on live run — OPS-001b).

---

## Cold-start observation

*Filled by OPS-001b.*

Observation of scale-to-zero cold-start impact on the first wave of VUs after an idle period.
Expected: gateway ~3 s, workers ~5 s cold-start per `vision.md Q6`.
Whether cold-start requests are excluded from the steady-state p95 calculation will be noted here.

---

## Mistral budget (real)

*Filled by OPS-001b.*

- Actual Mistral API cost for the 100-user run: TBD
- Actual Mistral API cost for the 500-user run: TBD
- Source: Mistral console billing export or invoice estimate post-run.
- Hard cap applied: €30/run (D-3). Run aborted if cap approached: TBD (yes/no).

---

## Cloudflare bypass (AC-7)

*Filled by OPS-001b.*

Option chosen: IP allowlist in Cloudflare WAF rule (D-2).

- Timestamp whitelist added: TBD
- Timestamp whitelist removed: TBD
- Evidence: Cloudflare rule capture / screenshot — TBD (attached or linked here).

---

## Langfuse traces (AC-9)

*Filled by OPS-001b.*

Langfuse link filtered to the 500-user run time window:
TBD (format: `https://langfuse.nocilia.fr/project/xxx/traces?from=<start>&to=<end>`)

---

## Follow-up tickets

*Filled by OPS-001b.*

If any SLO threshold fails, a follow-up ticket is opened and listed here before V1 promotion.

| Ticket  | SLO failed            | Root cause | Status |
|---------|-----------------------|------------|--------|
| TBD     | TBD                   | TBD        | TBD    |

---

## Notes for OPS-001b executor

1. Run `k6 run --env SCENARIO=chat_100_users --summary-export=scripts/load/runs/<date>-chat_100_users.json scripts/load/k6/chat-round-trip.js`.
2. Run `k6 run --env SCENARIO=chat_500_users --summary-export=scripts/load/runs/<date>-chat_500_users.json scripts/load/k6/chat-round-trip.js`.
3. Commit summary JSONs under `scripts/load/runs/`.
4. Fill all `TBD` fields in this report from summary JSONs + console exports.
5. Remove this "Notes" section when the report is finalised.
