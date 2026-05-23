/**
 * k6 load test — Archiviste Nocilia POST /v1/chat
 *
 * AC-1: Two named scenarios selectable via --env SCENARIO=<name>.
 *   - chat_100_users : 100 VUs steady-state, 60 s duration, 30 s ramp-up.
 *   - chat_500_users : 500 VUs steady-state, 60 s duration, 30 s ramp-up.
 *
 * AC-2: Each iteration POSTs {"conversation_id": "<uuid-v4-per-VU>", "query": "<prompt>"}
 *   against TARGET_URL (default https://archiviste.nocilia.fr).
 *   Prompts are loaded from prompts.json (≥ 10 in-domain entries).
 *
 * AC-3: Thresholds fail the run when SLOs are violated:
 *   - http_req_duration p95 < 3 000 ms (both scenarios).
 *   - http_req_failed rate < 1 %.
 *   - gateway_overhead_ms p95 < 80 ms (500-user scenario).
 *
 * AC-4: Custom Trend gateway_overhead_ms populated from X-Gateway-Overhead-Ms
 *   response header, tagged by scenario. Missing header on 2xx increments
 *   gateway_overhead_header_missing counter (warning, not fail).
 *
 * Usage:
 *   k6 run --env SCENARIO=chat_100_users scripts/load/k6/chat-round-trip.js
 *   k6 run --env SCENARIO=chat_500_users scripts/load/k6/chat-round-trip.js
 *
 * Executor: ramping-vus
 *   Ramp-up: 0 → target VUs over 30 s.
 *   Steady:  target VUs held for 60 s.
 *   Ramp-down: target VUs → 0 over 10 s.
 */

import http from "k6/http";
import { Trend, Counter } from "k6/metrics";
import { check, sleep } from "k6";
import { uuidv4 } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const TARGET_URL = __ENV.TARGET_URL || "https://archiviste.nocilia.fr";
const SCENARIO = __ENV.SCENARIO || "chat_100_users";

// Prompts loaded from the adjacent JSON file (AC-2, D-6: decoupled from golden set).
// k6 open() reads files relative to the script location.
const PROMPTS = JSON.parse(open("./prompts.json"));

// ---------------------------------------------------------------------------
// Scenario definitions (AC-1)
// ---------------------------------------------------------------------------

// Only the selected scenario is active; the other is disabled via exec:false.
// This lets k6 apply scenario-tagged thresholds correctly.
const SCENARIO_CONFIG = {
  chat_100_users: {
    executor: "ramping-vus",
    // Ramp-up: ≤ 30 s (AC-1). Steady: ≥ 60 s (AC-1). Ramp-down: 10 s.
    stages: [
      { duration: "30s", target: 100 },
      { duration: "60s", target: 100 },
      { duration: "10s", target: 0 },
    ],
    exec: "chatIteration",
    tags: { scenario: "chat_100_users" },
  },
  chat_500_users: {
    executor: "ramping-vus",
    stages: [
      { duration: "30s", target: 500 },
      { duration: "60s", target: 500 },
      { duration: "10s", target: 0 },
    ],
    exec: "chatIteration",
    tags: { scenario: "chat_500_users" },
  },
};

// Disable the scenario that was not selected.
const activeScenarios = {};
for (const [name, config] of Object.entries(SCENARIO_CONFIG)) {
  activeScenarios[name] = name === SCENARIO ? config : { executor: "constant-vus", vus: 0, duration: "1s", exec: "noop" };
}

// ---------------------------------------------------------------------------
// Custom metrics (AC-4)
// ---------------------------------------------------------------------------

/** Gateway-only overhead in ms, sourced from X-Gateway-Overhead-Ms header. */
const gatewayOverheadMs = new Trend("gateway_overhead_ms", true);

/** Incremented when X-Gateway-Overhead-Ms header is absent on a 2xx response. */
const overheadHeaderMissing = new Counter("gateway_overhead_header_missing");

// ---------------------------------------------------------------------------
// Thresholds (AC-3)
// ---------------------------------------------------------------------------

export const options = {
  scenarios: activeScenarios,
  discardResponseBodies: true, // AC per plan §D-H5 — headers remain accessible.
  thresholds: {
    // p95 round-trip < 3 s on both scenarios.
    "http_req_duration{scenario:chat_100_users}": ["p(95)<3000"],
    "http_req_duration{scenario:chat_500_users}": ["p(95)<3000"],
    // Global error rate < 1 % (proxy for availability SLO).
    http_req_failed: ["rate<0.01"],
    // Gateway overhead p95 < 80 ms at 500 users (vision.md SLO).
    "gateway_overhead_ms{scenario:chat_500_users}": ["p(95)<80"],
  },
};

// ---------------------------------------------------------------------------
// Per-VU state
// ---------------------------------------------------------------------------

/** Unique conversation ID generated once per VU to simulate a real session. */
let conversationId;

export function setup() {
  // Validate prompts file is loaded.
  if (!PROMPTS || PROMPTS.length < 10) {
    throw new Error(`prompts.json must contain ≥ 10 entries, got ${PROMPTS ? PROMPTS.length : 0}`);
  }
}

// ---------------------------------------------------------------------------
// Main iteration (AC-2, AC-4)
// ---------------------------------------------------------------------------

export function chatIteration() {
  // Lazy-init conversationId per VU (setup() runs once globally, not per VU).
  if (!conversationId) {
    conversationId = uuidv4();
  }

  // Pick a prompt at random from the in-domain pool (uniform distribution).
  const prompt = PROMPTS[Math.floor(Math.random() * PROMPTS.length)];

  const payload = JSON.stringify({
    conversation_id: conversationId,
    query: prompt,
  });

  const params = {
    headers: { "Content-Type": "application/json" },
    tags: { scenario: SCENARIO },
  };

  const res = http.post(`${TARGET_URL}/v1/chat`, payload, params);

  // AC-3: check for 2xx success.
  check(res, {
    "status is 2xx": (r) => r.status >= 200 && r.status < 300,
  });

  // AC-4: extract X-Gateway-Overhead-Ms and record custom metric.
  if (res.status >= 200 && res.status < 300) {
    const headerVal = res.headers["X-Gateway-Overhead-Ms"];
    if (headerVal !== undefined && headerVal !== null && headerVal !== "") {
      const overheadValue = parseFloat(headerVal);
      if (!isNaN(overheadValue)) {
        gatewayOverheadMs.add(overheadValue, { scenario: SCENARIO });
      }
    } else {
      overheadHeaderMissing.add(1, { scenario: SCENARIO });
      console.warn(`X-Gateway-Overhead-Ms header absent on 2xx (scenario=${SCENARIO})`);
    }
  }

  // Minimal pacing to avoid tight busy-loop.
  sleep(0.1);
}

/** No-op function for disabled scenarios. */
export function noop() {}
