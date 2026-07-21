#!/usr/bin/env python3
"""Assert the deploy policy still blocks the paths it is meant to block.

The deploy role's safety rests on a handful of Deny statements and ARN
prefixes that are easy to weaken by accident when adding a service. This
evaluates the rendered policy against the cases that matter — privilege
escalation, destroying the live library, and reaching outside the project —
so a regression fails loudly instead of silently widening access.

Usage (after ``terraform apply`` in this directory):

    terraform output -raw deploy_policy_json > /tmp/deploy.json
    python verify_policy.py /tmp/deploy.json

Exits non-zero if any expectation is unmet. This is a deliberately small
IAM evaluator: it models Deny-wins, wildcard matching on actions and
resources, ``NotAction``, and string conditions — enough for this policy's
shape, and not a general-purpose substitute for IAM Access Analyzer.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
from typing import Any

Decision = str  # "ALLOW" | "DENY" | "implicit-deny"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def evaluate(
    policy: dict[str, Any],
    action: str,
    resource: str,
    context: dict[str, str] | None = None,
) -> Decision:
    """Return the policy's decision for one request.

    Mirrors IAM's ordering: an explicit Deny wins outright, otherwise an
    Allow must match, otherwise the request is denied implicitly.
    """
    context = context or {}
    allowed = False

    for statement in policy["Statement"]:
        if not any(fnmatch.fnmatch(resource, r) for r in _as_list(statement["Resource"])):
            continue

        if "Action" in statement:
            matched = any(fnmatch.fnmatch(action, a) for a in _as_list(statement["Action"]))
        else:  # NotAction: everything except the listed actions
            matched = not any(fnmatch.fnmatch(action, a) for a in _as_list(statement["NotAction"]))
        if not matched:
            continue

        if not _conditions_hold(statement.get("Condition", {}), context):
            continue

        if statement["Effect"] == "Deny":
            return "DENY"
        allowed = True

    return "ALLOW" if allowed else "implicit-deny"


def _conditions_hold(condition: dict[str, Any], context: dict[str, str]) -> bool:
    for operator, pairs in condition.items():
        for key, expected in pairs.items():
            actual = context.get(key)
            if operator == "StringEquals" and actual not in _as_list(expected):
                return False
            if operator == "StringNotEquals" and actual in _as_list(expected):
                return False
    return True


def build_cases(account: str, prefix: str, table: str) -> list[tuple[str, str, str, dict, str]]:
    """The behaviours worth guarding, as (label, action, resource, context, expected)."""
    boundary = f"arn:aws:iam::{account}:policy/{prefix}-boundary"
    return [
        # --- privilege escalation ------------------------------------------
        (
            "cannot rewrite its own boundary",
            "iam:CreatePolicyVersion",
            boundary,
            {},
            "DENY",
        ),
        (
            "cannot edit its own deploy policy",
            "iam:CreatePolicyVersion",
            f"arn:aws:iam::{account}:policy/{prefix}-deploy",
            {},
            "DENY",
        ),
        (
            "cannot widen its own trust policy",
            "iam:UpdateAssumeRolePolicy",
            f"arn:aws:iam::{account}:role/{prefix}-deploy",
            {},
            "DENY",
        ),
        (
            "cannot create a role without the boundary",
            "iam:CreateRole",
            f"arn:aws:iam::{account}:role/{prefix}-lambda",
            {},
            "DENY",
        ),
        (
            "cannot hand a role to a service other than Lambda",
            "iam:PassRole",
            f"arn:aws:iam::{account}:role/{prefix}-lambda",
            {"iam:PassedToService": "ec2.amazonaws.com"},
            "implicit-deny",
        ),
        # --- the live library ----------------------------------------------
        (
            "cannot delete the live table",
            "dynamodb:DeleteTable",
            f"arn:aws:dynamodb:*:{account}:table/{table}",
            {},
            "DENY",
        ),
        (
            "cannot delete a row from the live table",
            "dynamodb:DeleteItem",
            f"arn:aws:dynamodb:*:{account}:table/{table}",
            {},
            "DENY",
        ),
        (
            "cannot even read the live table",
            "dynamodb:Scan",
            f"arn:aws:dynamodb:*:{account}:table/{table}",
            {},
            "DENY",
        ),
        # --- the v1 deploy ---------------------------------------------------
        (
            "cannot break the v1 Streamlit user's keys",
            "iam:DeleteAccessKey",
            f"arn:aws:iam::{account}:user/{prefix}",
            {},
            "DENY",
        ),
        # --- blast radius ----------------------------------------------------
        (
            "cannot touch an unrelated bucket",
            "s3:DeleteBucket",
            "arn:aws:s3:::some-other-bucket",
            {},
            "implicit-deny",
        ),
        (
            "cannot touch an unrelated function",
            "lambda:DeleteFunction",
            f"arn:aws:lambda:*:{account}:function:other-app",
            {},
            "implicit-deny",
        ),
        (
            "cannot touch an unrelated role",
            "iam:DeleteRole",
            f"arn:aws:iam::{account}:role/OrganizationAccountAccessRole",
            {},
            "implicit-deny",
        ),
        # --- and still does its job ------------------------------------------
        (
            "can create a role that carries the boundary",
            "iam:CreateRole",
            f"arn:aws:iam::{account}:role/{prefix}-lambda",
            {"iam:PermissionsBoundary": boundary},
            "ALLOW",
        ),
        (
            "can hand the execution role to Lambda",
            "iam:PassRole",
            f"arn:aws:iam::{account}:role/{prefix}-lambda",
            {"iam:PassedToService": "lambda.amazonaws.com"},
            "ALLOW",
        ),
        (
            "can describe the live table",
            "dynamodb:DescribeTable",
            f"arn:aws:dynamodb:*:{account}:table/{table}",
            {},
            "ALLOW",
        ),
        (
            "can create the project's function",
            "lambda:CreateFunction",
            f"arn:aws:lambda:*:{account}:function:{prefix}-api",
            {},
            "ALLOW",
        ),
        (
            "can write Terraform state",
            "s3:PutObject",
            f"arn:aws:s3:::{prefix}-tfstate-{account}/stack/terraform.tfstate",
            {},
            "ALLOW",
        ),
    ]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    policy = json.loads(open(sys.argv[1]).read())

    # Recover the account id from any ARN in the document so the cases line up
    # with whatever account this was rendered for.
    match = re.search(r"arn:aws:iam::(\d{12}):", json.dumps(policy))
    if not match:
        print("Could not determine the account id from the policy.", file=sys.stderr)
        return 2
    account = match.group(1)

    failures = 0
    for label, action, resource, context, expected in build_cases(
        account, "gamegusto", "gamegusto"
    ):
        actual = evaluate(policy, action, resource, context)
        ok = actual == expected
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label:44} {action:28} -> {actual}")

    total = len(build_cases(account, "gamegusto", "gamegusto"))
    print(f"\n{total - failures}/{total} expectations met")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
