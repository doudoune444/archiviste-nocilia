//! In-memory mock implementations of `UserLookup`, `SessionCreator`, and
//! `SessionRevoker` for handler happy-path tests.
//!
//! SEC-001 PR-b M3/N-5 fix: allows exercising signup/login/logout handler
//! paths without a DB.
//!
//! Each integration test binary includes `mod common;` and may be dead from
//! another binary's perspective — the `allow(dead_code)` suppresses the lint.

#![allow(dead_code)]

use archiviste_gateway::auth::{
    sessions::{SessionCreator, SessionError, SessionRevoker},
    user_lookup::{UserLookup, UserLookupError},
};
use std::{collections::HashMap, pin::Pin, sync::Mutex};
use uuid::Uuid;

// ---------------------------------------------------------------------------
// InMemoryUserLookup
// ---------------------------------------------------------------------------

/// Stored user row for test fixtures.
pub struct MockUser {
    pub id: Uuid,
    /// Argon2id PHC string (pre-hashed by caller).
    pub password_hash: String,
    pub tier: String,
}

/// In-memory `UserLookup` for tests.
///
/// Emails are stored in their already-lowercased form (caller normalises before
/// passing to `new_with_user`).
pub struct InMemoryUserLookup {
    /// email (lowercase) → `MockUser`
    users: Mutex<HashMap<String, MockUser>>,
}

impl InMemoryUserLookup {
    /// Create an empty lookup store (no users).
    pub fn empty() -> Self {
        Self {
            users: Mutex::new(HashMap::new()),
        }
    }

    /// Create a store with a single pre-seeded user.
    ///
    /// `email` is stored as-is (caller must normalise to lowercase).
    /// `password_hash` must be a valid PHC argon2id string.
    pub fn with_user(email: &str, id: Uuid, password_hash: String, tier: &str) -> Self {
        let mut map = HashMap::new();
        map.insert(
            email.to_string(),
            MockUser {
                id,
                password_hash,
                tier: tier.to_string(),
            },
        );
        Self {
            users: Mutex::new(map),
        }
    }
}

impl UserLookup for InMemoryUserLookup {
    fn email_is_taken<'a>(
        &'a self,
        email: &'a str,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<bool, UserLookupError>> + Send + 'a>> {
        let lower = email.to_lowercase();
        #[allow(clippy::expect_used)]
        let found = self
            .users
            .lock()
            .expect("mutex poisoned")
            .contains_key(&lower);
        Box::pin(std::future::ready(Ok(found)))
    }

    fn find_member<'a>(
        &'a self,
        email: &'a str,
    ) -> Pin<
        Box<
            dyn std::future::Future<
                    Output = Result<Option<(Uuid, String, String)>, UserLookupError>,
                > + Send
                + 'a,
        >,
    > {
        let lower = email.to_lowercase();
        #[allow(clippy::expect_used)]
        let result = self
            .users
            .lock()
            .expect("mutex poisoned")
            .get(&lower)
            .map(|u| (u.id, u.password_hash.clone(), u.tier.clone()));
        Box::pin(std::future::ready(Ok(result)))
    }

    fn create_member<'a>(
        &'a self,
        email: &'a str,
        password_hash: &'a str,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<Uuid, UserLookupError>> + Send + 'a>> {
        let lower = email.to_lowercase();
        let id = Uuid::new_v4();
        #[allow(clippy::expect_used)]
        self.users.lock().expect("mutex poisoned").insert(
            lower,
            MockUser {
                id,
                password_hash: password_hash.to_string(),
                tier: "member".to_string(),
            },
        );
        Box::pin(std::future::ready(Ok(id)))
    }
}

// ---------------------------------------------------------------------------
// InMemorySessionCreator
// ---------------------------------------------------------------------------

/// In-memory `SessionCreator` for tests that need to exercise the login success path.
///
/// Returns a random `sid` and a dummy `raw_token` without touching the DB.
pub struct InMemorySessionCreator;

impl SessionCreator for InMemorySessionCreator {
    fn create<'a>(
        &'a self,
        _user_id: Uuid,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<(Uuid, String), SessionError>> + Send + 'a>>
    {
        let sid = Uuid::new_v4();
        let raw_token = "mock_raw_token".to_string();
        Box::pin(std::future::ready(Ok((sid, raw_token))))
    }
}

// ---------------------------------------------------------------------------
// InMemorySessionRevoker
// ---------------------------------------------------------------------------

/// In-memory `SessionRevoker` for tests that need to exercise the logout success path (AC-8).
///
/// Records every revoked `sid` in a `Mutex<Vec<Uuid>>` accessible via `revoked_sids()`.
pub struct InMemorySessionRevoker {
    revoked: Mutex<Vec<Uuid>>,
}

impl InMemorySessionRevoker {
    /// Create an empty revoker (no sids revoked yet).
    pub fn new() -> Self {
        Self {
            revoked: Mutex::new(Vec::new()),
        }
    }

    /// Return a snapshot of all sids revoked so far.
    #[allow(clippy::expect_used)]
    pub fn revoked_sids(&self) -> Vec<Uuid> {
        self.revoked.lock().expect("mutex poisoned").clone()
    }
}

impl SessionRevoker for InMemorySessionRevoker {
    fn revoke<'a>(
        &'a self,
        sid: Uuid,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<(), SessionError>> + Send + 'a>>
    {
        #[allow(clippy::expect_used)]
        self.revoked.lock().expect("mutex poisoned").push(sid);
        Box::pin(std::future::ready(Ok(())))
    }
}
