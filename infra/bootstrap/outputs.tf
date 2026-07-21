output "deploy_role_arn" {
  description = "Role Terraform assumes for the v2 stack."
  value       = aws_iam_role.deploy.arn
}

output "boundary_policy_arn" {
  description = "Permissions boundary every role created by the stack must carry."
  value       = aws_iam_policy.boundary.arn
}

output "state_bucket" {
  description = "Remote state bucket for the v2 stack."
  value       = aws_s3_bucket.state.id
}

output "deploy_policy_json" {
  description = "Rendered deploy policy. Pipe to verify_policy.py to re-check the escalation and blast-radius guarantees after any change."
  value       = data.aws_iam_policy_document.deploy.json
}

output "boundary_policy_json" {
  description = "Rendered permissions boundary — the ceiling on every role in this project."
  value       = data.aws_iam_policy_document.boundary.json
}

output "aws_profile_snippet" {
  description = "Append this to ~/.aws/config, then run terraform with AWS_PROFILE=gamegusto-deploy. No manual sts calls, no long-lived keys."
  value       = <<-EOT

    [profile ${var.name_prefix}-deploy]
    role_arn       = ${aws_iam_role.deploy.arn}
    source_profile = default
    region         = ${var.aws_region}
  EOT
}
