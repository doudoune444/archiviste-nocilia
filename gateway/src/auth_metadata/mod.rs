//! Shared metadata-server bearer token providers.
//!
//! - `TokenProvider` — OAuth access tokens for GCS signBlob (SEC-004) and
//!   Cloud SQL IAM auth (SEC-005).
//! - `IdTokenProvider` — Google-signed ID tokens for Cloud Run service-to-service
//!   auth (SEC-006).  Sibling of `TokenProvider`; no shared cache.

pub mod id_token;
pub mod token;
pub use id_token::IdTokenProvider;
pub use token::{OAuthScope, TokenError, TokenProvider};
