# Resolves the numeric project number (billing API canonical form) from var.project_id.
data "google_project" "current" {
  project_id = var.project_id
}

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
    # The billing API canonicalises projects to the numeric project NUMBER. Sending
    # the project ID string causes perpetual plan drift (id → number); resolve the
    # number so config matches what the API returns.
    projects = ["projects/${data.google_project.current.number}"]
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
