# ---------------------------------------------------------------------------
# The Lambda execution role.
#
# Every permission here also has to be permitted by gamegusto-boundary, which
# bootstrap created and which the deploy policy requires on any role it makes.
# The boundary is the ceiling; this policy is what the function actually uses.
# If you add a grant here that the boundary does not allow, the role will be
# created and the call will still be denied at runtime — check boundary.tf.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_runtime" {
  statement {
    sid    = "InvokeTheModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream", # the streaming path
    ]
    resources = [
      "arn:aws:bedrock:*::foundation-model/anthropic.*",
      "arn:aws:bedrock:*:${local.account}:inference-profile/*",
      "arn:aws:bedrock:*::inference-profile/*",
    ]
  }

  statement {
    sid    = "LibraryItems"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:DescribeTable",
    ]
    resources = [
      data.aws_dynamodb_table.library.arn,
      "${data.aws_dynamodb_table.library.arn}/index/*",
    ]
  }

  statement {
    sid     = "ReadTavilyKey"
    effect  = "Allow"
    actions = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.tavily_api_key.arn,
      aws_ssm_parameter.igdb_credentials.arn,
    ]
  }

  statement {
    sid       = "DecryptViaSSM"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "OwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_iam_role" "lambda" {
  name = "${local.prefix}-api"

  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json

  # Required by the deploy policy: roles may only be created carrying this.
  # It is what makes granting Terraform iam:CreateRole safe.
  permissions_boundary = "arn:aws:iam::${local.account}:policy/${local.prefix}-boundary"
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.prefix}-api-runtime"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_runtime.json
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.prefix}-api"
  retention_in_days = 30
}
