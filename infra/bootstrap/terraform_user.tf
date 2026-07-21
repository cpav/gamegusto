# ---------------------------------------------------------------------------
# The identity Terraform runs as, day to day.
#
# The problem this solves: the deploy role trusted the admin user, so the only
# credential that could reach it was an admin access key sitting on a laptop.
# That key is the most valuable thing on the machine — it can do anything in
# the account — and it was being used for something that needs almost nothing.
#
# This user can do exactly one thing: assume gamegusto-deploy. Its access key
# is close to worthless on its own. Stealing it buys an attacker the deploy
# role, which is already bounded (see deploy.tf) and cannot touch the live
# library, the v1 credentials, or its own permissions.
#
# The admin user is not deleted — bootstrap changes still need it, since the
# deploy role is deliberately forbidden from editing its own policy. The point
# is that admin becomes an occasional, deliberate act rather than the ambient
# credential on disk.
#
# No access key is created here on purpose. Terraform would write the secret
# into state, and state is a file that gets copied around. Mint it yourself:
#
#   aws iam create-access-key --user-name gamegusto-terraform --profile admin
#   aws configure --profile gamegusto-terraform     # paste the two values
# ---------------------------------------------------------------------------

resource "aws_iam_user" "terraform" {
  name = "${var.name_prefix}-terraform"
  path = "/"
}

data "aws_iam_policy_document" "terraform_assume_only" {
  statement {
    sid       = "AssumeTheDeployRoleAndNothingElse"
    effect    = "Allow"
    actions   = ["sts:AssumeRole"]
    resources = [aws_iam_role.deploy.arn]
  }
}

resource "aws_iam_user_policy" "terraform" {
  name   = "${var.name_prefix}-assume-deploy"
  user   = aws_iam_user.terraform.name
  policy = data.aws_iam_policy_document.terraform_assume_only.json
}
