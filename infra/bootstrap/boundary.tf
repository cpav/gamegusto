# ---------------------------------------------------------------------------
# Permissions boundary
#
# The deploy role needs iam:CreateRole so Terraform can build the Lambda
# execution role. Unconstrained, that is a straight path to account admin:
# create a role with AdministratorAccess, pass it to a Lambda, done.
#
# This boundary is the control that closes it. deploy.tf permits creating a
# role ONLY when this policy is attached as its permissions boundary, and a
# boundary is an intersection — the resulting role can never exercise a
# permission absent from this document, whatever policies get attached to it.
#
# So this file is the true ceiling on everything the v2 runtime can ever do.
# Read it as the answer to "what is the worst a compromised Lambda could do?"
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "boundary" {
  # The model. Scoped to Anthropic models and this account's inference
  # profiles — a cross-Region profile fans out to several regions, hence the
  # region wildcard on the foundation-model ARN.
  statement {
    sid    = "InvokeAnthropicModels"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:*::foundation-model/anthropic.*",
      "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
    ]
  }

  # The library. Item-level access to the one existing table, and nothing
  # else in DynamoDB. Note the absence of DeleteTable and UpdateTable: the
  # runtime reads and writes rows, it never reshapes the table.
  statement {
    sid    = "LibraryTableItems"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:DescribeTable",
    ]
    resources = [
      "arn:aws:dynamodb:*:${data.aws_caller_identity.current.account_id}:table/${var.library_table_name}",
      "arn:aws:dynamodb:*:${data.aws_caller_identity.current.account_id}:table/${var.library_table_name}/index/*",
    ]
  }

  # The Tavily key, held as an SSM SecureString. Read-only, and only under
  # this project's parameter path.
  statement {
    sid    = "ReadProjectSecrets"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = ["arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/${var.name_prefix}/*"]
  }

  # Decrypting those SecureStrings, restricted to requests that actually came
  # via SSM rather than direct KMS use.
  statement {
    sid       = "DecryptSecretsViaSSM"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  # Its own logs, nothing else's.
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:CreateLogGroup",
    ]
    resources = ["arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.name_prefix}-*"]
  }

  # Cognito token verification needs the pool's public JWKS, which is fetched
  # over plain HTTPS and needs no IAM. Deliberately no cognito-idp:Admin*
  # here: the API validates tokens, it does not administer users.
}

resource "aws_iam_policy" "boundary" {
  name        = "${var.name_prefix}-boundary"
  path        = "/"
  description = "Ceiling for every role the ${var.name_prefix} deploy role creates. Blocks privilege escalation via iam:CreateRole."
  policy      = data.aws_iam_policy_document.boundary.json
}
