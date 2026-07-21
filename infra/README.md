# Infrastructure

Terraform for the v2 stack. Split in two on purpose:

| | Who applies it | How often | State |
|---|---|---|---|
| [`bootstrap/`](bootstrap/) | **You**, with your admin identity | Once | Local (git-ignored) |
| `stack/` (Phase 3) | Terraform, as the `gamegusto-deploy` role | Every deploy | S3, created by bootstrap |

The split exists because Terraform cannot create the credentials it runs
under. `bootstrap/` is the smallest possible set of things that must precede
automation — after it, no console clicking is needed.

## What bootstrap creates

1. **`gamegusto-tfstate-<account>`** — remote state bucket. Versioned,
   encrypted, TLS-only, public access blocked, `prevent_destroy`.
2. **`gamegusto-boundary`** — an IAM *permissions boundary*. The ceiling on
   what any role in this project can ever do. Read
   [`bootstrap/boundary.tf`](bootstrap/boundary.tf) to see the worst case for
   a compromised Lambda: invoke Anthropic models, read and write rows in the
   one existing table, read this project's SSM parameters, write its own logs.
   Nothing else.
3. **`gamegusto-deploy`** — the policy Terraform runs under.
4. **`gamegusto-deploy`** — the role carrying that policy, assumable by you.

## Running it

Requires Terraform >= 1.11 (`brew install terraform`, or `brew install
opentofu` and substitute `tofu`).

```bash
cd infra/bootstrap
terraform init
terraform apply -var aws_region=<your region>        # review the plan first
```

Then append the printed profile snippet to `~/.aws/config`. From that point
every stack run is:

```bash
AWS_PROFILE=gamegusto-deploy terraform -chdir=infra/stack apply
```

Temporary credentials, scoped to the deploy policy — your admin rights are
never what's driving an apply.

## Why the role cannot escalate itself

`iam:CreateRole` is a privilege-escalation primitive: given it, you can mint a
role with `AdministratorAccess` and pass it to a Lambda. Three things prevent
that here:

- Creating a role is permitted **only** when `gamegusto-boundary` is attached
  as its permissions boundary; a boundary is an intersection, so the new role
  can never exceed it whatever policies it also carries.
- `iam:PassRole` is limited to `gamegusto-*` roles, and only to
  `lambda.amazonaws.com`.
- An explicit **Deny** covers the deploy role, its policy, and the boundary
  itself. Without it the `gamegusto-*` prefix rules would match those very
  resources and a run could rewrite its own limits. Deny always wins.

## Verifying the guard rails

The safety above rests on a few Deny statements and ARN prefixes that are easy
to weaken by accident when adding a service. After any change to the policy:

```bash
cd infra/bootstrap
terraform output -raw deploy_policy_json > /tmp/deploy.json
python verify_policy.py /tmp/deploy.json
```

It evaluates the rendered policy against the paths that matter and exits
non-zero if any regressed — that the role cannot rewrite its own boundary,
cannot create a role without one, cannot delete or even read the live table,
cannot break the v1 user, cannot reach outside the project, and can still do
its actual job.

## What is deliberately out of reach

- **The live `gamegusto` DynamoDB table.** It holds the real library, sessions
  and platforms, and Terraform does not manage it. The deploy role may call
  `DescribeTable` and nothing else — an explicit Deny blocks every other
  action, including reads. `terraform destroy` cannot touch your data.
- **The `gamegusto` IAM user.** Long-lived keys for the v1 Streamlit deploy.
  Denied outright, so v1 keeps running untouched until Phase 4 retires it.

## Known limits of the scoping

Some IAM actions have no resource-level support, so they appear with
`Resource "*"`: `cognito-idp:CreateUserPool` (constrained instead by a
required `Project` tag) and most of `cloudfront:*`. Within CloudFront and
Cognito the role is therefore broader than the name prefix suggests. The
Denies and the boundary are the controls that actually bound the damage; the
prefix is a strong second layer, not a complete one.

`bootstrap/terraform.tfstate` is git-ignored. Losing it is recoverable — the
four resources are stable and importable — so it is not worth a second
chicken-and-egg backend.
