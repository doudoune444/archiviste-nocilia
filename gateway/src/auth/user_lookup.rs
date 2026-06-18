//! `UserLookup` trait — abstraction over DB user queries for testability.
//!
//! The production implementation `PgUserLookup` wraps a `PgPool`.
//! Tests inject `InMemoryUserLookup` to exercise handler happy paths without a DB.
//!
//! SEC-001 PR-b (M3 fix — reviewer request).

use std::pin::Pin;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

/// Error returned by `UserLookup` operations.
#[derive(Debug)]
pub enum UserLookupError {
    /// Database or internal failure.
    Unavailable,
}

// ---------------------------------------------------------------------------
// Type aliases for dyn-safe future return types
// ---------------------------------------------------------------------------

type BoolFuture<'a> =
    Pin<Box<dyn std::future::Future<Output = Result<bool, UserLookupError>> + Send + 'a>>;

type MemberFuture<'a> = Pin<
    Box<
        dyn std::future::Future<Output = Result<Option<(Uuid, String, String)>, UserLookupError>>
            + Send
            + 'a,
    >,
>;

type UuidFuture<'a> =
    Pin<Box<dyn std::future::Future<Output = Result<Uuid, UserLookupError>> + Send + 'a>>;

/// Boxed future returning an optional email string.
pub type EmailFuture<'a> =
    Pin<Box<dyn std::future::Future<Output = Result<Option<String>, UserLookupError>> + Send + 'a>>;

// ---------------------------------------------------------------------------
// Trait
// ---------------------------------------------------------------------------

/// Abstraction over DB user queries used by signup and login handlers.
///
/// Implemented as an object-safe async trait using boxed `Future` returns
/// to support `dyn UserLookup` (native `async fn in trait` is not yet dyn-safe
/// on Rust 1.95 stable).
pub trait UserLookup: Send + Sync {
    /// Return `true` if the email is already taken (case-insensitive).
    ///
    /// # Errors
    ///
    /// Returns `UserLookupError::Unavailable` on DB failure.
    fn email_is_taken<'a>(&'a self, email: &'a str) -> BoolFuture<'a>;

    /// Fetch `(user_id, password_hash, tier)` for the given email, or `None` if absent.
    ///
    /// # Errors
    ///
    /// Returns `UserLookupError::Unavailable` on DB failure.
    fn find_member<'a>(&'a self, email: &'a str) -> MemberFuture<'a>;

    /// Insert a new member user and return the generated `user_id`.
    ///
    /// # Errors
    ///
    /// Returns `UserLookupError::Unavailable` on DB failure.
    fn create_member<'a>(&'a self, email: &'a str, password_hash: &'a str) -> UuidFuture<'a>;

    /// Fetch the email address for `user_id`, or `None` if the user does not exist.
    ///
    /// Used by `GET /v1/me` to populate the `email` field for member/author tiers.
    ///
    /// # Errors
    ///
    /// Returns `UserLookupError::Unavailable` on DB failure.
    fn find_email_by_id(&self, user_id: Uuid) -> EmailFuture<'_>;
}

// ---------------------------------------------------------------------------
// Production implementation
// ---------------------------------------------------------------------------

/// Production `UserLookup` backed by a `PgPool`.
pub struct PgUserLookup(pub sqlx::PgPool);

impl UserLookup for PgUserLookup {
    fn email_is_taken<'a>(&'a self, email: &'a str) -> BoolFuture<'a> {
        Box::pin(async move {
            let existing: Option<(Uuid,)> =
                sqlx::query_as("SELECT id FROM users WHERE LOWER(email) = LOWER($1)")
                    .bind(email)
                    .fetch_optional(&self.0)
                    .await
                    .map_err(|_| UserLookupError::Unavailable)?;
            Ok(existing.is_some())
        })
    }

    fn find_member<'a>(&'a self, email: &'a str) -> MemberFuture<'a> {
        Box::pin(async move {
            let row: Option<(Uuid, Option<String>, String)> = sqlx::query_as(
                "SELECT id, password_hash, tier FROM users \
                 WHERE LOWER(email) = LOWER($1) AND tier != 'anonymous'",
            )
            .bind(email)
            .fetch_optional(&self.0)
            .await
            .map_err(|_| UserLookupError::Unavailable)?;

            Ok(row.and_then(|(id, hash, tier)| hash.map(|h| (id, h, tier))))
        })
    }

    fn create_member<'a>(&'a self, email: &'a str, password_hash: &'a str) -> UuidFuture<'a> {
        Box::pin(async move {
            let user_id = Uuid::new_v4();
            sqlx::query(
                // AC-15: tier='member' is a literal — no runtime value can produce 'author'.
                "INSERT INTO users (id, email, password_hash, tier) VALUES ($1, $2, $3, 'member')",
            )
            .bind(user_id)
            .bind(email)
            .bind(password_hash)
            .execute(&self.0)
            .await
            .map_err(|_| UserLookupError::Unavailable)?;
            Ok(user_id)
        })
    }

    fn find_email_by_id(&self, user_id: Uuid) -> EmailFuture<'_> {
        Box::pin(async move {
            let row: Option<(String,)> = sqlx::query_as("SELECT email FROM users WHERE id = $1")
                .bind(user_id)
                .fetch_optional(&self.0)
                .await
                .map_err(|_| UserLookupError::Unavailable)?;
            Ok(row.map(|(email,)| email))
        })
    }
}
