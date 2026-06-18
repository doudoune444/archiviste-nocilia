//! Integration tests for BOARD-001: public read-only lore-gap board.
//!
//! Covers:
//! - Anonymous `GET /v1/board` → 200 with open tickets JSON (AC: public API, LIMIT enforced).
//! - Anonymous `GET /board` → 200 text/html with stable selector.
//! - Board exposes no write path anonymously.
//! - `GET /dashboard` still 401 for anonymous, 403 for member.
//! - New assets `board.js` and `board.css` served with security headers.
//! - Static grep: `board.html` has no inline script/style/on*= and `board.js` uses textContent.

#![allow(clippy::unwrap_used, clippy::expect_used)]
// Pedantic doc_markdown suppressed for test prose (cf. test_observability_status.rs).
#![allow(clippy::doc_markdown)]

mod common;
use common::jwt_helpers::{make_app_state, make_test_config, sign_test_token};

use archiviste_gateway::{auth::extractor::UserTier, router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state() -> Arc<AppState> {
    make_app_state("http://127.0.0.1:1")
}

async fn get_anon(app: axum::Router, uri: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("GET")
            .uri(uri)
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap()
}

async fn body_string(resp: axum::response::Response) -> String {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    String::from_utf8_lossy(&bytes).to_string()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

/// Expected CSP value (SEC-003 AC-1 literal).
const CSP_VALUE: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
    img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; \
    base-uri 'none'; form-action 'self'";

fn assert_security_headers(resp: &axum::response::Response) {
    let headers = resp.headers();

    let csp = headers
        .get("content-security-policy")
        .expect("CSP header missing")
        .to_str()
        .unwrap();
    assert_eq!(csp, CSP_VALUE, "CSP value mismatch");

    let xcto = headers
        .get("x-content-type-options")
        .expect("X-Content-Type-Options header missing")
        .to_str()
        .unwrap();
    assert_eq!(xcto, "nosniff");

    let referrer = headers
        .get("referrer-policy")
        .expect("Referrer-Policy header missing")
        .to_str()
        .unwrap();
    assert_eq!(referrer, "strict-origin-when-cross-origin");

    let xfo = headers
        .get("x-frame-options")
        .expect("X-Frame-Options header missing")
        .to_str()
        .unwrap();
    assert_eq!(xfo, "DENY");
}

// ---------------------------------------------------------------------------
// AC: anonymous GET /v1/board → 200 with JSON (no DB → 503 acceptable in no-DB test)
// The route must exist and be public (not gated).
// ---------------------------------------------------------------------------

/// BOARD-001: anonymous GET /v1/board → not 401 and not 403 (public route, no auth gate).
/// Without a DB pool the handler returns 503 upstream_unavailable; that is fine here —
/// we only assert that auth is NOT required (status ≠ 401 and ≠ 403).
#[tokio::test]
async fn board_api_anonymous_not_auth_gated() {
    // BOARD-001 AC: /v1/board must be public (no RequireAuthor gate)
    let app = router(make_state());
    let resp = get_anon(app, "/v1/board").await;
    let status = resp.status().as_u16();
    assert!(
        status != 401 && status != 403,
        "GET /v1/board must not gate on auth; got {status}"
    );
}

/// BOARD-001 AC: anonymous GET /v1/board without DB → JSON error (not HTML, not auth error).
#[tokio::test]
async fn board_api_anonymous_no_db_returns_json() {
    // BOARD-001 AC: /v1/board returns JSON (even on error path without DB)
    let app = router(make_state());
    let resp = get_anon(app, "/v1/board").await;
    // Without DB pool we expect 503; response must be JSON application/json.
    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(
        ct.starts_with("application/json"),
        "expected application/json, got: {ct}"
    );
}

/// BOARD-001 AC: GET /v1/board carries all 5 security headers.
#[tokio::test]
async fn board_api_has_security_headers() {
    // BOARD-001 AC: security headers on /v1/board
    let app = router(make_state());
    let resp = get_anon(app, "/v1/board").await;
    assert_security_headers(&resp);
}

/// BOARD-001 AC: /v1/board response structure matches TicketsResponse shape when DB is present.
/// Seeded with 2 open + 1 resolved ticket; expect items.len()=2, total=2.
#[sqlx::test(migrations = "../migrations")]
async fn board_api_with_db_returns_open_tickets(pool: sqlx::PgPool) {
    // BOARD-001 AC: open-only tickets returned, order by priority_score DESC
    let anon_user_id = Uuid::nil();

    for n in 1_u32..=3 {
        sqlx::query(
            "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
        )
        .bind(Uuid::from_u128(u128::from(n)))
        .bind(anon_user_id)
        .bind(format!("gs://archiviste-conversations/conv/{n}.md"))
        .execute(&pool)
        .await
        .unwrap();
    }

    // Open tickets: priority 5 and 3
    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status) \
         VALUES ($1, $2, $3, $4, $5)",
    )
    .bind(Uuid::from_u128(1))
    .bind("Public question prio 5")
    .bind("lore")
    .bind(5_i32)
    .bind("open")
    .execute(&pool)
    .await
    .unwrap();

    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status) \
         VALUES ($1, $2, $3, $4, $5)",
    )
    .bind(Uuid::from_u128(2))
    .bind("Public question prio 3")
    .bind("lore")
    .bind(3_i32)
    .bind("open")
    .execute(&pool)
    .await
    .unwrap();

    // Resolved ticket must NOT appear
    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status) \
         VALUES ($1, $2, $3, $4, $5)",
    )
    .bind(Uuid::from_u128(3))
    .bind("Resolved question")
    .bind("lore")
    .bind(10_i32)
    .bind("resolved")
    .execute(&pool)
    .await
    .unwrap();

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;

    let items = body["items"].as_array().expect("items must be array");
    assert_eq!(items.len(), 2, "must return exactly 2 open tickets");
    assert_eq!(body["total"], 2, "total must be 2");

    // Priority ordering: 5 first
    assert_eq!(items[0]["priority_score"], 5);
    assert_eq!(items[1]["priority_score"], 3);

    // All returned tickets must be open
    for item in items {
        assert_eq!(item["status"], "open");
    }
}

/// BOARD-001 AC: LIMIT is enforced — ?limit=1 returns only 1 item.
#[sqlx::test(migrations = "../migrations")]
async fn board_api_limit_enforced(pool: sqlx::PgPool) {
    // BOARD-001 AC: LIMIT parameter honoured (security.md A01 — no bulk without LIMIT)
    let anon_user_id = Uuid::nil();

    for n in 1_u32..=2 {
        sqlx::query(
            "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
        )
        .bind(Uuid::from_u128(u128::from(n)))
        .bind(anon_user_id)
        .bind(format!("gs://archiviste-conversations/conv/{n}.md"))
        .execute(&pool)
        .await
        .unwrap();

        sqlx::query(
            "INSERT INTO tickets (conversation_id, question, category, priority_score, status) \
             VALUES ($1, $2, $3, $4, $5)",
        )
        .bind(Uuid::from_u128(u128::from(n)))
        .bind(format!("Q{n}"))
        .bind("lore")
        .bind(i32::try_from(n).unwrap())
        .bind("open")
        .execute(&pool)
        .await
        .unwrap();
    }

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board?limit=1").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let items = body["items"].as_array().expect("items must be array");
    assert_eq!(items.len(), 1, "limit=1 must return exactly 1 item");
    assert_eq!(body["total"], 2, "total still reflects all open tickets");
    assert_eq!(body["limit"], 1);
}

// ---------------------------------------------------------------------------
// AC: POST /v1/board must not exist (read-only)
// ---------------------------------------------------------------------------

/// BOARD-001 AC: POST /v1/board → 404 or 405 (no write path exposed anonymously).
#[tokio::test]
async fn board_no_write_path() {
    // BOARD-001 AC: only GET is exposed; write path must not exist
    let app = router(make_state());
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/board")
                .header("content-type", "application/json")
                .body(Body::from("{}"))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status().as_u16();
    assert!(
        status == 404 || status == 405,
        "POST /v1/board must not exist; got {status}"
    );
}

// ---------------------------------------------------------------------------
// AC: GET /board → 200 text/html with stable selector
// ---------------------------------------------------------------------------

/// BOARD-001 AC: anonymous GET /board → 200, text/html, body has <table id="board-table".
#[tokio::test]
async fn board_page_200_for_anonymous() {
    // BOARD-001 AC: public board page is accessible without auth
    let app = router(make_state());
    let resp = get_anon(app, "/board").await;

    assert_eq!(resp.status(), StatusCode::OK, "expected 200 for /board");

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(ct.starts_with("text/html"), "expected text/html, got: {ct}");

    let body = body_string(resp).await;
    assert!(
        body.contains(r#"<table id="board-table""#),
        "stable selector <table id=\"board-table\" not found in board.html body"
    );
}

/// BOARD-001 AC: GET /board carries all 5 security headers.
#[tokio::test]
async fn board_page_has_security_headers() {
    // BOARD-001 AC: security headers on /board page
    let app = router(make_state());
    let resp = get_anon(app, "/board").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// AC: author dashboard still gated
// ---------------------------------------------------------------------------

/// BOARD-001 AC: GET /dashboard still returns 401 for anonymous (not weakened).
#[tokio::test]
async fn dashboard_still_gated_for_anonymous() {
    // BOARD-001 AC: /dashboard auth gate not weakened by board addition
    let app = router(make_state());
    let resp = get_anon(app, "/dashboard").await;
    assert_eq!(
        resp.status(),
        StatusCode::UNAUTHORIZED,
        "GET /dashboard must still require author JWT"
    );
}

/// BOARD-001 AC: GET /dashboard still returns 403 for member tier.
#[tokio::test]
async fn dashboard_still_gated_for_member() {
    // BOARD-001 AC: /dashboard author gate not weakened; member still 403
    let app = router(make_state());
    let token = sign_test_token(Uuid::new_v4(), UserTier::Member, Uuid::new_v4());
    let resp = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/dashboard")
                .header("authorization", format!("Bearer {token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::FORBIDDEN,
        "GET /dashboard must still return 403 for member"
    );
}

// ---------------------------------------------------------------------------
// AC: new assets served with correct content-type and security headers
// ---------------------------------------------------------------------------

/// BOARD-001 AC: GET /assets/board.js → 200, Content-Type JS.
#[tokio::test]
async fn board_js_served() {
    // BOARD-001 AC: board.js static asset accessible
    let app = router(make_state());
    let resp = get_anon(app, "/assets/board.js").await;

    assert_eq!(resp.status(), StatusCode::OK, "expected 200 for board.js");

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(
        ct.starts_with("application/javascript") || ct.starts_with("text/javascript"),
        "expected JS content-type, got: {ct}"
    );
}

/// BOARD-001 AC: GET /assets/board.css → 200, Content-Type text/css.
#[tokio::test]
async fn board_css_served() {
    // BOARD-001 AC: board.css static asset accessible
    let app = router(make_state());
    let resp = get_anon(app, "/assets/board.css").await;

    assert_eq!(resp.status(), StatusCode::OK, "expected 200 for board.css");

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(
        ct.starts_with("text/css"),
        "expected text/css content-type, got: {ct}"
    );
}

/// BOARD-001 AC: GET /assets/board.js carries all 5 security headers.
#[tokio::test]
async fn board_js_has_security_headers() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/board.js").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// BOARD-001 AC: GET /assets/board.css carries all 5 security headers.
#[tokio::test]
async fn board_css_has_security_headers() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/board.css").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// AC: static grep — board.html has no inline script/style/on*= handlers
//     board.js uses textContent (never innerHTML)
// ---------------------------------------------------------------------------

/// BOARD-001 AC: board.html must have no inline <script>, <style>, style=, or on*= handlers.
#[test]
fn board_html_no_inline_code() {
    // BOARD-001 AC: CSP script-src 'self' — no inline content allowed
    let html = std::fs::read_to_string("static/board.html")
        .expect("static/board.html not found — check CWD or file creation");

    assert!(
        !html.contains("<script>") && !html.contains("<script\n"),
        "board.html must not contain inline <script> block"
    );
    assert!(
        !html.contains("<style"),
        "board.html must not contain <style> block"
    );
    assert!(
        !html.contains("style=\"") && !html.contains("style='"),
        "board.html must not contain style= attribute"
    );

    let lower = html.to_lowercase();
    for handler in &[
        " onclick=",
        " onload=",
        " onsubmit=",
        " onchange=",
        " oninput=",
        " onkeydown=",
        " onkeyup=",
        " onkeypress=",
        " onmouseover=",
        " onfocus=",
        " onblur=",
    ] {
        assert!(
            !lower.contains(handler),
            "board.html must not contain inline event handler: {handler}"
        );
    }
}

/// BOARD-001 AC: board.js must use textContent (never innerHTML) for ticket data.
#[test]
fn board_js_uses_text_content_not_inner_html() {
    // BOARD-001 AC: XSS prevention — visitor questions rendered via textContent only
    let js = std::fs::read_to_string("static/assets/board.js")
        .expect("static/assets/board.js not found — check CWD or file creation");

    assert!(
        js.contains("textContent"),
        "board.js must use textContent to render ticket data"
    );
    // Match the DOM sink (`.innerHTML`), not the bare word — which legitimately
    // appears in the "(never innerHTML)" header comment.
    assert!(
        !js.contains(".innerHTML"),
        "board.js must never use the .innerHTML sink (XSS risk with user-supplied ticket questions)"
    );
}

/// BOARD-001 AC: board handler source must not carry RequireAuthor (public route).
#[test]
fn board_handler_is_public() {
    // BOARD-001 AC: board is public, not author-gated
    let src = include_str!("../src/handlers/board.rs");
    assert!(
        !src.contains("RequireAuthor"),
        "board.rs must not use RequireAuthor (public route)"
    );
}

/// BOARD-001 AC: board handler source must use LIMIT (security.md A01).
#[test]
fn board_handler_has_limit() {
    // BOARD-001 AC: no bulk endpoint without LIMIT (security.md A01)
    let src = include_str!("../src/handlers/board.rs");
    assert!(
        src.contains("LIMIT"),
        "board.rs must enforce a LIMIT clause (security.md A01)"
    );
}

// ---------------------------------------------------------------------------
// #163: judges_not_passed field surfaced in board items
// ---------------------------------------------------------------------------

/// #163 AC: board item includes judges_not_passed field; overridden ticket is distinguishable.
#[sqlx::test(migrations = "../migrations")]
async fn board_item_includes_judges_not_passed(pool: sqlx::PgPool) {
    // #163 AC: judges_not_passed surfaced in board JSON — distinguishes judge-confirmed
    // from human-override tickets.
    let anon_user_id = Uuid::nil();

    for n in 1_u32..=2 {
        sqlx::query(
            "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
        )
        .bind(Uuid::from_u128(u128::from(n) + 100))
        .bind(anon_user_id)
        .bind(format!("gs://archiviste-conversations/conv/163-{n}.md"))
        .execute(&pool)
        .await
        .unwrap();
    }

    // Judge-confirmed ticket (judges_not_passed=false — the default).
    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status, judges_not_passed) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(Uuid::from_u128(101))
    .bind("Question confirmée par les juges")
    .bind("lore")
    .bind(2_i32)
    .bind("open")
    .bind(false)
    .execute(&pool)
    .await
    .unwrap();

    // Human-override ticket (judges_not_passed=true).
    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status, judges_not_passed) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(Uuid::from_u128(102))
    .bind("Question non confirmée — envoyée quand même")
    .bind("lore")
    .bind(1_i32)
    .bind("open")
    .bind(true)
    .execute(&pool)
    .await
    .unwrap();

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let items = body["items"].as_array().expect("items must be array");
    assert_eq!(items.len(), 2);

    // #163 AC: field present on both items.
    for item in items {
        assert!(
            item.get("judges_not_passed").is_some(),
            "judges_not_passed must be present in board item: {item}"
        );
    }

    // Confirmed ticket (priority 2, first by ordering) → judges_not_passed=false.
    assert_eq!(
        items[0]["judges_not_passed"],
        serde_json::json!(false),
        "judge-confirmed ticket must have judges_not_passed=false"
    );
    // Overridden ticket (priority 1, second) → judges_not_passed=true.
    assert_eq!(
        items[1]["judges_not_passed"],
        serde_json::json!(true),
        "human-override ticket must have judges_not_passed=true"
    );
}

/// #163 AC: board.js renders distinguishing badge text for judges_not_passed.
#[test]
fn board_js_renders_confirmation_badge() {
    // #163 AC: board.js must surface judges_not_passed as a visible badge
    let js = std::fs::read_to_string("static/assets/board.js")
        .expect("static/assets/board.js not found");
    assert!(
        js.contains("judges_not_passed"),
        "board.js must reference judges_not_passed to render the badge"
    );
    assert!(
        js.contains("non confirmé par les juges"),
        "board.js must render 'non confirmé par les juges' badge text"
    );
}

// ---------------------------------------------------------------------------
// BOARD-001 AFK: category filter + sort=priority|date
// ---------------------------------------------------------------------------

/// Seed helper: insert one conversation + one ticket into the pool.
async fn insert_ticket(
    pool: &sqlx::PgPool,
    conv_id: uuid::Uuid,
    user_id: uuid::Uuid,
    question: &str,
    category: &str,
    priority_score: i32,
) {
    sqlx::query(
        "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
    )
    .bind(conv_id)
    .bind(user_id)
    .bind(format!("gs://archiviste-conversations/conv/{conv_id}.md"))
    .execute(pool)
    .await
    .unwrap();

    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status) \
         VALUES ($1, $2, $3, $4, 'open')",
    )
    .bind(conv_id)
    .bind(question)
    .bind(category)
    .bind(priority_score)
    .execute(pool)
    .await
    .unwrap();
}

/// BOARD-001 AFK AC: ?category=lore returns only tickets in category "lore".
#[sqlx::test(migrations = "../migrations")]
async fn board_category_filter_narrows_results(pool: sqlx::PgPool) {
    // BOARD-001 AFK AC: category filter returns only matching tickets
    let user_id = Uuid::nil();

    insert_ticket(&pool, Uuid::from_u128(200), user_id, "Lore Q1", "lore", 5).await;
    insert_ticket(&pool, Uuid::from_u128(201), user_id, "Lore Q2", "lore", 3).await;
    insert_ticket(
        &pool,
        Uuid::from_u128(202),
        user_id,
        "Chrono Q1",
        "chronologie",
        4,
    )
    .await;

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board?category=lore").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let items = body["items"].as_array().expect("items must be array");

    assert_eq!(items.len(), 2, "category=lore must return exactly 2 items");
    assert_eq!(body["total"], 2, "total reflects filtered count");
    for item in items {
        assert_eq!(
            item["category"], "lore",
            "all returned tickets must have category=lore"
        );
    }
}

/// BOARD-001 AFK AC: unknown category returns empty list, not an error.
#[sqlx::test(migrations = "../migrations")]
async fn board_category_filter_unknown_returns_empty(pool: sqlx::PgPool) {
    // BOARD-001 AFK AC: unknown category → empty items, total=0, HTTP 200
    let user_id = Uuid::nil();
    insert_ticket(&pool, Uuid::from_u128(210), user_id, "Lore Q", "lore", 5).await;

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board?category=unknown_category").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let items = body["items"].as_array().expect("items must be array");
    assert_eq!(items.len(), 0, "unknown category must return empty items");
    assert_eq!(body["total"], 0);
}

/// BOARD-001 AFK AC: ?sort=priority orders by priority_score DESC.
#[sqlx::test(migrations = "../migrations")]
async fn board_sort_priority_orders_by_score_desc(pool: sqlx::PgPool) {
    // BOARD-001 AFK AC: sort=priority → items ordered by priority_score DESC
    let user_id = Uuid::nil();
    insert_ticket(&pool, Uuid::from_u128(220), user_id, "Low prio", "lore", 1).await;
    insert_ticket(&pool, Uuid::from_u128(221), user_id, "High prio", "lore", 9).await;
    insert_ticket(&pool, Uuid::from_u128(222), user_id, "Mid prio", "lore", 5).await;

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board?sort=priority").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let items = body["items"].as_array().expect("items must be array");
    assert_eq!(items.len(), 3);

    // sort=priority → priority_score DESC: 9, 5, 1
    assert_eq!(items[0]["priority_score"], 9);
    assert_eq!(items[1]["priority_score"], 5);
    assert_eq!(items[2]["priority_score"], 1);
}

/// BOARD-001 AFK AC: ?sort=date orders by created_at DESC (most recent first).
#[sqlx::test(migrations = "../migrations")]
async fn board_sort_date_orders_by_created_at_desc(pool: sqlx::PgPool) {
    // BOARD-001 AFK AC: sort=date → items ordered by created_at DESC
    let user_id = Uuid::nil();

    // Insert with explicit sleeps not possible; use RETURNING created_at and verify
    // the ordering is consistent with DB order. We insert three tickets and set
    // a pg_sleep to force distinct timestamps.
    // Strategy: insert sequentially and verify the last-inserted appears first.
    sqlx::query(
        "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
    )
    .bind(Uuid::from_u128(230))
    .bind(user_id)
    .bind("gs://archiviste-conversations/conv/230.md")
    .execute(&pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status, created_at) \
         VALUES ($1, $2, $3, $4, 'open', '2024-01-01T00:00:00Z')",
    )
    .bind(Uuid::from_u128(230))
    .bind("Oldest ticket")
    .bind("lore")
    .bind(5_i32)
    .execute(&pool)
    .await
    .unwrap();

    sqlx::query(
        "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
    )
    .bind(Uuid::from_u128(231))
    .bind(user_id)
    .bind("gs://archiviste-conversations/conv/231.md")
    .execute(&pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status, created_at) \
         VALUES ($1, $2, $3, $4, 'open', '2024-06-15T12:00:00Z')",
    )
    .bind(Uuid::from_u128(231))
    .bind("Middle ticket")
    .bind("lore")
    .bind(1_i32)
    .execute(&pool)
    .await
    .unwrap();

    sqlx::query(
        "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
    )
    .bind(Uuid::from_u128(232))
    .bind(user_id)
    .bind("gs://archiviste-conversations/conv/232.md")
    .execute(&pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO tickets (conversation_id, question, category, priority_score, status, created_at) \
         VALUES ($1, $2, $3, $4, 'open', '2025-03-20T08:00:00Z')",
    )
    .bind(Uuid::from_u128(232))
    .bind("Newest ticket")
    .bind("lore")
    .bind(3_i32)
    .execute(&pool)
    .await
    .unwrap();

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board?sort=date").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let items = body["items"].as_array().expect("items must be array");
    assert_eq!(items.len(), 3);

    // sort=date → created_at DESC: newest (2025) first, then 2024-06, then 2024-01
    let first_q = items[0]["question"].as_str().unwrap();
    let last_q = items[2]["question"].as_str().unwrap();
    assert_eq!(
        first_q, "Newest ticket",
        "sort=date: newest ticket must be first"
    );
    assert_eq!(
        last_q, "Oldest ticket",
        "sort=date: oldest ticket must be last"
    );
}

/// BOARD-001 AFK AC: no params → same behaviour as before (backward-compatible).
#[sqlx::test(migrations = "../migrations")]
async fn board_no_params_backward_compatible(pool: sqlx::PgPool) {
    // BOARD-001 AFK AC: omitting category + sort preserves default ordering (priority DESC)
    let user_id = Uuid::nil();
    insert_ticket(&pool, Uuid::from_u128(240), user_id, "Low prio", "lore", 2).await;
    insert_ticket(&pool, Uuid::from_u128(241), user_id, "High prio", "lore", 8).await;

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let items = body["items"].as_array().expect("items must be array");
    assert_eq!(items.len(), 2);

    // Default (no params) → priority_score DESC: 8 first
    assert_eq!(
        items[0]["priority_score"], 8,
        "default ordering must be priority_score DESC"
    );
    assert_eq!(items[1]["priority_score"], 2);
}

/// BOARD-001 AFK AC: ?sort=priority + ?category=lore filters and sorts correctly.
#[sqlx::test(migrations = "../migrations")]
async fn board_category_and_sort_combined(pool: sqlx::PgPool) {
    // BOARD-001 AFK AC: category filter + sort=priority combined
    let user_id = Uuid::nil();
    insert_ticket(&pool, Uuid::from_u128(250), user_id, "Lore low", "lore", 2).await;
    insert_ticket(&pool, Uuid::from_u128(251), user_id, "Lore high", "lore", 7).await;
    insert_ticket(
        &pool,
        Uuid::from_u128(252),
        user_id,
        "Chrono Q",
        "chronologie",
        9,
    )
    .await;

    let state =
        Arc::new(AppState::new_with_pool(make_test_config("http://127.0.0.1:1"), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/board?category=lore&sort=priority").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    let items = body["items"].as_array().expect("items must be array");

    // Only lore tickets, sorted by priority DESC: 7 first
    assert_eq!(items.len(), 2, "must return only lore tickets");
    assert_eq!(body["total"], 2);
    assert_eq!(items[0]["priority_score"], 7);
    assert_eq!(items[1]["priority_score"], 2);
    for item in items {
        assert_eq!(item["category"], "lore");
    }
}
