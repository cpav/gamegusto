# ---------------------------------------------------------------------------
# The front door.
#
# One distribution, two origins, one domain:
#
#   /api/*  ->  the Lambda Function URL   (streaming)
#   /*      ->  the S3 bucket             (the PWA)
#
# Same-origin is the point. The client calls /api/chat as a relative path, so
# the SSE stream involves no CORS negotiation at all, and there is no API URL
# to bake into the build.
#
# Neither origin is publicly reachable. Both sit behind Origin Access Control,
# so CloudFront signs every request with SigV4; a direct hit on the bucket or
# the Function URL is unsigned and refused.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "site" {
  bucket = "${local.prefix}-site-${local.account}"
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SPA routing. Runs only on the S3 behaviour, so /api/* can never be rewritten
# — which is the point: the previous approach (a distribution-wide error page)
# also rewrote failed API responses into an HTML 200.
resource "aws_cloudfront_function" "spa_routing" {
  name    = "${local.prefix}-spa-routing"
  runtime = "cloudfront-js-2.0"
  comment = "Serve index.html for client-side routes."
  publish = true

  code = <<-JS
    function handler(event) {
      var uri = event.request.uri;

      // A trailing slash or a segment with no dot is a client-side route,
      // not a file. Anything with an extension (/assets/x.js, /icon.svg)
      // falls through to S3 untouched.
      if (uri.endsWith('/')) {
        event.request.uri = '/index.html';
      } else if (!uri.split('/').pop().includes('.')) {
        event.request.uri = '/index.html';
      }

      return event.request;
    }
  JS
}

resource "aws_cloudfront_origin_access_control" "s3" {
  name                              = "${local.prefix}-s3"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# OAC works for Lambda Function URLs too, which is what keeps the streaming
# endpoint private while still reachable through the distribution.
resource "aws_cloudfront_origin_access_control" "lambda" {
  name                              = "${local.prefix}-lambda"
  origin_access_control_origin_type = "lambda"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# --- cache policies --------------------------------------------------------

# The API must never be cached, and must forward the Authorization header so
# the Lambda can validate the Cognito token.
resource "aws_cloudfront_cache_policy" "api" {
  name        = "${local.prefix}-api-no-cache"
  default_ttl = 0
  min_ttl     = 0
  max_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = false
    enable_accept_encoding_brotli = false
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_origin_request_policy" "api" {
  name = "${local.prefix}-api-forward"

  cookies_config {
    cookie_behavior = "none"
  }
  headers_config {
    header_behavior = "whitelist"
    headers {
      # Neither Host nor Authorization may be forwarded, and both omissions
      # are load-bearing:
      #
      #   Host          SigV4 is computed over the ORIGIN's host. Forwarding
      #                 the viewer's host invalidates the signature.
      #   Authorization CloudFront's OAC puts its own SigV4 signature in this
      #                 header. Forwarding the viewer's would collide with it,
      #                 and the origin rejects every request with 403.
      #
      # The second is why the app's own bearer token travels as X-Id-Token
      # instead — see api/auth.py. Using OAC on a Function URL costs you the
      # Authorization header for application purposes.
      # x-amz-content-sha256 is deliberately NOT listed: CloudFront reserves
      # it and rejects the policy outright. It reads the viewer's value and
      # folds it into the signature itself. The browser must still SEND it on
      # POST/PUT — Lambda refuses unsigned payloads — it just is not a
      # forwarded header. /api/chat is a POST, so see web/src/api.ts.
      items = ["Content-Type", "Accept", "X-Id-Token"]
    }
  }
  query_strings_config {
    query_string_behavior = "all"
  }
}

# --- the distribution ------------------------------------------------------

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  comment             = "${local.prefix} v2"
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # NA + EU is plenty for one user in Denmark

  origin {
    origin_id                = "s3"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
  }

  origin {
    origin_id                = "lambda"
    domain_name              = replace(replace(aws_lambda_function_url.api.function_url, "https://", ""), "/", "")
    origin_access_control_id = aws_cloudfront_origin_access_control.lambda.id

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      # Model turns run long; do not cut the stream off mid-answer.
      origin_read_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # AWS managed "CachingOptimized"
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_routing.arn
    }
  }

  ordered_cache_behavior {
    path_pattern           = "/api/*"
    target_origin_id       = "lambda"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    # Compression must stay off: it buffers the response to compress it,
    # which would defeat the token-by-token streaming this design is built
    # around.
    compress                 = false
    cache_policy_id          = aws_cloudfront_cache_policy.api.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id
  }

  # No custom_error_response here, deliberately. It applies to the whole
  # distribution, so mapping 403 -> 200 /index.html also swallowed genuine
  # API failures: a broken origin returned HTTP 200 and an HTML page instead
  # of an error, which is precisely how a 403 from the Lambda origin went
  # unnoticed. SPA routing is handled by the viewer-request function on the
  # S3 behaviour instead, which cannot touch /api/*.

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # The default *.cloudfront.net certificate. A custom domain would need an
    # ACM certificate in us-east-1; deliberately deferred.
    cloudfront_default_certificate = true
  }
}

# --- origin policies -------------------------------------------------------

data "aws_iam_policy_document" "site_bucket" {
  statement {
    sid       = "AllowCloudFrontRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    # Scoped to this distribution, so another account's CloudFront cannot
    # read the bucket even if it somehow guessed the name.
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.main.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_bucket.json
}

# Lets this distribution — and nothing else — invoke the Function URL.
#
# BOTH statements are required. Granting only InvokeFunctionUrl leaves every
# request failing with a bare 403 from the function URL, with nothing in the
# Lambda log because the call never reaches the function. The second grant is
# easy to miss: it is a separate add-permission call in the AWS docs, and the
# error gives no hint that a second action exists.
resource "aws_lambda_permission" "cloudfront_url" {
  statement_id           = "AllowCloudFrontInvokeUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "cloudfront.amazonaws.com"
  source_arn             = aws_cloudfront_distribution.main.arn
  function_url_auth_type = "AWS_IAM"
}

resource "aws_lambda_permission" "cloudfront_invoke" {
  statement_id  = "AllowCloudFrontInvokeFunction"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "cloudfront.amazonaws.com"
  source_arn    = aws_cloudfront_distribution.main.arn
  # No function_url_auth_type here: Lambda accepts that qualifier only on
  # lambda:InvokeFunctionUrl and rejects the whole call otherwise.
}
