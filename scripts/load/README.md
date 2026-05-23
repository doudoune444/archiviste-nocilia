# Load Tests — Archiviste Nocilia

k6 scripts targeting `POST /v1/chat` at `https://archiviste.nocilia.fr`.

## (a) Pre-requisites

- **k6 ≥ 0.50** installed locally (`k6 version`). Download: https://k6.io/docs/get-started/installation/
- **Cloudflare rate-limit bypass** (D-2): the production rule enforces 100 req/min/IP.
  Before running the 500-VU scenario, add the runner IP to the Cloudflare rule allowlist:
  1. Log into Cloudflare dashboard → `archiviste.nocilia.fr` → Security → WAF.
  2. Find the rate-limit rule (100 req/min/IP) → add runner IP as an exception.
  3. **Remove the exception immediately after the run** (runbook checklist — do not leave active).
  Without this bypass, the 500-VU run will generate massive 429 responses and the result is invalid.
- `TARGET_URL` environment variable (optional, defaults to `https://archiviste.nocilia.fr`).
- Mistral API budget cap of €30 confirmed in Mistral console before launch (D-3).

## (b) Commands per scenario

```bash
# 100 VUs — 30 s ramp-up, 60 s steady-state
k6 run --env SCENARIO=chat_100_users scripts/load/k6/chat-round-trip.js

# 500 VUs — 30 s ramp-up, 60 s steady-state (requires Cloudflare bypass, see above)
k6 run --env SCENARIO=chat_500_users scripts/load/k6/chat-round-trip.js

# Custom target URL (e.g. a GCP runner, canary revision)
k6 run --env SCENARIO=chat_100_users --env TARGET_URL=https://canary-xyz.run.app \
    scripts/load/k6/chat-round-trip.js
```

### Dry-run (no real traffic, 1 VU, 5 s)

```bash
k6 run --env SCENARIO=chat_100_users --duration 5s --vus 1 \
    --http-debug=full scripts/load/k6/chat-round-trip.js
```

Use this to verify body JSON, UUID generation, and header extraction before the live run.

## (c) Mistral budget estimate

Each VU sends approximately 1 request per ~1.5 s (1 s LLM + 0.1 s pacing + overhead).

| Scenario    | VUs | Duration | Requests est. | Cost/req est. | Total est. |
|-------------|-----|----------|---------------|---------------|------------|
| 100 users   | 100 | 100 s    | ~6 600        | ~€0.003       | ~€20       |
| 500 users   | 500 | 100 s    | ~33 000       | ~€0.003       | ~€100      |

Cost per request = tokens_in × price_in + tokens_out × price_out (Mistral Mistral-7B pricing).
Estimate: ~500 tokens in × €0.002/1k + ~200 tokens out × €0.006/1k ≈ €0.003/req.

**Hard cap: €30/run** (D-3). Set a spending alert in the Mistral console before launching.
Abort the run (`Ctrl+C`) immediately if the console shows budget nearing the cap.

> ⚠ **500-VU run at full 100 s duration ≈ €100, exceeds the €30 cap.** Either (a) shorten the 500-VU steady window to ~25 s to land near €25, (b) extrapolate from a 100-VU run, or (c) raise the cap explicitly with human sign-off before launching. Do NOT launch the full 500-VU/100 s slice without one of the three.

For a more conservative budget, run 100-VU scenario only and extrapolate.

## (d) Summary export procedure

Append `--summary-export=<path>` to any `k6 run` command:

```bash
k6 run --env SCENARIO=chat_100_users \
    --summary-export=scripts/load/runs/$(date +%Y-%m-%d)-chat_100_users.json \
    scripts/load/k6/chat-round-trip.js
```

Commit the summary JSON under `scripts/load/runs/` (max 2 files per scenario, rotate manually).
These files are the raw source of truth for the load-test report.

## (e) Report generation procedure

After both runs complete and summary JSONs are committed:

1. Open `docs/load-test-report-v1.md`.
2. Fill in the `## Run metadata` section: date, commit SHA (`git rev-parse HEAD`), region, instance size.
3. Copy `http_req_duration` p50/p95/p99, `http_reqs` total + rate, `http_req_failed` rate,
   and `gateway_overhead_ms` p50/p95 from the summary JSON files into the metrics table.
4. Evaluate each SLO threshold (p95 < 3 000 ms, overhead p95 < 80 ms, error rate < 1 %)
   and mark ✓ or ✗ in the verdict table.
5. Fill the cold-start observation, Mistral budget, Cloudflare bypass method,
   and Langfuse link sections.
6. If any threshold is ✗, open a `OPS-002` follow-up ticket and reference it in the report.
7. Commit the filled report as part of the OPS-001b PR.
