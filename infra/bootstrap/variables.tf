variable "aws_region" {
  description = "Region for the state bucket and the v2 stack. Must be the region already hosting the gamegusto DynamoDB table and Bedrock access."
  type        = string
}

variable "name_prefix" {
  description = <<-EOT
    Every resource the deploy role is allowed to touch must start with this.
    The prefix IS the security boundary: the role can neither see nor modify
    anything in the account named otherwise. Changing it means reissuing the
    policy, so treat it as fixed.
  EOT
  type        = string
  default     = "gamegusto"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name_prefix))
    error_message = "Prefix must be lowercase alphanumeric with hyphens, 3-21 characters."
  }
}

variable "state_bucket_name" {
  description = "Terraform state bucket. Must be globally unique; the account id is appended by default to guarantee that."
  type        = string
  default     = null
}

variable "library_table_name" {
  description = <<-EOT
    The EXISTING DynamoDB table holding the live library, sessions and
    platforms. Terraform never manages this table — the deploy role is granted
    describe-only access to it, and an explicit Deny blocks every mutating and
    destructive action. Named here so those guard rails can be written.
  EOT
  type        = string
  default     = "gamegusto"
}

variable "deploy_principals" {
  description = <<-EOT
    ARNs allowed to assume the deploy role. Defaults to whoever applies this
    module, which is normally what you want for a single-operator account.
    Add a GitHub OIDC role ARN here later to move deploys into CI without
    reissuing any of the policies.
  EOT
  type        = list(string)
  default     = []
}

variable "max_session_seconds" {
  description = "Maximum lifetime of an assumed deploy session. One hour is long enough for a full apply and short enough to limit an exposed credential."
  type        = number
  default     = 3600
}
