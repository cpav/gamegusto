# ---------------------------------------------------------------------------
# The v1 Streamlit user, cut back to least privilege.
#
# This user's keys live in Streamlit Community Cloud's secrets store. They
# arrived carrying AmazonS3FullAccess, AmazonDynamoDBFullAccess (and _v2),
# AmazonBedrockFullAccess and AmazonBedrockMarketplaceAccess — which meant a
# third-party SaaS held credentials that could delete the library table or
# read and destroy the Terraform state bucket.
#
# It lives in bootstrap rather than the stack because the deploy role is
# explicitly denied any access to this user (deploy.tf), so only an admin can
# change it. That denial is deliberate: Terraform must never be able to break
# the running v1 app.
#
# Retire this file in Phase 4 when Streamlit goes away — deleting the user is
# then the whole job.
#
# The grants below are derived from what the code actually calls:
#   services/dynamodb_memory_client.py  get_item, put_item, delete_item, query
#   services/bedrock_service.py         converse, converse_stream
# ---------------------------------------------------------------------------

variable "v1_user_name" {
  description = "The existing IAM user whose keys run the v1 Streamlit app. Managed here only to restrict it."
  type        = string
  default     = "gamegusto"
}

data "aws_iam_policy_document" "v1_runtime" {
  statement {
    sid    = "InvokeAnthropicModels"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:*::foundation-model/anthropic.*",
      # Both ARN shapes: system-defined profiles (global.anthropic.*) are
      # account-qualified, but the account-less form appears in some regions.
      # A cross-Region profile also needs the underlying foundation models,
      # which the region wildcard above covers.
      "arn:aws:bedrock:*:${local.account}:inference-profile/*",
      "arn:aws:bedrock:*::inference-profile/*",
    ]
  }

  statement {
    sid    = "LibraryTableItems"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      # boto3's Table resource can lazily describe the table; cheap to allow
      # and it avoids a surprise failure in the running app.
      "dynamodb:DescribeTable",
    ]
    resources = [
      "arn:aws:dynamodb:*:${local.account}:table/${var.library_table_name}",
      "arn:aws:dynamodb:*:${local.account}:table/${var.library_table_name}/index/*",
    ]
  }
}

resource "aws_iam_user_policy" "v1_runtime" {
  name   = "${var.name_prefix}-v1-runtime"
  user   = var.v1_user_name
  policy = data.aws_iam_policy_document.v1_runtime.json
}

# Declares the complete set of managed policies on this user — an empty list,
# so the five AWS-managed grants it arrived with are detached. Without this
# they would simply sit alongside the inline policy above and nothing would
# actually be restricted.
resource "aws_iam_user_policy_attachments_exclusive" "v1" {
  user_name   = var.v1_user_name
  policy_arns = []
}
