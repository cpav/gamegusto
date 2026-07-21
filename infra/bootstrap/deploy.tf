# ---------------------------------------------------------------------------
# The deploy role — what Terraform runs as.
#
# Scoping strategy, in order of how much work each does:
#
#   1. Explicit Deny on the control plane itself (this role, its policy, the
#      boundary, the live table, the v1 IAM user). Deny always wins, so these
#      hold no matter what the Allow statements below say.
#   2. Name prefix on every resource ARN. The role cannot see or touch
#      anything in the account not named ${var.name_prefix}-*.
#   3. A permissions boundary required on any role it creates.
#
# Honest limitation: a handful of actions have no resource-level support in
# IAM — cognito-idp:CreateUserPool and cloudfront:CreateDistribution among
# them — so they appear with Resource "*". The Denies and the boundary are
# what actually contain the blast radius; the prefix conditions are a strong
# second layer, not a complete one.
# ---------------------------------------------------------------------------

locals {
  account = data.aws_caller_identity.current.account_id
  prefix  = var.name_prefix

  # Resources that MUST stay beyond the deploy role's reach.
  protected_arns = [
    aws_iam_policy.boundary.arn, # its own ceiling
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.name_prefix}-deploy",
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.name_prefix}-deploy",
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/${var.name_prefix}", # v1 Streamlit user
  ]
}

data "aws_iam_policy_document" "deploy" {
  # --- 1. Denies -----------------------------------------------------------

  # Without this, the prefix-based Allows below would cover the deploy role's
  # own policy and its boundary — letting a run rewrite its own limits. This
  # is the statement that makes the whole scheme hold.
  statement {
    sid       = "DenyTamperingWithOwnControls"
    effect    = "Deny"
    actions   = ["iam:*"]
    resources = local.protected_arns
  }

  # The live library is irreplaceable. Terraform may describe it (to wire up
  # the Lambda's permissions) and nothing more — not even reading rows, which
  # it has no reason to do.
  statement {
    sid    = "DenyMutatingTheLiveLibrary"
    effect = "Deny"
    not_actions = [
      "dynamodb:DescribeTable",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:ListTagsOfResource",
    ]
    resources = [
      "arn:aws:dynamodb:*:${local.account}:table/${var.library_table_name}",
      "arn:aws:dynamodb:*:${local.account}:table/${var.library_table_name}/*",
    ]
  }

  # Belt and braces alongside the condition on CreateRole below: no role may
  # be created or have its boundary changed unless the boundary is ours.
  statement {
    sid    = "DenyRolesWithoutTheBoundary"
    effect = "Deny"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePermissionsBoundary",
      "iam:DeleteRolePermissionsBoundary",
    ]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary.arn]
    }
  }

  # --- 2. Allows -----------------------------------------------------------

  # Terraform's own state.
  statement {
    sid       = "TerraformState"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    sid    = "TerraformStateObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject", # the lock file, on release
    ]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }

  # Read-only look at the live table, so the stack can reference it as a data
  # source. Paired with the Deny above, describe is genuinely all it gets.
  statement {
    sid    = "DescribeLiveLibrary"
    effect = "Allow"
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:ListTagsOfResource",
    ]
    resources = ["arn:aws:dynamodb:*:${local.account}:table/${var.library_table_name}"]
  }

  # Lambda: the API itself, plus its streaming Function URL.
  statement {
    sid    = "ManageLambda"
    effect = "Allow"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PublishVersion",
      "lambda:ListVersionsByFunction",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:GetPolicy",
      "lambda:CreateFunctionUrlConfig",
      "lambda:UpdateFunctionUrlConfig",
      "lambda:DeleteFunctionUrlConfig",
      "lambda:GetFunctionUrlConfig",
      "lambda:PutFunctionConcurrency",
      "lambda:DeleteFunctionConcurrency",
    ]
    resources = ["arn:aws:lambda:*:${local.account}:function:${local.prefix}-*"]
  }

  # IAM, tightly fenced: only this project's roles and policies.
  statement {
    sid    = "ManageProjectRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:PutRolePermissionsBoundary",
    ]
    resources = ["arn:aws:iam::${local.account}:role/${local.prefix}-*"]
  }

  statement {
    sid    = "ManageProjectPolicies"
    effect = "Allow"
    actions = [
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:ListPolicyVersions",
      "iam:TagPolicy",
      "iam:UntagPolicy",
      "iam:ListEntitiesForPolicy",
    ]
    resources = ["arn:aws:iam::${local.account}:policy/${local.prefix}-*"]
  }

  # Handing the execution role to Lambda, and to nothing else.
  statement {
    sid       = "PassExecutionRoleToLambdaOnly"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account}:role/${local.prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  # Cognito. CreateUserPool has no resource-level support, so it is split out
  # and constrained by a required project tag instead.
  statement {
    sid       = "CreateUserPool"
    effect    = "Allow"
    actions   = ["cognito-idp:CreateUserPool"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = [local.prefix]
    }
  }
  statement {
    sid    = "ManageUserPool"
    effect = "Allow"
    actions = [
      "cognito-idp:DeleteUserPool",
      "cognito-idp:DescribeUserPool",
      "cognito-idp:UpdateUserPool",
      "cognito-idp:GetUserPoolMfaConfig",
      "cognito-idp:SetUserPoolMfaConfig",
      "cognito-idp:CreateUserPoolClient",
      "cognito-idp:DeleteUserPoolClient",
      "cognito-idp:DescribeUserPoolClient",
      "cognito-idp:UpdateUserPoolClient",
      "cognito-idp:CreateUserPoolDomain",
      "cognito-idp:DeleteUserPoolDomain",
      "cognito-idp:DescribeUserPoolDomain",
      "cognito-idp:UpdateUserPoolDomain",
      "cognito-idp:TagResource",
      "cognito-idp:UntagResource",
      "cognito-idp:ListTagsForResource",
      "cognito-idp:AdminCreateUser", # inviting yourself as the first user
      "cognito-idp:AdminGetUser",
      "cognito-idp:AdminDeleteUser",
      "cognito-idp:AdminSetUserPassword",
    ]
    resources = ["arn:aws:cognito-idp:*:${local.account}:userpool/*"]
  }

  # Static hosting for the PWA.
  statement {
    sid    = "ManageSiteBuckets"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:GetBucketPolicy",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:GetBucketPublicAccessBlock",
      "s3:PutBucketPublicAccessBlock",
      "s3:GetBucketVersioning",
      "s3:PutBucketVersioning",
      "s3:GetBucketTagging",
      "s3:PutBucketTagging",
      "s3:GetBucketOwnershipControls",
      "s3:PutBucketOwnershipControls",
      "s3:GetEncryptionConfiguration",
      "s3:PutEncryptionConfiguration",
      "s3:GetBucketCORS",
      "s3:PutBucketCORS",
      "s3:GetBucketWebsite",
      "s3:PutBucketWebsite",
      "s3:DeleteBucketWebsite",
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:PutObjectAcl",
    ]
    resources = [
      "arn:aws:s3:::${local.prefix}-*",
      "arn:aws:s3:::${local.prefix}-*/*",
    ]
  }

  # CloudFront in front of both origins. Almost nothing here supports
  # resource-level permissions at create time.
  statement {
    sid    = "ManageCloudFront"
    effect = "Allow"
    actions = [
      "cloudfront:CreateDistribution",
      "cloudfront:UpdateDistribution",
      "cloudfront:DeleteDistribution",
      "cloudfront:GetDistribution",
      "cloudfront:GetDistributionConfig",
      "cloudfront:ListDistributions",
      "cloudfront:CreateOriginAccessControl",
      "cloudfront:UpdateOriginAccessControl",
      "cloudfront:DeleteOriginAccessControl",
      "cloudfront:GetOriginAccessControl",
      "cloudfront:GetOriginAccessControlConfig",
      "cloudfront:ListOriginAccessControls",
      "cloudfront:CreateInvalidation",
      "cloudfront:GetInvalidation",
      "cloudfront:CreateCachePolicy",
      "cloudfront:DeleteCachePolicy",
      "cloudfront:GetCachePolicy",
      "cloudfront:GetCachePolicyConfig",
      "cloudfront:UpdateCachePolicy",
      "cloudfront:ListCachePolicies",
      "cloudfront:CreateResponseHeadersPolicy",
      "cloudfront:DeleteResponseHeadersPolicy",
      "cloudfront:GetResponseHeadersPolicy",
      "cloudfront:GetResponseHeadersPolicyConfig",
      "cloudfront:UpdateResponseHeadersPolicy",
      "cloudfront:CreateOriginRequestPolicy",
      "cloudfront:DeleteOriginRequestPolicy",
      "cloudfront:GetOriginRequestPolicy",
      "cloudfront:GetOriginRequestPolicyConfig",
      "cloudfront:UpdateOriginRequestPolicy",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
      "cloudfront:ListTagsForResource",
    ]
    resources = ["*"]
  }

  # Log groups for the function.
  statement {
    sid    = "ManageLogGroups"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:DescribeLogGroups",
      "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource",
      "logs:ListTagsForResource",
    ]
    resources = ["arn:aws:logs:*:${local.account}:log-group:/aws/lambda/${local.prefix}-*"]
  }

  # The Tavily key, as an SSM SecureString under this project's path.
  statement {
    sid    = "ManageProjectParameters"
    effect = "Allow"
    actions = [
      "ssm:PutParameter",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:DeleteParameter",
      "ssm:DescribeParameters",
      "ssm:AddTagsToResource",
      "ssm:RemoveTagsFromResource",
      "ssm:ListTagsForResource",
    ]
    resources = ["arn:aws:ssm:*:${local.account}:parameter/${local.prefix}/*"]
  }

  # Read-only calls Terraform makes constantly to plan.
  statement {
    sid    = "ReadOnlyPlanning"
    effect = "Allow"
    actions = [
      "sts:GetCallerIdentity",
      "iam:ListPolicies",
      "iam:ListRoles",
      "tag:GetResources",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deploy" {
  name        = "${local.prefix}-deploy"
  description = "Permissions for Terraform to manage the ${local.prefix} v2 stack. Cannot touch the live library, the v1 user, or its own controls."
  policy      = data.aws_iam_policy_document.deploy.json
}

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type = "AWS"
      # Defaults to whoever bootstrapped this. Add a GitHub OIDC role here to
      # move deploys into CI without touching any of the policies above.
      identifiers = length(var.deploy_principals) > 0 ? var.deploy_principals : [data.aws_caller_identity.current.arn]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${local.prefix}-deploy"
  description          = "Assumed to run terraform for the ${local.prefix} v2 stack."
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  max_session_duration = var.max_session_seconds
}

resource "aws_iam_role_policy_attachment" "deploy" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy.arn
}
