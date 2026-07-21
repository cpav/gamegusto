variable "aws_region" {
  description = "Region hosting the Lambda, Cognito pool and the live table."
  type        = string
  default     = "eu-north-1"
}

variable "name_prefix" {
  description = "Prefix for every resource. The deploy role's policy is scoped to it, so changing this breaks the ability to manage the stack."
  type        = string
  default     = "gamegusto"
}

variable "library_table_name" {
  description = "The existing DynamoDB table. Read as a data source; never managed here."
  type        = string
  default     = "gamegusto"
}

variable "bedrock_model_id" {
  description = "Bedrock model or inference-profile id the agent calls."
  type        = string
  default     = "eu.anthropic.claude-sonnet-4-6"
}

variable "login_email" {
  description = <<-EOT
    Email for the single Cognito user, created at apply time so there is no
    console step. Cognito emails a temporary password on first creation.
  EOT
  type        = string
}

variable "refresh_token_days" {
  description = <<-EOT
    How long a session survives before requiring a fresh login. This is an
    installed PWA on a personal phone holding a game library — a short window
    would mean re-authenticating constantly for very little security gain, so
    the default leans towards not being annoying.
  EOT
  type        = number
  default     = 90
}
