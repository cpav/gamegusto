# Adding another user

GameGusto is a single-user app that has been *authenticated*, which is not the
same as being multi-user. This document is the honest version of what adding a
second person costs, because the two obvious answers are both wrong:

- **"Add a sign-up link"** — actively unsafe today. See [Why there is no
  sign-up link](#why-there-is-no-sign-up-link).
- **"Just change `current_user` to return the Cognito `sub`"** — a one-line
  change that appears to work and quietly hides the existing library. See
  [Path B](#path-b-separate-libraries).

Read [How identity actually works](#how-identity-actually-works) first. Then
pick a path:

| | Sees | Effort | Use when |
|---|---|---|---|
| [Path A](#path-a-a-shared-library) | **The same library as you** | ~10 lines | A partner or housemate sharing one games collection |
| [Path B](#path-b-separate-libraries) | Their own, separate library | ~1 day | Genuinely separate people |

---

## How identity actually works

Two identities exist, and they are deliberately not the same thing.

**The Cognito user** decides *whether a request proceeds*. `api/auth.py`
verifies the ID token's signature against the pool's JWKS, checks the issuer,
and rejects anything that is not an ID token. That is all it does.

**The storage identity** decides *whose data is touched*. It is
`ctx.user_id`, currently the string `"default"`, and every row in DynamoDB is
keyed `PK = USER#<user_id>` (`services/dynamodb_memory_client.py`).

`api/app.py` joins them, and the join is the important part:

```python
def current_user(request: Request) -> str:
    if verifier is None:
        return ctx.user_id
    ...
    verifier.subject(token)     # authenticate
    return ctx.user_id          # ...then return the STORAGE identity
```

The Cognito `sub` is verified and then discarded. This is not an oversight:
the library predates authentication and lives under `"default"`, so returning
the subject would not migrate that data — it would leave it in place and show
the owner an empty account. That looks exactly like data loss.

**Consequence:** every authenticated user shares one library. Auth is a
front door, not a partition.

### What lives under a user partition

All of it, via `services/memory_service.py`:

- game records (the library)
- owned platforms
- session history and past recommendations
- 👍/👎 feedback, which feeds taste back into the agent
- the conversation transcript

### The part that is easy to miss

`build_app()` is called **once per process** (`api/main.py`), and it binds the
user into the object graph at construction:

```python
sources.append(ManualSource(memory, user_id))
tools = ToolRegistry(..., user_id=user_id)
```

`agent/tools.py` reads that bound `user_id` in **16 places**. So the agent's
tools are not user-agnostic helpers that take an identity per call — they are
wired to one person when the Lambda cold-starts. Any real multi-user design
has to deal with this, and it is the reason Path B is a day rather than a
line.

---

## Why there is no sign-up link

Self-signup is off (`allow_admin_create_user_only = true` in
`infra/stack/cognito.tf`). Turning it on, with storage as it is today, would
mean anyone on the internet could create an account and immediately get:

- your game library, to read and delete
- your conversation history
- your Bedrock spend

The authentication would be working perfectly and the outcome would still be a
breach. The absence of the link is what holds the door shut. Do not add one
before completing Path B, and even then only with a deliberate decision about
who is allowed in.

---

## Path A: a shared library

For someone who *should* see the same collection — a partner sharing the
console. No code changes; they simply sign in as a second Cognito identity
into the same storage.

**1. Add the user** in `infra/stack/cognito.tf`:

```hcl
resource "aws_cognito_user" "partner" {
  user_pool_id = aws_cognito_user_pool.main.id
  username     = var.partner_email

  attributes = {
    email          = var.partner_email
    email_verified = true
  }

  lifecycle {
    ignore_changes = [temporary_password, password]
  }
}
```

**2. Apply.** Cognito emails them the invitation (the template is in the same
file) with a temporary password they must change on first sign-in.

```bash
make apply
```

**That is the whole change.** Nothing in the API or the client needs to know:
both people authenticate, both resolve to `"default"`, both see one library.

### What to expect

- **Concurrent turns are refused, not queued.** `TurnGuard` is keyed by
  storage identity, so both users share one slot: if one is mid-answer, the
  other gets a `409` and the client shows "Still working on the previous
  message."
- **One conversation.** They will see your transcript and you will see theirs.
- **Feedback is pooled.** Their 👍/👎 trains the agent's sense of *your*
  taste, because the agent cannot tell you apart.
- **Cost is pooled**, with no per-user cap. See [Before you invite
  anyone](#before-you-invite-anyone).

If any of those read as a problem rather than a feature, you want Path B.

---

## Path B: separate libraries

Real per-user data. Four pieces, in this order — the migration must land
before the switch, or the owner opens the app to an empty library.

### 1. Make the object graph user-aware

The blocker described above. Two workable shapes:

**Cache a context per user** (smaller change). Keep `build_app()` as is, and
hold a `dict[str, AppContext]`, building on first request for each user.
Boto3 clients are the expensive part, so pass them in rather than
reconstructing per user. Bounded by the number of real users, which is tiny.

**Thread the identity through calls** (cleaner, larger). Drop `user_id` from
`ToolRegistry.__init__` and pass it into each dispatch instead — 16 call
sites in `agent/tools.py`, plus `ManualSource`. This makes the graph
genuinely stateless and is the better long-term shape.

Prefer the second if this is going anywhere; take the first if you want one
extra person by the weekend.

### 2. Migrate the existing data

Copy every `USER#default` item to `USER#<your-cognito-sub>`. Get the sub from:

```bash
aws cognito-idp admin-get-user \
  --user-pool-id <pool-id> --username <your-email> \
  --query 'UserAttributes[?Name==`sub`].Value' --output text \
  --profile gamegusto-deploy
```

Copy, **verify, and only then delete** the originals — the deploy role is
denied writes to this table by design (`infra/bootstrap/deploy.tf`), so this
runs with admin credentials. Take a PITR backup or an on-demand backup first;
this is the only irreplaceable data in the system.

### 3. Flip the join

Only now, in `api/app.py`:

```python
return verifier.subject(token)   # the Cognito sub, not ctx.user_id
```

Delete the storage-identity comment while you are there, since it stops being
true.

### 4. Add a per-user budget

Currently missing, and the reason not to skip it: Bedrock tokens are the
entire bill, and an invited user can spend without limit. Store a monthly
counter per user (`DOC#usage` under their partition), increment it from the
`usage` already returned on every turn, and refuse new turns over the cap with
a clear message. The plumbing exists — `api/app.py` reports usage per turn
already; nothing accumulates it.

### Also worth doing

- **Per-user `TurnGuard`** falls out of the change for free, since it is keyed
  by whatever `current_user` returns.
- **A shared enrichment cache.** Game metadata is not personal; if two people
  add Hades you pay Brave and Bedrock twice. A `GAME#<dedup_key>` item
  outside user partitions fixes that. Only worth it beyond ~3 users.
- **Decide the invitation policy explicitly.** Even with Path B done,
  `allow_admin_create_user_only` should probably stay on: adding people via
  Terraform is a deliberate act with a record, which is what you want for
  something that spends money.

---

## Before you invite anyone

**Cost.** Bedrock is the bill and there is no cap. Check what a real session
costs (the per-turn figure in the app) and decide what a second person doubles
before, not after.

**The invitation email** (`infra/stack/cognito.tf`) names the product,
explains the temporary password, and tells the recipient what to do if they
were not expecting it. Read it once before it goes to someone who does not
know what GameGusto is.

**Removing someone** is `terraform destroy -target` on their
`aws_cognito_user`, or deleting them from the pool. Their tokens stop working
immediately. Under Path A nothing of theirs is separable from yours; under
Path B their partition can be deleted with them.
