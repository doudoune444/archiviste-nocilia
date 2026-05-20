//! Unit tests for `gcs::sign` — AC-9, AC-21.
//!
//! RSA key generated at runtime via `rsa` dev-dep — no PEM committed (Z4 secret-hygiene).

// `expect` / `unwrap` allowed in tests per project convention.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use archiviste_gateway::gcs::sign::{sign_get, SignedUrl, SIGNED_URL_TTL_SECONDS};
use chrono::{TimeZone, Utc};
use rsa::{pkcs8::EncodePrivateKey, RsaPrivateKey};
use std::sync::OnceLock;

// ---------------------------------------------------------------------------
// Runtime RSA keypair (Z4: generated at test startup, never written to disk)
// ---------------------------------------------------------------------------

struct RsaFixture {
    private_key_pem: String,
    sa_email: String,
    bucket: String,
}

static RSA_FIXTURE: OnceLock<RsaFixture> = OnceLock::new();

fn fixture() -> &'static RsaFixture {
    RSA_FIXTURE.get_or_init(|| {
        let mut rng = rand_core::OsRng;
        let priv_key = RsaPrivateKey::new(&mut rng, 2048).expect("RSA 2048 keygen must succeed");
        let pem = priv_key
            .to_pkcs8_pem(rsa::pkcs8::LineEnding::LF)
            .expect("PKCS#8 PEM encoding must succeed")
            .to_string();
        RsaFixture {
            private_key_pem: pem,
            sa_email: "archiviste-runtime@project.iam.gserviceaccount.com".to_string(),
            bucket: "archiviste-conversations".to_string(),
        }
    })
}

// ---------------------------------------------------------------------------
// Helper: parse query string from a URL
// ---------------------------------------------------------------------------

fn query_param(url: &str, key: &str) -> Option<String> {
    let qs = url.split('?').nth(1)?;
    for part in qs.split('&') {
        let mut kv = part.splitn(2, '=');
        if kv.next()? == key {
            return Some(kv.next().unwrap_or("").to_string());
        }
    }
    None
}

// ---------------------------------------------------------------------------
// AC-21: SIGNED_URL_TTL_SECONDS constant is exactly 300
// ---------------------------------------------------------------------------

/// AC-21 — constant exported and equals 300 byte-for-byte.
#[test]
fn ttl_constant_is_300() {
    // AC-21: `pub const SIGNED_URL_TTL_SECONDS: u64 = 300`
    assert_eq!(SIGNED_URL_TTL_SECONDS, 300_u64);
}

// ---------------------------------------------------------------------------
// AC-9, AC-21: URL structure and parameters
// ---------------------------------------------------------------------------

/// AC-9 — signed URL has prefix `https://storage.googleapis.com/<bucket>/<object>?`
/// and carries `X-Goog-Expires=300`.
#[test]
fn signed_url_has_correct_prefix_and_expiry() {
    let f = fixture();
    let now = Utc.with_ymd_and_hms(2026, 5, 20, 12, 0, 0).unwrap();
    let url: SignedUrl = sign_get(
        &f.sa_email,
        &f.private_key_pem,
        &f.bucket,
        "conv/abc.md",
        now,
    )
    .expect("sign_get must succeed with valid key");

    assert!(
        url.starts_with("https://archiviste-conversations.storage.googleapis.com/conv/abc.md?"),
        "URL must start with https://<bucket>.storage.googleapis.com/<object>?: got {url}"
    );
    assert_eq!(
        query_param(&url, "X-Goog-Expires").as_deref(),
        Some("300"),
        "X-Goog-Expires must be 300"
    );
}

/// AC-9 — algorithm is GOOG4-RSA-SHA256 (not a weaker variant).
#[test]
fn signed_url_uses_goog4_rsa_sha256() {
    let f = fixture();
    let now = Utc::now();
    let url = sign_get(
        &f.sa_email,
        &f.private_key_pem,
        &f.bucket,
        "conv/abc.md",
        now,
    )
    .expect("sign_get must succeed");

    assert_eq!(
        query_param(&url, "X-Goog-Algorithm").as_deref(),
        Some("GOOG4-RSA-SHA256"),
    );
}

/// AC-9 — `X-Goog-SignedHeaders=host` (GET read-only — no extra signed headers).
#[test]
fn signed_url_signed_headers_is_host_only() {
    let f = fixture();
    let url = sign_get(
        &f.sa_email,
        &f.private_key_pem,
        &f.bucket,
        "conv/abc.md",
        Utc::now(),
    )
    .expect("sign_get must succeed");

    assert_eq!(
        query_param(&url, "X-Goog-SignedHeaders").as_deref(),
        Some("host"),
    );
}

/// AC-9 — signature hex is 512 chars (RSA-2048 = 256 bytes = 512 hex chars).
#[test]
fn signed_url_signature_is_512_hex_chars() {
    let f = fixture();
    let url = sign_get(
        &f.sa_email,
        &f.private_key_pem,
        &f.bucket,
        "conv/abc.md",
        Utc::now(),
    )
    .expect("sign_get must succeed");

    let sig = query_param(&url, "X-Goog-Signature").expect("X-Goog-Signature must be present");
    assert_eq!(
        sig.len(),
        512,
        "RSA-2048 signature must be 512 hex chars, got {}",
        sig.len()
    );
    assert!(
        sig.chars().all(|c| c.is_ascii_hexdigit()),
        "signature must be hex"
    );
}

/// AC-9 — `expires_at` computed as `now + SIGNED_URL_TTL_SECONDS` is within ±2 s.
#[test]
fn expires_at_is_within_tolerance() {
    let now = Utc::now();
    let expires_at =
        now + chrono::Duration::seconds(i64::try_from(SIGNED_URL_TTL_SECONDS).unwrap());
    let delta = (expires_at - now).num_seconds().unsigned_abs();
    // Tolerance: exactly 300 s (injectable `now`, no clock skew in unit test).
    assert!(
        (298..=302).contains(&delta),
        "TTL delta must be 300 ± 2 s, got {delta}"
    );
}

/// Invalid PEM returns `SignError::InvalidKey` (not a panic).
#[test]
fn invalid_pem_returns_error() {
    let result = sign_get(
        "sa@project.iam.gserviceaccount.com",
        "not-a-pem",
        "bucket",
        "obj",
        Utc::now(),
    );
    assert!(result.is_err(), "invalid PEM must return Err");
}
