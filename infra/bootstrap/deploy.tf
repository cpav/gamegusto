# ---------------------------------------------------------------------------
# The deploy role — what Terraform runs as.
#
# Written as Denies plus service wildcards over tightly scoped resource ARNs,
# rather than enumerated action lists. Two reasons, in order of importance:
#
#   1. The resource ARN is what actually contains this role. "lambda:* on
#      function:gamegusto-*" is no weaker than naming twenty lambda actions on
#      the same ARN, and it does not silently break Phase 3 the first time
#      Terraform calls an action nobody thought to list.
#   2. IAM managed policies are capped at 6144 characters. The enumerated
#      version exceeded it.
#
# So the security lives in the four Deny statements and the ARN prefixes, not
# in the length of the action lists. Where a service has no resource-level
# support — cognito-idp:CreateUserPool, most of cloudfront:* — that is called
# out inline rather than papered over.
# ---------------------------------------------------------------------------

locals {
  account = data.aws_caller_identity.current.account_id
  prefix  = var.name_prefix

  # Resources that MUST stay beyond the deploy role's reach.
  protected_arns = [
    aws_iam_policy.boundary.arn,                                   # its own ceiling
    "arn:aws:iam::${local.account}:policy/${local.prefix}-deploy", # its own permissions
    "arn:aws:iam::${local.account}:role/${local.prefix}-deploy",   # its own trust policy
    "arn:aws:iam::${local.account}:user/${local.prefix}",          # the v1 Streamlit user
  ]

  live_table_arns = [
    "arn:aws:dynamodb:*:${local.account}:table/${var.library_table_name}",
    "arn:aws:dynamodb:*:${local.account}:table/${var.library_table_name}/*",
  ]
}

data "aws_iam_policy_document" "deploy" {
  # === Denies — the actual security boundary ================================
  # Deny wins over any Allow, so these hold regardless of what follows.

  # Without this, the gamegusto-* prefix Allows below would match the deploy
  # role's own policy and its boundary, letting a run rewrite its own limits.
  # A prefix-scoped policy is self-escalating unless it carves this out.
  statement {
    sid       = "DenyTamperingWithOwnControls"
    effect    = "Deny"
    actions   = ["iam:*"]
    resources = local.protected_arns
  }

  # The live library is irreplaceable and Terraform does not manage it.
  # Describe is genuinely all this role gets — not even reading rows.
  statement {
    sid         = "DenyEverythingButDescribeOnLiveLibrary"
    effect      = "Deny"
    not_actions = ["dynamodb:Describe*", "dynamodb:ListTagsOfResource"]
    resources   = local.live_table_arns
  }

  # iam:CreateRole is a privilege-escalation primitive. Roles may only exist
  # with our boundary attached, which caps them however they are later
  # policied.
  statement {
    sid       = "DenyRolesWithoutTheBoundary"
    effect    = "Deny"
    actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary.arn]
    }
  }

  # The Allows below grant iam:* over gamegusto-* roles, which would otherwise
  # include passing one to any service. Lambda is the only legitimate target.
  statement {
    sid       = "DenyPassRoleExceptToLambda"
    effect    = "Deny"
    actions   = ["iam:PassRole"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  # === Allows ===============================================================

  statement {
    sid       = "TerraformState"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
  }

  # Paired with the Deny above, this resolves to describe-only.
  statement {
    sid       = "DescribeLiveLibrary"
    effect    = "Allow"
    actions   = ["dynamodb:Describe*", "dynamodb:ListTagsOfResource"]
    resources = local.live_table_arns
  }

  # The project's own resources, each fenced by its ARN.
  statement {
    sid       = "ProjectLambda"
    effect    = "Allow"
    actions   = ["lambda:*"]
    resources = ["arn:aws:lambda:*:${local.account}:function:${local.prefix}-*"]
  }

  # Attaching a layer requires permission on the layer itself, which is owned
  # by AWS, not by us — the function-scoped grant above does not reach it.
  # This is the Lambda Web Adapter, needed because response streaming is the
  # only way to keep SSE working on Lambda (Mangum buffers).
  statement {
    sid       = "ReadLambdaWebAdapterLayer"
    effect    = "Allow"
    actions   = ["lambda:GetLayerVersion"]
    resources = ["arn:aws:lambda:*:753240598075:layer:LambdaAdapterLayer*:*"]
  }

  statement {
    sid     = "ProjectRolesAndPolicies"
    effect  = "Allow"
    actions = ["iam:*"]
    resources = [
      "arn:aws:iam::${local.account}:role/${local.prefix}-*",
      "arn:aws:iam::${local.account}:policy/${local.prefix}-*",
    ]
  }

  statement {
    sid       = "ProjectBuckets"
    effect    = "Allow"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::${local.prefix}-*", "arn:aws:s3:::${local.prefix}-*/*"]
  }

  statement {
    sid       = "ProjectLogs"
    effect    = "Allow"
    actions   = ["logs:*"]
    resources = ["arn:aws:logs:*:${local.account}:log-group:/aws/lambda/${local.prefix}-*"]
  }

  statement {
    sid       = "ProjectParameters"
    effect    = "Allow"
    actions   = ["ssm:*"]
    resources = ["arn:aws:ssm:*:${local.account}:parameter/${local.prefix}/*"]
  }

  # Cognito user pool ARNs carry a generated id, so they cannot be prefixed.
  # Creating one has no resource-level support at all, hence the tag condition.
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
    sid       = "ManageUserPools"
    effect    = "Allow"
    actions   = ["cognito-idp:*"]
    resources = ["arn:aws:cognito-idp:*:${local.account}:userpool/*"]
  }

  # CloudFront has essentially no resource-level support on the create paths,
  # so this is the broadest grant here. Documented in infra/README.md.
  statement {
    sid       = "ManageCloudFront"
    effect    = "Allow"
    actions   = ["cloudfront:*"]
    resources = ["*"]
  }

  # Read-only calls Terraform makes constantly while planning.
  #
  # These are metadata APIs that genuinely have no resource-level support —
  # DescribeUserPoolDomain, DescribeLogGroups and DescribeParameters all
  # reject a scoped ARN — so they must be granted on "*". Note what is NOT
  # here: ssm:GetParameter. Describe returns parameter metadata only, while
  # Get returns the decrypted value, and granting that account-wide would let
  # this role read every secret in the account. verify_policy.py asserts it
  # stays out.
  statement {
    sid    = "ReadOnlyPlanning"
    effect = "Allow"
    actions = [
      "sts:GetCallerIdentity",
      "tag:GetResources",
      "iam:List*",
      "iam:Get*",
      "lambda:List*",
      "logs:Describe*",
      "logs:List*",
      "cognito-idp:Describe*",
      "cognito-idp:List*",
      "cognito-idp:Get*",
      "ssm:DescribeParameters",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deploy" {
  name        = "${local.prefix}-deploy"
  description = "Terraform's permissions for the ${local.prefix} v2 stack. Cannot touch the live library, the v1 user, or its own controls."
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
