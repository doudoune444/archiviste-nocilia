//! GCS V4 signed URL generation (GOOG4-RSA-SHA256) — AC-9, AC-21.
//! No external GCS crate; uses `ring` for RSA-SHA256 (already transitive via rustls).
//! `sign_get` takes an injectable `now: DateTime<Utc>` for deterministic unit tests.

use chrono::{DateTime, Utc};
use ring::signature::{RsaKeyPair, RSA_PKCS1_SHA256};
use thiserror::Error;

/// TTL (seconds) of every signed URL produced by this module (AC-9, AC-21).
pub const SIGNED_URL_TTL_SECONDS: u64 = 300;

/// Opaque signed URL value (HTTPS string).
pub type SignedUrl = String;

/// Errors from `sign_get`.
#[derive(Debug, Error)]
pub enum SignError {
    /// PEM could not be parsed as RSA PKCS#8.
    #[error("invalid RSA private key PEM: {0}")]
    InvalidKey(String),
    /// RSA operation failed (e.g. hardware RNG unavailable).
    #[error("RSA signing failed")]
    SigningFailed,
}

/// Generate a GCS V4 signed GET URL.
///
/// `key_pem` is a PKCS#8 RSA private key in PEM format.  The caller holds it
/// as `secrecy::SecretString`; this fn accepts a `&str` view and never logs it.
///
/// # Errors
/// Returns `SignError` if the PEM is invalid or the RSA signature fails.
pub fn sign_get(
    sa_email: &str,
    key_pem: &str,
    bucket: &str,
    object: &str,
    now: DateTime<Utc>,
) -> Result<SignedUrl, SignError> {
    let key_pair = parse_rsa_key(key_pem)?;
    let datetime = now.format("%Y%m%dT%H%M%SZ").to_string();
    let date = now.format("%Y%m%d").to_string();
    let scope = format!("{date}/auto/storage/goog4_request");
    let cred_encoded = format!("{sa_email}/{scope}")
        .replace('/', "%2F")
        .replace('@', "%40");
    let host = format!("{bucket}.storage.googleapis.com");
    let qs = format!(
        "X-Goog-Algorithm=GOOG4-RSA-SHA256\
         &X-Goog-Credential={cred_encoded}\
         &X-Goog-Date={datetime}\
         &X-Goog-Expires={SIGNED_URL_TTL_SECONDS}\
         &X-Goog-SignedHeaders=host"
    );
    let canonical = format!("GET\n/{object}\n{qs}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD");
    let cr_hash = sha256_hex(canonical.as_bytes());
    let sts = format!("GOOG4-RSA-SHA256\n{datetime}\n{scope}\n{cr_hash}");
    let sig_hex = hex::encode(rsa_sign(&key_pair, sts.as_bytes())?);
    Ok(format!(
        "https://{host}/{object}?{qs}&X-Goog-Signature={sig_hex}"
    ))
}

fn parse_rsa_key(pem: &str) -> Result<RsaKeyPair, SignError> {
    let der = pem_body_to_der(pem).map_err(|e| SignError::InvalidKey(e.to_string()))?;
    RsaKeyPair::from_pkcs8(&der).map_err(|e| SignError::InvalidKey(e.to_string()))
}

fn pem_body_to_der(pem: &str) -> Result<Vec<u8>, base64::DecodeError> {
    let body = pem
        .lines()
        .filter(|l| !l.starts_with("-----"))
        .collect::<Vec<_>>()
        .join("");
    base64::engine::Engine::decode(&base64::engine::general_purpose::STANDARD, body)
}

fn rsa_sign(key: &RsaKeyPair, msg: &[u8]) -> Result<Vec<u8>, SignError> {
    let mut sig = vec![0u8; key.public().modulus_len()];
    key.sign(
        &RSA_PKCS1_SHA256,
        &ring::rand::SystemRandom::new(),
        msg,
        &mut sig,
    )
    .map_err(|_| SignError::SigningFailed)?;
    Ok(sig)
}

fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    hex::encode(Sha256::digest(data))
}
