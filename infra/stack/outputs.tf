output "user_pool_id" {
  description = "Cognito user pool. The API validates tokens against its JWKS."
  value       = aws_cognito_user_pool.main.id
}

output "user_pool_client_id" {
  description = "Public SPA client id. Safe to ship in the web build."
  value       = aws_cognito_user_pool_client.web.id
}

output "login_domain" {
  description = "Hosted UI domain for the login redirect."
  value       = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
}

output "lambda_role_arn" {
  description = "Execution role, carrying the permissions boundary."
  value       = aws_iam_role.lambda.arn
}

output "tavily_parameter_name" {
  description = "Write the real key here — see ssm.tf. Terraform never holds the value."
  value       = aws_ssm_parameter.tavily_api_key.name
}
