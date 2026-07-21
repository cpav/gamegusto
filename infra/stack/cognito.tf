# ---------------------------------------------------------------------------
# Identity.
#
# One user pool, one public SPA client, one user — you. Self-signup is off:
# nobody can create an account, and the single user is created here at apply
# time so there is no console step.
#
# The client is public (no secret) and uses authorization code flow with
# PKCE, which is the correct choice for a browser app: a secret shipped to a
# browser is not a secret, and PKCE covers the interception risk that made
# implicit flow unsafe. Tokens are validated in the API — see api/auth.py.
# ---------------------------------------------------------------------------

resource "aws_cognito_user_pool" "main" {
  name = local.prefix

  # Required for CreateUserPool under the deploy policy, which has no
  # resource-level IAM support and is gated on this tag instead.
  tags = { Project = local.prefix }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true # no self-signup, ever

    # Cognito's default invite is a bare "Your username is X and temporary
    # password is Y" with no indication of what it grants access to — which
    # reads like phishing when it lands months later. Both {username} and
    # {####} are mandatory placeholders; Cognito rejects the template without
    # them.
    #
    # No app URL is embedded: referencing the distribution here would close a
    # dependency cycle (pool -> CloudFront -> Function URL -> Lambda, which
    # needs the pool id). var.app_url carries it instead, and the message
    # degrades gracefully when it is unset.
    invite_message_template {
      # Required by Cognito even though this pool only ever sends email —
      # it rejects the whole template if sms_message is empty.
      sms_message   = "GameGusto sign-in for {username}. Temporary password: {####}"
      email_subject = "Your GameGusto sign-in"
      email_message = replace(
        trimspace(
          <<-HTML
            <div style="font-family:-apple-system,system-ui,sans-serif;font-size:15px;line-height:1.5;color:#1a1d2e">
              <p style="font-size:17px;margin:0 0 4px"><b>GameGusto</b></p>
              <p style="margin:0 0 18px;color:#4a4f66">Your next game, cooked and served to your taste.</p>
              <p>An account has been created for you. Use these details the first time you sign in — you will be asked to choose your own password straight away.</p>
              <p style="background:#f7f2e6;border:1px solid #d9d0ba;border-radius:10px;padding:12px 14px">
                <b>Email:</b> {username}<br>
                <b>Temporary password:</b> {####}
              </p>
              APP_LINK
              <p style="color:#6f7590;font-size:13px">The temporary password expires in 7 days. If you did not expect this, you can ignore it — the account cannot be used until that password is changed.</p>
            </div>
          HTML
        ),
        "APP_LINK",
        var.app_url != "" ? "<p>Open <a href=\"${var.app_url}\">${var.app_url}</a> to sign in.</p>" : ""
      )
    }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # Cognito's own mailer caps at 50/day, which is ample for one user who
  # receives an invite and the occasional password reset.
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  # Prefix must be globally unique across all of Cognito, hence the account id.
  domain       = "${local.prefix}-${local.account}"
  user_pool_id = aws_cognito_user_pool.main.id
}

# The hosted UI is the one screen in this product AWS designs rather than us,
# and landing on stock grey-and-blue in the middle of the Blend palette reads
# as a different application. Cognito only accepts a fixed set of selectors
# and properties here, so this is as close to the design system as it goes:
# the ground, the ink, and the thrill colour on the one button that matters.
resource "aws_cognito_user_pool_ui_customization" "web" {
  user_pool_id = aws_cognito_user_pool_domain.main.user_pool_id
  client_id    = aws_cognito_user_pool_client.web.id

  image_file = filebase64("${path.module}/../../web/public/icon-192.png")

  # The Blend palette's LIGHT ground, not the dark one, and deliberately so.
  # Cognito exposes no selector for page headings ("Change Password", "Reset
  # your password"), which render in its own near-black. On a dark panel those
  # become unreadable — dark grey on dark navy — and there is no way to fix it
  # from here. A cream ground keeps every string Cognito controls legible while
  # still carrying the brand through the banner, the logo and the pink action.
  css = <<-CSS
    .banner-customizable {
      background: #0e101c;
      padding: 24px 0 18px;
    }
    .background-customizable {
      background: #f7f2e6;
      border: 1px solid #d9d0ba;
      border-radius: 14px;
    }
    .logo-customizable { max-width: 56px; max-height: 56px; }
    .label-customizable { color: #4a4f66; font-weight: 400; }
    .inputField-customizable {
      background: #fffdf7;
      border: 1px solid #cfc6b0;
      border-radius: 10px;
      color: #1a1d2e;
    }
    .inputField-customizable:focus {
      border-color: #0f9ba1;
      outline: 0;
    }
    .submitButton-customizable {
      background: #d81b7a;
      border-radius: 999px;
      color: #ffffff;
      font-weight: 600;
    }
    .submitButton-customizable:hover {
      background: #b81566;
      color: #ffffff;
    }
    /* One selector per rule: Cognito validates against an allow-list and
       rejects comma-grouped selectors outright. */
    .redirect-customizable { color: #4a4f66; }
    .textDescription-customizable { color: #4a4f66; }
    .legalText-customizable { color: #6f7590; }
    .errorMessage-customizable {
      background: #fdeaf3;
      border: 1px solid #d81b7a;
      color: #6b0f3c;
      border-radius: 10px;
    }
  CSS
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${local.prefix}-web"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false # public client: a browser cannot keep a secret

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"] # code + PKCE, never implicit
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  # The deployed app plus localhost, so the same pool serves local development
  # and production without a second client.
  callback_urls = concat(
    ["http://localhost:5173/", "https://${aws_cloudfront_distribution.main.domain_name}/"],
    var.extra_callback_urls,
  )
  logout_urls = concat(
    ["http://localhost:5173/", "https://${aws_cloudfront_distribution.main.domain_name}/"],
    var.extra_callback_urls,
  )

  access_token_validity  = 60 # minutes
  id_token_validity      = 60
  refresh_token_validity = var.refresh_token_days

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  # Rotating the refresh token on each use limits the damage from a stolen
  # one: the old token is invalidated as soon as the new one is issued.
  enable_token_revocation       = true
  prevent_user_existence_errors = "ENABLED"
}

# The single user, created here so no console step is needed. Cognito emails
# a temporary password; first sign-in forces a permanent one.
resource "aws_cognito_user" "owner" {
  user_pool_id = aws_cognito_user_pool.main.id
  username     = var.login_email

  attributes = {
    email          = var.login_email
    email_verified = true
  }

  # Terraform would otherwise try to "reset" the user on every apply once the
  # password has been changed.
  lifecycle {
    ignore_changes = [temporary_password, password]
  }
}

variable "extra_callback_urls" {
  description = "Additional OAuth redirect URLs. cloudfront.tf feeds the distribution URL in here."
  type        = list(string)
  default     = []
}
