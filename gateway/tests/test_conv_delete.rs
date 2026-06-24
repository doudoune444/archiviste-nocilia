//! Integration tests for #283 — `DELETE /v1/conversations/{id}` permanent deletion.
//!
//! Covers the acceptance criteria:
//!   - Owner deletes own conversation → 204; row + its `conversation_messages` gone.
//!   - Another user's conversation OR a nonexistent id → 404 (indistinct, anti-IDOR).
//!   - Author tier deleting another user's conversation → 404 (no delete privilege).
//!   - Conversation referenced by a ticket → 409, conversation still present.
//!   - GCS-delete failure rolls back the Postgres delete (row survives).
//!   - The recording GCS fake is issued on 204, not issued on 404/409.
//!
//! DB-backed via `#[sqlx::test]` (throwaway Postgres) + `oneshot`, mirroring
//! `test_hist001_history.rs`. Skipped locally without a DATABASE_URL; run on CI.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::doc_markdown)]

mod common;
use common::jwt_helpers::sign_test_token;

use archiviste_gateway::{
    auth::extractor::UserTier,
    config::Config,
    gcs::delete::{GcsObjectDeleter, RecordingGcsDeleter},
    router,
    state::AppState,
};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use secrecy::SecretString;
use std::sync::Arc;
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Harness
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
        gcs_signing_sa_email: "archiviste-runtime@project.iam.gserviceaccount.com".to_string(),
        gcs_bucket: "archiviste-conversations".to_string(),
        cloud_run_service_url: "http://127.0.0.1:1".to_string(),
        cost_tariffs: Some(archiviste_gateway::config::CostTariffs::default()),
    }
}

/// Build the router over a real pool with an injected GCS-delete fake, returning
/// the fake so a test can assert whether a delete was issued.
fn app_with_deleter(pool: sqlx::PgPool, deleter: &Arc<RecordingGcsDeleter>) -> axum::Router {
    let seam: Arc<dyn GcsObjectDeleter> = Arc::clone(deleter) as Arc<dyn GcsObjectDeleter>;
    let state =
        Arc::new(AppState::new_with_pool_and_gcs_deleter(make_config(), pool, seam).unwrap());
    router(state)
}

async fn delete_with_token(app: axum::Router, id: Uuid, token: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("DELETE")
            .uri(format!("/v1/conversations/{id}"))
            .header("authorization", format!("Bearer {token}"))
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap()
}

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

async fn seed_anon_owner(pool: &sqlx::PgPool) -> Uuid {
    let user_id = Uuid::new_v4();
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous')")
        .bind(user_id)
        .execute(pool)
        .await
        .unwrap();
    user_id
}

async fn seed_conversation(pool: &sqlx::PgPool, owner_id: Uuid) -> Uuid {
    let conv_id = Uuid::new_v4();
    let gcs_uri = format!("gs://archiviste-conversations/conv/{conv_id}.md");
    sqlx::query(
        "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
    )
    .bind(conv_id)
    .bind(owner_id)
    .bind(&gcs_uri)
    .execute(pool)
    .await
    .unwrap();
    conv_id
}

async fn seed_message(pool: &sqlx::PgPool, conv_id: Uuid, ordinal: i32) {
    sqlx::query(
        "INSERT INTO conversation_messages (conversation_id, role, ordinal, content, token_count) \
         VALUES ($1, 'user', $2, 'contenu', 1)",
    )
    .bind(conv_id)
    .bind(ordinal)
    .execute(pool)
    .await
    .unwrap();
}

async fn seed_ticket(pool: &sqlx::PgPool, conv_id: Uuid, status: &str) {
    sqlx::query("INSERT INTO tickets (conversation_id, question, status) VALUES ($1, 'q', $2)")
        .bind(conv_id)
        .bind(status)
        .execute(pool)
        .await
        .unwrap();
}

async fn conversation_exists(pool: &sqlx::PgPool, conv_id: Uuid) -> bool {
    let row: (bool,) = sqlx::query_as("SELECT EXISTS(SELECT 1 FROM conversations WHERE id = $1)")
        .bind(conv_id)
        .fetch_one(pool)
        .await
        .unwrap();
    row.0
}

async fn message_count(pool: &sqlx::PgPool, conv_id: Uuid) -> i64 {
    let row: (i64,) =
        sqlx::query_as("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = $1")
            .bind(conv_id)
            .fetch_one(pool)
            .await
            .unwrap();
    row.0
}

// ---------------------------------------------------------------------------
// AC: owner deletes own conversation → 204; row + messages gone; GCS issued.
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn delete_owner_succeeds_204_and_removes_row_and_messages(pool: sqlx::PgPool) {
    let (owner_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id).await;
    seed_message(&pool, conv, 0).await;
    seed_message(&pool, conv, 1).await;

    let deleter = Arc::new(RecordingGcsDeleter::succeeding());
    let app = app_with_deleter(pool.clone(), &deleter);
    let resp = delete_with_token(app, conv, &token).await;

    assert_eq!(resp.status(), StatusCode::NO_CONTENT);
    assert!(!conversation_exists(&pool, conv).await, "row must be gone");
    assert_eq!(
        message_count(&pool, conv).await,
        0,
        "messages must follow via ON DELETE CASCADE"
    );
    assert!(deleter.was_issued(), "GCS delete must be issued on 204");
}

// ---------------------------------------------------------------------------
// AC: another user's conversation → 404 (indistinct), GCS not issued, row survives.
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn delete_other_users_conversation_404(pool: sqlx::PgPool) {
    let (_caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id).await;

    let deleter = Arc::new(RecordingGcsDeleter::succeeding());
    let app = app_with_deleter(pool.clone(), &deleter);
    let resp = delete_with_token(app, foreign, &token).await;

    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    assert!(
        conversation_exists(&pool, foreign).await,
        "foreign row must survive a 404"
    );
    assert!(
        !deleter.was_issued(),
        "GCS delete must NOT be issued on 404"
    );
}

#[sqlx::test(migrations = "../migrations")]
async fn delete_nonexistent_conversation_404(pool: sqlx::PgPool) {
    let (_caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;

    let deleter = Arc::new(RecordingGcsDeleter::succeeding());
    let app = app_with_deleter(pool, &deleter);
    let resp = delete_with_token(app, Uuid::new_v4(), &token).await;

    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    assert!(
        !deleter.was_issued(),
        "GCS delete must NOT be issued on 404"
    );
}

// ---------------------------------------------------------------------------
// AC: author tier deleting another user's conversation → 404 (no delete privilege).
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn delete_author_other_users_conversation_404(pool: sqlx::PgPool) {
    let (_author_id, token) = seed_user_with_session(&pool, UserTier::Author).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id).await;

    let deleter = Arc::new(RecordingGcsDeleter::succeeding());
    let app = app_with_deleter(pool.clone(), &deleter);
    let resp = delete_with_token(app, foreign, &token).await;

    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "author has read-for-moderation but NO delete privilege (#283)"
    );
    assert!(
        conversation_exists(&pool, foreign).await,
        "row must survive"
    );
    assert!(
        !deleter.was_issued(),
        "GCS delete must NOT be issued on 404"
    );
}

// ---------------------------------------------------------------------------
// AC: conversation referenced by a ticket → 409, conversation still present.
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn delete_conversation_with_ticket_409(pool: sqlx::PgPool) {
    let (owner_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id).await;
    seed_ticket(&pool, conv, "open").await;

    let deleter = Arc::new(RecordingGcsDeleter::succeeding());
    let app = app_with_deleter(pool.clone(), &deleter);
    let resp = delete_with_token(app, conv, &token).await;

    assert_eq!(resp.status(), StatusCode::CONFLICT);
    let bytes = http_body_util::BodyExt::collect(resp.into_body())
        .await
        .unwrap()
        .to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["error"], "conversation_referenced_by_ticket");
    assert!(body.get("request_id").is_some(), "409 carries a request_id");
    assert!(
        body.get("title").is_none() && body.get("content").is_none(),
        "409 body must not carry conversation content"
    );

    assert!(
        conversation_exists(&pool, conv).await,
        "row must survive 409"
    );
    assert!(
        !deleter.was_issued(),
        "GCS delete must NOT be issued on 409"
    );
}

#[sqlx::test(migrations = "../migrations")]
async fn delete_conversation_with_resolved_ticket_409(pool: sqlx::PgPool) {
    let (owner_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id).await;
    seed_ticket(&pool, conv, "resolved").await;

    let deleter = Arc::new(RecordingGcsDeleter::succeeding());
    let app = app_with_deleter(pool.clone(), &deleter);
    let resp = delete_with_token(app, conv, &token).await;

    assert_eq!(
        resp.status(),
        StatusCode::CONFLICT,
        "a ticket in ANY status blocks deletion"
    );
    assert!(conversation_exists(&pool, conv).await);
}

// ---------------------------------------------------------------------------
// AC: GCS-delete failure rolls back the Postgres delete (row survives).
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn delete_gcs_failure_rolls_back_postgres(pool: sqlx::PgPool) {
    let (owner_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id).await;
    seed_message(&pool, conv, 0).await;

    let deleter = Arc::new(RecordingGcsDeleter::failing());
    let app = app_with_deleter(pool.clone(), &deleter);
    let resp = delete_with_token(app, conv, &token).await;

    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert!(
        deleter.was_issued(),
        "GCS delete was attempted before rollback"
    );
    assert!(
        conversation_exists(&pool, conv).await,
        "GCS failure must ROLLBACK the Postgres delete — row survives"
    );
    assert_eq!(
        message_count(&pool, conv).await,
        1,
        "cascaded messages must also survive the rollback"
    );
}
