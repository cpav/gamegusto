# ---------------------------------------------------------------------------
# The Tavily API key.
#
# Created as a SecureString with a placeholder, then written out of band. The
# real value is deliberately NOT a Terraform variable: anything Terraform
# knows ends up in state, and state is a file in S3 that gets copied around.
# ignore_changes means later applies leave the real value alone.
#
# Set it once, after the first apply:
#
#   aws ssm put-parameter --name /gamegusto/tavily_api_key \
#     --value "$(grep '^TAVILY_API_KEY=' .env | cut -d= -f2-)" \
#     --type SecureString --overwrite --profile gamegusto-deploy
#
# SSM Parameter Store rather than Secrets Manager: SecureStrings are free at
# this scale, Secrets Manager is $0.40/secret/month, and nothing here needs
# rotation or cross-account sharing.
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "tavily_api_key" {
  name        = "/${local.prefix}/tavily_api_key"
  description = "Tavily key for metadata enrichment and autocomplete."
  type        = "SecureString"
  value       = "placeholder-set-out-of-band"

  lifecycle {
    ignore_changes = [value]
  }
}
