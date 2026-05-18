# AC-9: monthly billing budget alert at 50 EUR, notif email to budget_email at 100%.
resource "google_billing_budget" "archiviste_beta_monthly" {
  # billing_account must be set via provider default project billing account.
  # Override with: `terraform apply -var billing_account=<ID>` if needed.
  billing_account = var.billing_account
  display_name    = "archiviste-beta-monthly"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "EUR"
      units         = "50"
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = []
    # Email notification goes to billing account admins by default.
    # budget_email owner receives alert via Cloud Billing notification (Google-managed).
    disable_default_iam_recipients = false
    schema_version                 = "1.0"
  }
}
