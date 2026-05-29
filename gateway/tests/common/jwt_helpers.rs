//! Runtime-generated Ed25519 keypair for test JWT signing.
//!
//! Keys are generated once per test process via `OnceLock` and never committed
//! to source — no hardcoded PEM constants (security.md § Forbidden patterns).
//!
//! # Usage
//! Add `mod common;` + `use common::jwt_helpers::*;` at the top of each test file.
//!
//! # Config helper
//! Use `make_test_config` / `make_test_config_with_url` to build `Config` structs
//! in tests without duplicating field initializers (GCS fields added by UI-002 PR1).

// Each integration test binary includes this module independently;
// clippy analyses per-binary and may flag items unused in that binary.
#![allow(dead_code)]

use archiviste_gateway::{
    auth::{extractor::UserTier, jwt::JWT_ISSUER_AUDIENCE},
    config::Config,
};
use chrono::{DateTime, Utc};
use ed25519_dalek::SigningKey;
use jsonwebtoken::EncodingKey;
use pkcs8::{EncodePrivateKey, EncodePublicKey, LineEnding};
use rand_core::OsRng;
use std::sync::OnceLock;
use uuid::Uuid;

/// Key ID embedded in test JWTs (fixed string — not a secret).
pub const TEST_KEY_ID: &str = "test-key-runtime";

// ---------------------------------------------------------------------------
// Cached keypair
// ---------------------------------------------------------------------------

struct TestKeypair {
    encoding_key: EncodingKey,
    public_key_pem: String,
    private_key_pem: String,
}

/// Generate an Ed25519 keypair and return an `EncodingKey` + public PEM.
///
/// Panics on internal crypto / encoding failures — a broken test setup is a
/// build-time defect, not a runtime error. `expect` is permitted in tests/.
#[allow(clippy::expect_used)]
fn generate_keypair() -> TestKeypair {
    let signing_key = SigningKey::generate(&mut OsRng);

    let private_pem = signing_key
        .to_pkcs8_pem(LineEnding::LF)
        .expect("Ed25519 PKCS#8 PEM encoding must succeed");

    let encoding_key = EncodingKey::from_ed_pem(private_pem.as_bytes())
        .expect("EncodingKey::from_ed_pem must accept a freshly-generated key");

    let public_key_pem = signing_key
        .verifying_key()
        .to_public_key_pem(LineEnding::LF)
        .expect("Ed25519 public key PEM encoding must succeed");

    let private_key_pem = private_pem.to_string();

    TestKeypair {
        encoding_key,
        public_key_pem,
        private_key_pem,
    }
}

static TEST_KEYPAIR: OnceLock<TestKeypair> = OnceLock::new();

fn keypair() -> &'static TestKeypair {
    TEST_KEYPAIR.get_or_init(generate_keypair)
}

/// Return the test Ed25519 public key PEM (cached, stable within a test process).
pub fn test_public_key_pem() -> &'static str {
    &keypair().public_key_pem
}

/// Return the test Ed25519 private key PEM (cached, stable within a test process).
pub fn test_private_key_pem() -> &'static str {
    &keypair().private_key_pem
}

// ---------------------------------------------------------------------------
// Config factory (UI-002 PR1: avoids re-specifying GCS fields in every test)
// ---------------------------------------------------------------------------

/// Build a `Config` suitable for unit/integration tests.
///
/// `workers_url` defaults to a loopback address that is not listening.
/// GCS fields use an empty placeholder PEM — the signing module will error if
/// actually called, which is correct for non-GCS tests.
///
/// Callers that need specific GCS keys should build `Config` directly.
#[allow(clippy::expect_used)]
pub fn make_test_config(workers_url: &str) -> Config {
    Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: workers_url.to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        jwt_ed25519_private_key_pem: secrecy::SecretString::from(
            test_private_key_pem().to_string(),
        ),
        jwt_kid: TEST_KEY_ID.to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 35_000,
        gcs_signing_sa_email: "test-sa@project.iam.gserviceaccount.com".to_string(),
        // SEC-004: no SA private key — signing via IAM signBlob; no field here.
        gcs_bucket: "archiviste-conversations".to_string(),
    }
}

// ---------------------------------------------------------------------------
// Token signing helpers
// ---------------------------------------------------------------------------

/// Sign a test JWT with the runtime keypair and 7-day expiry.
#[allow(clippy::expect_used)]
#[must_use]
pub fn sign_test_token(sub: Uuid, tier: UserTier, sid: Uuid) -> String {
    let exp = Utc::now() + chrono::Duration::days(7);
    sign_test_token_with_exp(sub, tier, sid, exp)
}

/// Sign a test JWT with a custom expiry timestamp.
#[must_use]
pub fn sign_test_token_with_exp(
    sub: Uuid,
    tier: UserTier,
    sid: Uuid,
    exp: DateTime<Utc>,
) -> String {
    sign_with_iss(sub, tier, sid, exp, JWT_ISSUER_AUDIENCE)
}

/// Sign a test JWT with a custom issuer (for iss-validation tests).
#[must_use]
pub fn sign_test_token_custom_iss(sub: Uuid, tier: UserTier, sid: Uuid, iss: &str) -> String {
    let exp = Utc::now() + chrono::Duration::days(7);
    sign_with_iss(sub, tier, sid, exp, iss)
}

#[allow(clippy::expect_used)]
fn sign_with_iss(sub: Uuid, tier: UserTier, sid: Uuid, exp: DateTime<Utc>, iss: &str) -> String {
    use archiviste_gateway::auth::jwt::Claims;

    let claims = Claims {
        sub: sub.to_string(),
        tier: tier.as_str().to_string(),
        sid: sid.to_string(),
        iat: Utc::now().timestamp(),
        exp: exp.timestamp(),
        iss: iss.to_string(),
        aud: JWT_ISSUER_AUDIENCE.to_string(),
    };

    let mut header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::EdDSA);
    header.kid = Some(TEST_KEY_ID.to_string());

    jsonwebtoken::encode(&header, &claims, &keypair().encoding_key)
        .expect("test token signing must not fail — claims are well-formed")
}
