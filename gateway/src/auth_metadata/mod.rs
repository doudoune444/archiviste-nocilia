//! Shared metadata-server bearer token provider for IAM-scoped OAuth tokens.
//!
//! Houses `TokenProvider` used by both GCS signBlob (SEC-004) and Cloud SQL
//! IAM auth (SEC-005).  Two named constructors expose the two scope variants;
//! each instance holds its own independent cache (AC-2).

pub mod token;
pub use token::{OAuthScope, TokenError, TokenProvider};
