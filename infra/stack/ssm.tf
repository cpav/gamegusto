# ---------------------------------------------------------------------------
# The Brave Search API key.
#
# Created as a SecureString with a placeholder, then written out of band. The
# real value is deliberately NOT a Terraform variable: anything Terraform
# knows ends up in state, and state is a file in S3 that gets copied around.
# ignore_changes means later applies leave the real value alone.
#
# Set it once, after the first apply:
#
#   aws ssm put-parameter --name /gamegusto/brave_api_key \
#     --value "$(grep '^BRAVE_API_KEY=' .env | cut -d= -f2-)" \
#     --type SecureString --overwrite --profile gamegusto-deploy
#
# SSM Parameter Store rather than Secrets Manager: SecureStrings are free at
# this scale, Secrets Manager is $0.40/secret/month, and nothing here needs
# rotation or cross-account sharing.
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "brave_api_key" {
  name        = "/${local.prefix}/brave_api_key"
  description = "Brave Search API key for web search (agent + enrichment)."
  type        = "SecureString"
  value       = "placeholder-set-out-of-band"

  lifecycle {
    ignore_changes = [value]
  }
}

# IGDB (Twitch) credentials backing cover art. One parameter holding
# "client_id:client_secret" — two secrets would mean two SSM round trips on
# every cold start for values that are always set together.
#
#   aws ssm put-parameter --name /gamegusto/igdb_credentials \
#     --value "<client-id>:<client-secret>" \
#     --type SecureString --overwrite --profile gamegusto-deploy
resource "aws_ssm_parameter" "igdb_credentials" {
  name        = "/${local.prefix}/igdb_credentials"
  description = "IGDB client id and secret, colon-separated. Cover art only."
  type        = "SecureString"
  value       = "placeholder-set-out-of-band"

  lifecycle {
    ignore_changes = [value]
  }
}
