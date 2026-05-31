# SEC-006 AC-10: Terraform test fixture for the workers_iam_no_public_invoker
# check block (infra/terraform/checks.tf).
#
# Requires Terraform >= 1.6 (already enforced by versions.tf).
# Run: terraform -chdir=infra/terraform test
#
# Two cases:
#   1. allUsers member → check must fail (expect_failures).
#   2. Nominal SA member → check must pass (no expect_failures).

run "rejects_all_users" {
  # AC-10: terraform plan must fail when the invoker binding is allUsers.
  command = plan

  override_resource {
    target = google_cloud_run_v2_service_iam_member.workers_runtime_invoker
    values = {
      member = "allUsers"
    }
  }

  expect_failures = [
    check.workers_iam_no_public_invoker,
  ]
}

run "accepts_runtime_sa" {
  # AC-10: terraform plan must succeed when the invoker binding is the runtime SA.
  command = plan

  override_resource {
    target = google_cloud_run_v2_service_iam_member.workers_runtime_invoker
    values = {
      member = "serviceAccount:archiviste-runtime@my-project.iam.gserviceaccount.com"
    }
  }
}
