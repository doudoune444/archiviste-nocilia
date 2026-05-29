//! Google Cloud Storage helpers used by the gateway.

pub mod sign;
// SEC-005: TokenProvider relocated to `auth_metadata::token`; re-export here
// to preserve SEC-004 call sites (`gcs::token::TokenProvider`) without churn.
/// SEC-004 backward-compat re-exports for legacy `gcs::token` callers.
pub mod token {
    pub use crate::auth_metadata::token::{OAuthScope, TokenError, TokenProvider};
}
