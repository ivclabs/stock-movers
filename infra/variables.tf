# variables.tf - settings passed in from outside (deploy.sh reads them from ../.env)

variable "region" {
  description = "AWS region. eu-north-1 = Stockholm."
  type        = string
  default     = "eu-north-1"
}

# "sensitive" = Terraform never prints these on screen.
# Note: Terraform still stores them in terraform.tfstate, which is why
# that file is in .gitignore and must never be shared.
variable "apca_key_id" {
  description = "Alpaca API key id"
  type        = string
  sensitive   = true
}

variable "apca_secret" {
  description = "Alpaca API secret"
  type        = string
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Claude API key (for judge.py)"
  type        = string
  sensitive   = true
}

variable "alert_email" {
  description = "Where AWS sends billing alerts (from ALERT_EMAIL in ../.env)"
  type        = string
}

variable "monthly_budget_usd" {
  description = "Billing alarm limit per month, in US dollars"
  type        = number
  default     = 5
}
