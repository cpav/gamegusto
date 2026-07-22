# ---------------------------------------------------------------------------
# The API: FastAPI on Lambda, streaming.
#
# The whole shape of this file follows from one constraint. PR #54 made the
# agent stream model output token by token, and that only survives to the
# browser if every hop forwards chunks as they arrive. Two hops would have
# broken it:
#
#   * API Gateway buffers the response body, so it is not used at all — the
#     function is exposed through a Function URL in RESPONSE_STREAM mode.
#   * Mangum, the usual FastAPI-on-Lambda adapter, returns a single buffered
#     response. Instead the Lambda Web Adapter runs the app as an ordinary
#     uvicorn server and streams through it.
#
# The Function URL is NOT public: auth_type is AWS_IAM and only CloudFront's
# Origin Access Control may sign requests to it (see cloudfront.tf).
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "artifacts" {
  bucket = "${local.prefix}-artifacts-${local.account}"
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Old bundles are worth keeping briefly — rolling back is then a version
# change rather than a rebuild — but not forever.
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-old-bundles"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

locals {
  api_zip = "${path.module}/../../dist/api.zip"
}

resource "aws_s3_object" "api_zip" {
  bucket = aws_s3_bucket.artifacts.id
  # The hash in the key makes each build a distinct object, so Lambda always
  # sees a changed source and never serves stale code from a cached key.
  key    = "api/${filesha256(local.api_zip)}.zip"
  source = local.api_zip
  etag   = filemd5(local.api_zip)
}

resource "aws_lambda_function" "api" {
  function_name = "${local.prefix}-api"
  role          = aws_iam_role.lambda.arn

  s3_bucket        = aws_s3_bucket.artifacts.id
  s3_key           = aws_s3_object.api_zip.key
  source_code_hash = filebase64sha256(local.api_zip)

  runtime = "python3.13"
  # Ignored by the Lambda Web Adapter, which runs run.sh instead, but the
  # runtime still requires the field to be set.
  handler = "run.sh"

  # Bedrock turns take tens of seconds with tool calls; the model, not the
  # function, is the slow part. 512 MB is ample — this is I/O bound, though
  # more memory also means more CPU, which shortens cold starts.
  timeout     = 300
  memory_size = 512

  layers = [
    "arn:aws:lambda:${var.aws_region}:753240598075:layer:LambdaAdapterLayerX86:${var.lwa_layer_version}",
  ]

  environment {
    variables = {
      # Lambda Web Adapter
      AWS_LAMBDA_EXEC_WRAPPER = "/opt/bootstrap"
      AWS_LWA_INVOKE_MODE     = "response_stream" # the reason LWA is here
      AWS_LWA_PORT            = "8000"
      # Readiness: LWA waits for the app to answer before forwarding traffic.
      AWS_LWA_READINESS_CHECK_PATH = "/api/health"

      # Application. AWS_REGION is not set here — Lambda provides it, and it
      # is a reserved key the runtime rejects.
      BEDROCK_MODEL_ID    = var.bedrock_model_id
      DYNAMODB_TABLE_NAME = data.aws_dynamodb_table.library.name
      DEALS_REGION        = var.deals_region
      BRAVE_PARAMETER    = aws_ssm_parameter.brave_api_key.name
      IGDB_PARAMETER      = aws_ssm_parameter.igdb_credentials.name

      # Only the pool id. The client id is deliberately absent: taking it here
      # would close a dependency cycle — the Cognito client needs CloudFront's
      # domain for its callback URL, CloudFront needs the Function URL, and
      # the function would need the client.
      #
      # Nothing is lost. Validation checks the signature against the pool's
      # JWKS, that `iss` is this pool, and that `token_use` is "access". With
      # exactly one client in the pool, the issuer check already pins the
      # audience; a client_id claim check would be a tautology. If a second
      # client is ever added, pass the id through SSM by its static name
      # rather than by a resource reference.
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.main.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]
}

resource "aws_lambda_function_url" "api" {
  function_name = aws_lambda_function.api.function_name

  # Not public. CloudFront signs requests with SigV4 through its Origin
  # Access Control; a direct hit on this URL is unsigned and rejected.
  authorization_type = "AWS_IAM"

  # The setting this entire design exists to enable.
  invoke_mode = "RESPONSE_STREAM"
}

variable "lwa_layer_version" {
  description = <<-EOT
    Lambda Web Adapter layer version. Pinned rather than floating so a
    redeploy cannot silently change the component that carries the response
    stream. AWS publishes no list permission on the layer, so newer versions
    are found by probing get-layer-version-by-arn.
  EOT
  type        = number
  default     = 29
}

variable "deals_region" {
  description = "Natural-language region for store deals. Pinned rather than browser-detected on the server."
  type        = string
  default     = "Denmark"
}
