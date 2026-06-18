# SEC-006 AC-10: Guard against accidental public invoker binding on the workers
# Cloud Run service. IAM (roles/run.invoker restricted to archiviste-runtime SA)
# is the sole trust boundary; ingress=INGRESS_TRAFFIC_ALL is intentional routing.
#
# WHY this assert references only workers_runtime_invoker: the check reads the
# single known google_cloud_run_v2_service_iam_member resource keyed on workers.
# Drift risk: if a future PR adds a SECOND google_cloud_run_v2_service_iam_member
# binding on the workers service (e.g. a new role), this assert will not cover
# it. The author of that PR MUST extend this check block to include the new
# resource in the condition.
check "workers_iam_no_public_invoker" {
  assert {
    condition = (
      google_cloud_run_v2_service_iam_member.workers_runtime_invoker.member != "allUsers" &&
      google_cloud_run_v2_service_iam_member.workers_runtime_invoker.member != "allAuthenticatedUsers"
    )
    error_message = "workers run.invoker binding must not be allUsers or allAuthenticatedUsers — IAM is the trust boundary (SEC-006 AC-10)."
  }
}

# PLATFORM-004 AC-2: Guard that the gateway run.invoker binding is never
# flipped back to allUsers or allAuthenticatedUsers. The gateway is now
# browser-unreachable by design — IAM is the enforced trust boundary.
# Parallel to workers_iam_no_public_invoker above (SEC-006 pattern).
check "gateway_iam_no_public_invoker" {
  assert {
    condition = (
      google_cloud_run_v2_service_iam_member.gateway_runtime_invoker.member != "allUsers" &&
      google_cloud_run_v2_service_iam_member.gateway_runtime_invoker.member != "allAuthenticatedUsers"
    )
    error_message = "gateway run.invoker binding must not be allUsers or allAuthenticatedUsers — gateway is SA-gated (PLATFORM-004 AC-2)."
  }
}
