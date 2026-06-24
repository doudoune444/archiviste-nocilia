//! Integration tests for #283 — `DELETE /v1/conversations/{id}` permanent deletion.
//!
//! Covers the acceptance criteria, all at the HTTP level via `oneshot`, calqués sur
//! `test_hist001_history.rs`:
//!   - owner deletes own conversation → 204; row + its `conversation_messages` gone.
//!   - non-owner OR nonexistent id → 404 indistinct.
//!   - author tier deleting another user's conversation → 404 (no delete privilege).
//!   - conversation referenced by a ticket → 409; row still present.
//!   - GCS-delete trait injected with a test fake recording whether a delete was issued,
//!     asserted per path (issued on 204; not issued on 404/409).
//!   - GCS-delete failure rolls back the Postgres delete (row survives).
//!
//! DB-backed via `#[sqlx::test]` — these provision a throwaway Postgres database and
//! run the real router. They are skipped locally without a DATABASE_URL and run on CI.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::doc_markdown,
    clippy::clone_on_ref_ptr
)]

mod common;
use common::jwt_helpers::sign_test_token;

use archiviste_gateway::{
    auth::extractor::UserTier,
    config::Config,
    gcs::delete::{GcsDeleteError, GcsObjectDeleter},
    router,
    state::AppState,
};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use secrecy::SecretString;
use std::sync::{
    atomic::{AtomicBool, AtomicUsize, Ordering},
    Arc,
};
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// GCS-delete test fake (records whether a delete was issued; can be forced to fail)
// ---------------------------------------------------------------------------

/// Fake `GcsObjectDeleter` that records every delete call and can simulate failure.
///
/// The full DB delete path (204/404/409) is exercised without a real bucket: the
/// handler's GCS seam resolves to this fake, which counts issued deletes so each
/// path can assert whether GCS was reached, and rolls back on a forced failure.
struct RecordingDeleter {
    delete_count: AtomicUsize,
    should_fail: AtomicBool,
}

impl RecordingDeleter {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            delete_count: AtomicUsize::new(0),
            should_fail: AtomicBool::new(false),
        })
    }

    fn failing() -> Arc<Self> {
        Arc::new(Self {
            delete_count: AtomicUsize::new(0),
            should_fail: AtomicBool::new(true),
        })
    }

    fn delete_count(&self) -> usize {
        self.delete_count.load(Ordering::SeqCst)
    }
}

impl GcsObjectDeleter for RecordingDeleter {
    fn delete_object<'a>(
        &'a self,
        _object_path: &'a str,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<(), GcsDeleteError>> + Send + 'a>>
    {
        Box::pin(async move {
            self.delete_count.fetch_add(1, Ordering::SeqCst);
            if self.should_fail.load(Ordering::SeqCst) {
                Err(GcsDeleteError::Status(500))
            } else {
                Ok(())
            }
        })
    }
}

// ---------------------------------------------------------------------------
// Harness helpers (mirror test_hist001_history.rs)
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

fn make_state(pool: sqlx::PgPool, deleter: Arc<dyn GcsObjectDeleter>) -> Arc<AppState> {
    Arc::new(AppState::new_with_pool_and_gcs_deleter(make_config(), pool, deleter).unwrap())
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

async fn delete_with_token(app: axum::Router, uri: &str, token: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("DELETE")
            .uri(uri)
            .header("authorization", format!("Bearer {token}"))
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap()
}

/// Seed a `member`/`author` user with a live session and return `(user_id, token)`.
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

/// Insert a bare `anonymous` user row to own a foreign conversation.
async fn seed_anon_owner(pool: &sqlx::PgPool) -> Uuid {
    let user_id = Uuid::new_v4();
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous')")
        .bind(user_id)
        .execute(pool)
        .await
        .unwrap();
    user_id
}

/// Insert a conversation owned by `owner_id`, returning its id.
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

async fn seed_ticket(pool: &sqlx::PgPool, conv_id: Uuid, status: &str) {
    sqlx::query("INSERT INTO tickets (conversation_id, question, status) VALUES ($1, 'q?', $2)")
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
        sqlx::query_as("SELECT count(*) FROM conversation_messages WHERE conversation_id = $1")
            .bind(conv_id)
            .fetch_one(pool)
            .await
            .unwrap();
    row.0
}

// ---------------------------------------------------------------------------
// AC: owner deletes own conversation → 204; row + messages gone; GCS delete issued
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn del283_owner_deletes_own_conversation_204(pool: sqlx::PgPool) {
    let (owner_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id).await;
    seed_message(&pool, conv, "user", 0, "question").await;
    seed_message(&pool, conv, "assistant", 1, "answer").await;

    let deleter = RecordingDeleter::new();
    let app = router(make_state(pool.clone(), deleter.clone()));
    let resp = delete_with_token(app, &format!("/v1/conversations/{conv}"), &token).await;

    assert_eq!(resp.status(), StatusCode::NO_CONTENT);
    assert!(!conversation_exists(&pool, conv).await, "row must be gone");
    assert_eq!(
        message_count(&pool, conv).await,
        0,
        "conversation_messages must cascade-delete"
    );
    assert_eq!(
        deleter.delete_count(),
        1,
        "GCS delete must be issued on 204"
    );
}

// ---------------------------------------------------------------------------
// AC: non-owner OR nonexistent id → 404 indistinct; no GCS delete issued
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn del283_non_owner_denied_404(pool: sqlx::PgPool) {
    let (_caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id).await;

    let deleter = RecordingDeleter::new();
    let app = router(make_state(pool.clone(), deleter.clone()));
    let resp = delete_with_token(app, &format!("/v1/conversations/{foreign}"), &token).await;

    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_not_found");
    assert!(
        conversation_exists(&pool, foreign).await,
        "foreign conversation must survive"
    );
    assert_eq!(deleter.delete_count(), 0, "no GCS delete on 404");
}

#[sqlx::test(migrations = "../migrations")]
async fn del283_nonexistent_id_404(pool: sqlx::PgPool) {
    let (_caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;

    let deleter = RecordingDeleter::new();
    let app = router(make_state(pool, deleter.clone()));
    let missing = Uuid::new_v4();
    let resp = delete_with_token(app, &format!("/v1/conversations/{missing}"), &token).await;

    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_not_found");
    assert_eq!(deleter.delete_count(), 0, "no GCS delete on 404");
}

// ---------------------------------------------------------------------------
// AC: author tier deleting another user's conversation → 404 (no delete privilege)
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn del283_author_cannot_delete_other_user_404(pool: sqlx::PgPool) {
    let (_author_id, token) = seed_user_with_session(&pool, UserTier::Author).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id).await;

    let deleter = RecordingDeleter::new();
    let app = router(make_state(pool.clone(), deleter.clone()));
    let resp = delete_with_token(app, &format!("/v1/conversations/{foreign}"), &token).await;

    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "author tier has no delete privilege over others' conversations"
    );
    assert!(
        conversation_exists(&pool, foreign).await,
        "foreign conversation must survive"
    );
    assert_eq!(deleter.delete_count(), 0, "no GCS delete on 404");
}

// ---------------------------------------------------------------------------
// AC: conversation referenced by a ticket → 409; row still present; no GCS delete
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn del283_conversation_with_ticket_409(pool: sqlx::PgPool) {
    let (owner_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id).await;
    seed_ticket(&pool, conv, "open").await;

    let deleter = RecordingDeleter::new();
    let app = router(make_state(pool.clone(), deleter.clone()));
    let resp = delete_with_token(app, &format!("/v1/conversations/{conv}"), &token).await;

    assert_eq!(resp.status(), StatusCode::CONFLICT);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_referenced_by_ticket");
    assert!(
        conversation_exists(&pool, conv).await,
        "conversation referenced by a ticket must survive"
    );
    assert_eq!(deleter.delete_count(), 0, "no GCS delete on 409");
}

#[sqlx::test(migrations = "../migrations")]
async fn del283_conversation_with_resolved_ticket_still_409(pool: sqlx::PgPool) {
    let (owner_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id).await;
    seed_ticket(&pool, conv, "resolved").await;

    let deleter = RecordingDeleter::new();
    let app = router(make_state(pool.clone(), deleter.clone()));
    let resp = delete_with_token(app, &format!("/v1/conversations/{conv}"), &token).await;

    assert_eq!(
        resp.status(),
        StatusCode::CONFLICT,
        "a ticket of any status blocks deletion"
    );
    assert!(conversation_exists(&pool, conv).await);
    assert_eq!(deleter.delete_count(), 0);
}

/// Anti-IDOR: a non-owner deleting a foreign conversation that has a ticket gets
/// 404 (not 409) — a non-owner must never learn the conversation exists.
#[sqlx::test(migrations = "../migrations")]
async fn del283_non_owner_ticketed_conversation_is_404_not_409(pool: sqlx::PgPool) {
    let (_caller_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let other_id = seed_anon_owner(&pool).await;
    let foreign = seed_conversation(&pool, other_id).await;
    seed_ticket(&pool, foreign, "open").await;

    let deleter = RecordingDeleter::new();
    let app = router(make_state(pool.clone(), deleter.clone()));
    let resp = delete_with_token(app, &format!("/v1/conversations/{foreign}"), &token).await;

    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "non-owner must get 404 even for a ticketed conversation (no existence leak)"
    );
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_not_found");
    assert!(conversation_exists(&pool, foreign).await);
    assert_eq!(deleter.delete_count(), 0);
}

// ---------------------------------------------------------------------------
// AC: GCS-delete failure rolls back the Postgres delete (row survives)
// ---------------------------------------------------------------------------

#[sqlx::test(migrations = "../migrations")]
async fn del283_gcs_failure_rolls_back_row(pool: sqlx::PgPool) {
    let (owner_id, token) = seed_user_with_session(&pool, UserTier::Member).await;
    let conv = seed_conversation(&pool, owner_id).await;
    seed_message(&pool, conv, "user", 0, "question").await;

    let deleter = RecordingDeleter::failing();
    let app = router(make_state(pool.clone(), deleter.clone()));
    let resp = delete_with_token(app, &format!("/v1/conversations/{conv}"), &token).await;

    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        deleter.delete_count(),
        1,
        "GCS delete must have been attempted"
    );
    assert!(
        conversation_exists(&pool, conv).await,
        "GCS failure must roll back the Postgres delete — row survives"
    );
    assert_eq!(
        message_count(&pool, conv).await,
        1,
        "cascaded messages must survive the rollback too"
    );
}
