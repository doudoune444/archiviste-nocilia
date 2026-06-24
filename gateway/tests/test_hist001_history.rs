//! Integration tests for HIST-001 — owner-scoped conversation history + signed-url IDOR.
//!
//! Covers the four acceptance criteria:
//!   - A caller lists only their own conversations.
//!   - A cross-owner read is denied (signed-url IDOR closed; messages IDOR closed).
//!   - Reopening a conversation returns its turns from the structured store.
//!   - History is owner-scoped to the durable identity (member `sub` / anon cookie).
//!
//! DB-backed via `#[sqlx::test]` — these provision a throwaway Postgres database and
//! run the real router. They are skipped locally without a DATABASE_URL and run on CI.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::doc_markdown)]

mod common;
use common::jwt_helpers::sign_test_token;

use archiviste_gateway::{auth::extractor::UserTier, config::Config, router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use secrecy::SecretString;
use std::sync::Arc;
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Harness helpers (mirror test_dashboard_backend.rs)
// ---------------------------------------------------------------------------

fn make_config() -> Config {
    use common::jwt_helpers::{test_private_key_pem, test_public_key_pem, TEST_KEY_ID};
    Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: "http://127.0.0.1:1".to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        jwt_ed25519_private_key_pem: SecretString::from(test_private_key_pem().to_string()),
        jwt_kid: TEST_KEY_ID.to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 5_000,
        chat_request_timeout_ms: 90_000,
        gcs_signing_sa_email: "archiviste-runtime@project.iam.gserviceaccount.com".to_string(),
        gcs_bucket: "archiviste-conversations".to_string(),
        cloud_run_service_url: "http://127.0.0.1:1".to_string(),
        cost_tariffs: Some(archiviste_gateway::config::CostTariffs::default()),
    }
}

fn make_state_with_pool(pool: sqlx::PgPool) -> Arc<AppState> {
    Arc::new(AppState::new_with_pool(make_config(), pool).unwrap())
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

async fn get_with_token(
    app: axum::Router,
    uri: &str,
    token: Option<&str>,
) -> axum::response::Response {
    let mut builder = Request::builder().method("GET").uri(uri);
    if let Some(t) = token {
        builder = builder.header("authorization", format!("Bearer {t}"));
    }
    app.oneshot(builder.body(Body::empty()).unwrap())
        .await
        .unwrap()
}

/// Seed a `member` (or `author`) user with a live session and return `(user_id, token)`.
///
/// A live pool activates the AC-13 server-side session check, so the JWT must be
/// paired with a seeded `sessions` row. `member`/`author` tiers require email +
/// password_hash (users_auth_consistency CHECK).
async fn seed_user_with_session(pool: &sqlx::PgPool, tier: UserTier) -> (Uuid, String) {
    let user_id = Uuid::new_v4();
    let sid = Uuid::new_v4();
    sqlx::query("INSERT INTO users (id, tier, email, password_hash) VALUES ($1, $2, $3, 'x')")
        .bind(user_id)
        .bind(tier.as_str())
        .bind(format!("{user_id}@test.local"))
        .execute(pool)
        .await
        .unwrap();
    sqlx::query(
        "INSERT INTO sessions (id, user_id, token_hash, expires_at) \
         VALUES ($1, $2, 'x', NOW() + interval '1 hour')",
    )
    .bind(sid)
    .bind(user_id)
    .execute(pool)
    .await
    .unwrap();
    (user_id, sign_test_token(user_id, tier, sid))
}

/// Insert a bare `anonymous` user row to own a conversation (a different visitor).
async fn seed_anon_owner(pool: &sqlx::PgPool) -> Uuid {
    let user_id = Uuid::new_v4();
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous')")
        .bind(user_id)
        .execute(pool)
        .await
        .unwrap();
    user_id
}

/// Insert a conversation owned by `owner_id` with an explicit `updated_at` (list order).
async fn seed_conversation(
    pool: &sqlx::PgPool,
    owner_id: Uuid,
    message_count: i32,
    updated_at: chrono::DateTime<chrono::Utc>,
) -> Uuid {
    let conv_id = Uuid::new_v4();
    let gcs_uri = format!("gs://archiviste-conversations/conv/{conv_id}.md");
    sqlx::query(
        "INSERT INTO conversations (id, user_id, gcs_uri, message_count, created_at, updated_at) \
         VALUES ($1, $2, $3, $4, $5, $5)",
    )
    .bind(conv_id)
    .bind(owner_id)
    .bind(&gcs_uri)
    .bind(message_count)
    .bind(updated_at)
    .execute(pool)
    .await
    .unwrap();
    conv_id
}

async fn seed_message(pool: &sqlx::PgPool, conv_id: Uuid, role: &str, ordinal: i32, content: &str) {
    sqlx::query(
        "INSERT INTO conversation_messages (conversation_id, role, ordinal, content, token_count) \
         VALUES ($1, $2, $3, $4, 1)",
    )
    .bind(conv_id)
    .bind(role)
    .bind(ordinal)
    .bind(content)
    .execute(pool)
    .await
    .unwrap();
}

fn ts(secs_ago: i64) -> chrono::DateTime<chrono::Utc> {
    chrono::Utc::now() - chrono::Duration::seconds(secs_ago)
}

// ---------------------------------------------------------------------------
// AC: a caller lists only their own conversations
// ---------------------------------------------------------------------------

/// Member sees exactly their two conversations, newest-activity first, never the
/// other owner's conversation.
#[sqlx::test(migrations = "../migrations")]
async fn hist001_list_returns_only_own_conversations(pool: sqlx::PgPool) {
    let (caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let other_id = seed_anon_owner(&pool).await;

    let older = seed_conversation(&pool, caller_id, 2, ts(120)).await;
    let newer = seed_conversation(&pool, caller_id, 4, ts(10)).await;
    let _foreign = seed_conversation(&pool, other_id, 6, ts(5)).await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(app, "/v1/conversations", Some(&token)).await;
    assert_eq!(resp.status(), StatusCode::OK);

    let body = body_json(resp).await;
    let items = body["conversations"].as_array().unwrap();
    assert_eq!(
        items.len(),
        2,
        "caller must see only their own 2 conversations"
    );
    // ORDER BY updated_at DESC → newer first.
    assert_eq!(items[0]["id"], newer.to_string());
    assert_eq!(items[1]["id"], older.to_string());
    assert_eq!(items[0]["message_count"], 4);
}

/// A caller with no conversations gets an empty list (not an error).
#[sqlx::test(migrations = "../migrations")]
async fn hist001_list_empty_for_new_caller(pool: sqlx::PgPool) {
    let (_caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(app, "/v1/conversations", Some(&token)).await;
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    assert_eq!(body["conversations"].as_array().unwrap().len(), 0);
}

// ---------------------------------------------------------------------------
// #250 — derived history titles (first user message, truncated on UTF-8 boundary)
// ---------------------------------------------------------------------------

/// A conversation's `title` equals the start of its first user message (minimal
/// ordinal among `role = 'user'` rows), independent of an earlier non-user turn.
#[sqlx::test(migrations = "../migrations")]
async fn hist250_title_is_first_user_message(pool: sqlx::PgPool) {
    let (caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, caller_id, 3, ts(30)).await;
    seed_message(&pool, conv, "user", 0, "Quelle est la capitale ?").await;
    seed_message(&pool, conv, "assistant", 1, "Paris.").await;
    seed_message(&pool, conv, "user", 2, "Et le fleuve ?").await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(app, "/v1/conversations", Some(&token)).await;
    assert_eq!(resp.status(), StatusCode::OK);

    let body = body_json(resp).await;
    let items = body["conversations"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["title"], "Quelle est la capitale ?");
}

/// A title longer than the limit is truncated on a UTF-8 boundary with a `…` suffix.
/// The accented multibyte content must never be split mid-codepoint.
#[sqlx::test(migrations = "../migrations")]
async fn hist250_title_truncated_on_utf8_boundary(pool: sqlx::PgPool) {
    let (caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, caller_id, 1, ts(30)).await;
    // 70 accented (multibyte) chars — exceeds the ~60 char cap.
    let long_question: String = "é".repeat(70);
    seed_message(&pool, conv, "user", 0, &long_question).await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(app, "/v1/conversations", Some(&token)).await;
    assert_eq!(resp.status(), StatusCode::OK);

    let body = body_json(resp).await;
    let title = body["conversations"][0]["title"].as_str().unwrap();
    assert!(title.ends_with('…'), "long title must end with an ellipsis");
    let visible_chars = title.trim_end_matches('…').chars().count();
    assert_eq!(visible_chars, 60, "title capped at 60 visible chars");
    assert!(
        title.trim_end_matches('…').chars().all(|c| c == 'é'),
        "truncation must not split a multibyte codepoint"
    );
}

/// A conversation with no user message yet yields an empty title (not an error).
#[sqlx::test(migrations = "../migrations")]
async fn hist250_title_empty_when_no_user_message(pool: sqlx::PgPool) {
    let (caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, caller_id, 1, ts(30)).await;
    seed_message(&pool, conv, "assistant", 0, "Bonjour.").await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(app, "/v1/conversations", Some(&token)).await;
    assert_eq!(resp.status(), StatusCode::OK);

    let body = body_json(resp).await;
    assert_eq!(body["conversations"][0]["title"], "");
}

/// Titles are owner-scoped: a caller never sees another owner's conversation title.
#[sqlx::test(migrations = "../migrations")]
async fn hist250_title_owner_scoped(pool: sqlx::PgPool) {
    let (caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let other_id = seed_anon_owner(&pool).await;

    let own = seed_conversation(&pool, caller_id, 1, ts(30)).await;
    seed_message(&pool, own, "user", 0, "Ma question à moi").await;
    let foreign = seed_conversation(&pool, other_id, 1, ts(5)).await;
    seed_message(&pool, foreign, "user", 0, "Secret de l'autre").await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(app, "/v1/conversations", Some(&token)).await;
    assert_eq!(resp.status(), StatusCode::OK);

    let body = body_json(resp).await;
    let items = body["conversations"].as_array().unwrap();
    assert_eq!(items.len(), 1, "caller sees only their own conversation");
    assert_eq!(items[0]["title"], "Ma question à moi");
}

// ---------------------------------------------------------------------------
// AC: reopening a conversation returns its turns from the structured store
// ---------------------------------------------------------------------------

/// Owner reads their conversation's turns, ascending by ordinal.
#[sqlx::test(migrations = "../migrations")]
async fn hist001_messages_owner_returns_turns_in_order(pool: sqlx::PgPool) {
    let (caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, caller_id, 3, ts(30)).await;
    seed_message(&pool, conv, "user", 0, "première question").await;
    seed_message(&pool, conv, "assistant", 1, "première réponse").await;
    seed_message(&pool, conv, "user", 2, "seconde question").await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(
        app,
        &format!("/v1/conversations/{conv}/messages"),
        Some(&token),
    )
    .await;
    assert_eq!(resp.status(), StatusCode::OK);

    let body = body_json(resp).await;
    assert_eq!(body["conversation_id"], conv.to_string());
    let messages = body["messages"].as_array().unwrap();
    assert_eq!(messages.len(), 3);
    assert_eq!(messages[0]["ordinal"], 0);
    assert_eq!(messages[0]["role"], "user");
    assert_eq!(messages[0]["content"], "première question");
    assert_eq!(messages[2]["ordinal"], 2);
    assert_eq!(messages[2]["content"], "seconde question");
}

// ---------------------------------------------------------------------------
// AC: a cross-owner read is denied (IDOR closed)
// ---------------------------------------------------------------------------

/// A caller reading another owner's conversation messages gets 404 — no turns leak.
#[sqlx::test(migrations = "../migrations")]
async fn hist001_messages_cross_owner_denied_404(pool: sqlx::PgPool) {
    let (_caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id, 2, ts(30)).await;
    seed_message(&pool, foreign, "user", 0, "secret de l'autre").await;
    seed_message(&pool, foreign, "assistant", 1, "réponse privée").await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(
        app,
        &format!("/v1/conversations/{foreign}/messages"),
        Some(&token),
    )
    .await;
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "cross-owner read must be denied"
    );
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_not_found");
}

/// A non-author caller requesting another owner's signed-url gets 404 — IDOR closed.
#[sqlx::test(migrations = "../migrations")]
async fn hist001_signed_url_cross_owner_denied_404(pool: sqlx::PgPool) {
    let (_caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id, 1, ts(30)).await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(
        app,
        &format!("/v1/conversations/{foreign}/signed-url"),
        Some(&token),
    )
    .await;
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "non-owner signed-url must be 404 (IDOR closed), never reach signing"
    );
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_not_found");
}

/// The owner of a conversation passes the ownership guard and reaches GCS signing
/// (200 if IAM reachable, else 503 in a network-isolated runner) — never 404/403.
#[sqlx::test(migrations = "../migrations")]
async fn hist001_signed_url_owner_passes_ownership(pool: sqlx::PgPool) {
    let (caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let own = seed_conversation(&pool, caller_id, 1, ts(30)).await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(
        app,
        &format!("/v1/conversations/{own}/signed-url"),
        Some(&token),
    )
    .await;
    let status = resp.status().as_u16();
    assert!(
        status == 200 || status == 503,
        "owner must pass ownership (200 signed or 503 IAM-unreachable), got {status}"
    );
}

/// An author moderating any conversation passes the ownership guard even when they
/// do not own it — the moderation dashboard path is preserved.
#[sqlx::test(migrations = "../migrations")]
async fn hist001_signed_url_author_reads_any(pool: sqlx::PgPool) {
    let (_author_id, token) = seed_user_with_session(&pool, UserTier::Author).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id, 1, ts(30)).await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(
        app,
        &format!("/v1/conversations/{foreign}/signed-url"),
        Some(&token),
    )
    .await;
    let status = resp.status().as_u16();
    assert!(
        status == 200 || status == 503,
        "author must read any conversation (200 signed or 503 IAM-unreachable), got {status}"
    );
}

// ---------------------------------------------------------------------------
// #161 — reload regression: backend contract the JS reload path relies on
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// DASH-002 AC: author may read any conversation's turns (owner-or-author)
// ---------------------------------------------------------------------------

/// DASH-002 AC: author calling /messages for a conversation owned by a DIFFERENT user
/// → 200 with turns in ordinal order (moderation dashboard read).
///
/// Non-author tier is unchanged — the AC for that (non-owner → 404) is tested
/// in `hist001_messages_cross_owner_denied_404` below.
#[sqlx::test(migrations = "../migrations")]
async fn dash002_messages_author_reads_other_user_conversation(pool: sqlx::PgPool) {
    // DASH-002 AC: author may read any conversation for moderation.
    let (_author_id, author_token) = seed_user_with_session(&pool, UserTier::Author).await;
    let other_id = seed_anon_owner(&pool).await;
    let conv = seed_conversation(&pool, other_id, 2, ts(30)).await;
    seed_message(&pool, conv, "user", 0, "question modérée").await;
    seed_message(&pool, conv, "assistant", 1, "réponse modérée").await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(
        app,
        &format!("/v1/conversations/{conv}/messages"),
        Some(&author_token),
    )
    .await;
    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "author must read any conversation for moderation dashboard (DASH-002)"
    );

    let body = body_json(resp).await;
    assert_eq!(body["conversation_id"], conv.to_string());
    let messages = body["messages"].as_array().unwrap();
    assert_eq!(messages.len(), 2, "author must see both turns");
    // Turns returned in ordinal order (ascending).
    assert_eq!(messages[0]["ordinal"], 0);
    assert_eq!(messages[0]["role"], "user");
    assert_eq!(messages[0]["content"], "question modérée");
    assert_eq!(messages[1]["ordinal"], 1);
    assert_eq!(messages[1]["role"], "assistant");
}

/// DASH-002 AC: non-author, non-owner calling /messages → 404 (IDOR preserved).
/// Indistinguishable from a non-existent conversation.
#[sqlx::test(migrations = "../migrations")]
async fn dash002_messages_non_author_non_owner_denied_404(pool: sqlx::PgPool) {
    // DASH-002 AC: non-author non-owner → 404, existence hidden (security.md A01 IDOR).
    let (_caller_id, caller_token) = seed_user_with_session(&pool, UserTier::Member).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id, 1, ts(30)).await;
    seed_message(&pool, foreign, "user", 0, "secret").await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(
        app,
        &format!("/v1/conversations/{foreign}/messages"),
        Some(&caller_token),
    )
    .await;
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "non-author non-owner must get 404 (IDOR preserved)"
    );
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_not_found");
}

/// DASH-002 AC (no regression): owner (non-author) reading their own conversation → 200.
#[sqlx::test(migrations = "../migrations")]
async fn dash002_messages_owner_non_author_reads_own_200(pool: sqlx::PgPool) {
    // DASH-002 AC: owner (non-author tier) must still read their own conversation.
    let (owner_id, owner_token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id, 1, ts(30)).await;
    seed_message(&pool, conv, "user", 0, "ma question").await;

    let app = router(make_state_with_pool(pool));
    let resp = get_with_token(
        app,
        &format!("/v1/conversations/{conv}/messages"),
        Some(&owner_token),
    )
    .await;
    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "owner (non-author) must still read their own conversation (no regression)"
    );
    let body = body_json(resp).await;
    let messages = body["messages"].as_array().unwrap();
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0]["content"], "ma question");
}

// ---------------------------------------------------------------------------
// #161 — reload regression: backend contract the JS reload path relies on
// ---------------------------------------------------------------------------

/// #161 AC: on page reload, `reopenConversation(activeId)` in app.js calls
/// GET /v1/conversations/{id}/messages as the owner and expects 200 + ordered turns.
/// This test documents the server contract that makes the reload flow safe:
///   - owner reload → 200 with turns in ordinal order (JS renders them)
///   - non-owner id in localStorage → 404 (server enforces; JS no-ops on !response.ok)
///
/// Both assertions already exist individually above; this test pins them together
/// as an explicit reload-contract regression guard so future refactors cannot
/// accidentally break one without the other.
#[sqlx::test(migrations = "../migrations")]
async fn hist161_reload_contract_owner_200_nonowner_404(pool: sqlx::PgPool) {
    let (owner_id, owner_token) = seed_user_with_session(&pool, UserTier::Member).await;
    let (_other_id, other_token) = seed_user_with_session(&pool, UserTier::Member).await;

    let conv = seed_conversation(&pool, owner_id, 2, ts(60)).await;
    seed_message(&pool, conv, "user", 0, "reload question").await;
    seed_message(&pool, conv, "assistant", 1, "reload answer").await;

    let app = router(make_state_with_pool(pool));
    let uri = format!("/v1/conversations/{conv}/messages");

    // AC: owner reload → 200 with turns in ordinal order.
    let resp = get_with_token(app.clone(), &uri, Some(&owner_token)).await;
    assert_eq!(resp.status(), StatusCode::OK, "owner reload must be 200");
    let body = body_json(resp).await;
    let messages = body["messages"].as_array().unwrap();
    assert_eq!(messages.len(), 2, "owner reload must return both turns");
    assert_eq!(messages[0]["ordinal"], 0);
    assert_eq!(messages[0]["content"], "reload question");
    assert_eq!(messages[1]["ordinal"], 1);
    assert_eq!(messages[1]["content"], "reload answer");

    // AC: non-owner id in localStorage → 404; JS `reopenConversation` no-ops on !response.ok.
    let resp = get_with_token(app, &uri, Some(&other_token)).await;
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "non-owner reload must be 404 so JS no-ops gracefully"
    );
}
