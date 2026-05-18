# MED-1 / AC-9: email notification channel wired to var.budget_email.
# Without this resource the budget alert only reaches billing account admins (not the operator).
resource "google_monitoring_notification_channel" "budget_owner_email" {
  project      = var.project_id
  display_name = "archiviste-budget-owner"
  type         = "email"

  labels = {
    email_address = var.budget_email
  }
}

# AC-9: monthly billing budget alert at 50 EUR, notif email to budget_email at 100%.
resource "google_billing_budget" "archiviste_beta_monthly" {
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
    monitoring_notification_channels = [google_monitoring_notification_channel.budget_owner_email.id]
    disable_default_iam_recipients   = false
    schema_version                   = "1.0"
  }
}
