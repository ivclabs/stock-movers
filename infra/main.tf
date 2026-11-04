# =============================================================================
# main.tf - everything Stock Movers needs in AWS, in one file
# =============================================================================
#
#   EventBridge Scheduler ──▶ Lambda "pipeline" (every 20 min, pre/after-hours)
#                        └──▶ Lambda "system"   (once a day, at night)
#                                   │ write results.json / system.json
#                                   ▼
#                              S3 bucket (private) ◀── index.html, style.css, app.js
#                                   │ read only by CloudFront
#                                   ▼
#                              CloudFront (https) ──▶ visitors
#
# `terraform apply` creates/updates all of it, `terraform destroy` deletes all of it.
# Use ../deploy.sh, which builds the Lambda code and passes your keys in.
# =============================================================================

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region = var.region
  # Every resource gets this tag, so you can find them all in the AWS console
  default_tags {
    tags = { Project = "stock-movers" }
  }
}

locals {
  name        = "stock-movers"
  project_dir = "${path.module}/.."
}

# Bucket names must be unique in ALL of AWS, so we add a random ending
resource "random_id" "suffix" {
  byte_length = 4
}

# -----------------------------------------------------------------------------
# 1. S3 bucket: holds the website files and the data files
# -----------------------------------------------------------------------------
resource "aws_s3_bucket" "site" {
  bucket        = "${local.name}-${random_id.suffix.hex}"
  force_destroy = true # lets `terraform destroy` delete it even when it has files
}

# Private: nobody can read the bucket directly, only CloudFront (see policy below)
resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The 3 website files. Terraform re-uploads a file when it changes (etag = fingerprint).
# results.json and system.json are NOT here: the Lambdas write those.
locals {
  site_files = {
    "index.html" = "text/html; charset=utf-8"
    "style.css"  = "text/css; charset=utf-8"
    "app.js"     = "text/javascript; charset=utf-8"
  }
}

resource "aws_s3_object" "site" {
  for_each      = local.site_files
  bucket        = aws_s3_bucket.site.id
  key           = each.key
  source        = "${local.project_dir}/${each.key}"
  content_type  = each.value
  cache_control = "max-age=300" # browsers/CloudFront re-check after 5 min
  etag          = filemd5("${local.project_dir}/${each.key}")
}

# -----------------------------------------------------------------------------
# 2. CloudFront: the https address visitors use
# -----------------------------------------------------------------------------
# "Origin Access Control" = CloudFront signs its requests to S3, so the bucket
# can stay private and still be read by CloudFront.
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = aws_s3_bucket.site.bucket
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# AWS's ready-made cache settings. They respect the files' Cache-Control,
# so results.json (max-age=60) is refreshed every minute. Also turns on gzip.
data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "Stock Movers website"
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # Europe + North America edge locations (cheapest)

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3-site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-site"
    viewer_protocol_policy = "redirect-to-https" # http:// visitors are sent to https://
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # free https on *.cloudfront.net
  }
}

# Bucket policy: "CloudFront (only THIS distribution) may read files"
data "aws_iam_policy_document" "site_bucket" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket     = aws_s3_bucket.site.id
  policy     = data.aws_iam_policy_document.site_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.site]
}

# -----------------------------------------------------------------------------
# 3. Lambda functions: run our Python scripts
# -----------------------------------------------------------------------------
# Zip of build/package (made by ../build_lambda.sh)
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${local.project_dir}/build/package"
  output_path = "${local.project_dir}/build/lambda.zip"
}

# The "role" = what the Lambdas are allowed to do
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# Allowed: write logs (to see print() output in CloudWatch) ...
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ... and read/write files in OUR bucket only
data "aws_iam_policy_document" "lambda_s3" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket"] # so a missing file says "not found" instead of "denied"
    resources = [aws_s3_bucket.site.arn]
  }
}

resource "aws_iam_role_policy" "lambda_s3" {
  name   = "s3-site-bucket"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_s3.json
}

# Logs are kept 14 days (default is forever, which slowly costs money)
resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/lambda/${local.name}-pipeline"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "system" {
  name              = "/aws/lambda/${local.name}-system"
  retention_in_days = 14
}

resource "aws_lambda_function" "pipeline" {
  function_name    = "${local.name}-pipeline"
  description      = "movers -> news -> judge -> charts, writes results.json"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  handler          = "lambda_function.pipeline_handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256 # re-upload when code changes
  timeout          = 300                                          # seconds; a normal run takes 1-2 min
  memory_size      = 512

  environment {
    variables = {
      BUCKET            = aws_s3_bucket.site.id
      APCA_KEY_ID       = var.apca_key_id
      APCA_SECRET       = var.apca_secret
      ANTHROPIC_API_KEY = var.anthropic_api_key
    }
  }

  depends_on = [aws_cloudwatch_log_group.pipeline, aws_iam_role_policy_attachment.lambda_logs]
}

resource "aws_lambda_function" "system" {
  function_name    = "${local.name}-system"
  description      = "Daily System scan of the whole market, writes system.json"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  handler          = "lambda_function.system_handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 900  # the maximum (15 min); the scan takes ~3 min
  memory_size      = 2048 # it holds ~7,000 stocks x 170 candles in memory

  environment {
    variables = {
      BUCKET      = aws_s3_bucket.site.id
      APCA_KEY_ID = var.apca_key_id
      APCA_SECRET = var.apca_secret
    }
  }

  depends_on = [aws_cloudwatch_log_group.system, aws_iam_role_policy_attachment.lambda_logs]
}

# -----------------------------------------------------------------------------
# 4. EventBridge Scheduler: starts the Lambdas on time
# -----------------------------------------------------------------------------
# Times are US market time (America/New_York), so summer/winter time is handled.
# cron(minutes hours day-of-month month day-of-week year)
locals {
  schedules = {
    premarket = {
      cron        = "cron(0/20 4-9 ? * MON-FRI *)" # 04:00, 04:20 ... 09:40 (the code skips 04:00 and 09:40)
      function    = aws_lambda_function.pipeline.arn
      description = "Pre-market movers, every 20 min"
    }
    afterhours = {
      cron        = "cron(0/20 16-19 ? * MON-FRI *)" # 16:00, 16:20 ... 19:40 (the code skips 16:00)
      function    = aws_lambda_function.pipeline.arn
      description = "After-hours movers, every 20 min"
    }
    system = {
      cron        = "cron(0 2 ? * TUE-SAT *)" # 02:00 the night after each trading day
      function    = aws_lambda_function.system.arn
      description = "Daily System scan of yesterday's candles"
    }
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

# The scheduler may start our 2 Lambdas, nothing else
data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.pipeline.arn, aws_lambda_function.system.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name   = "invoke-lambdas"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

resource "aws_scheduler_schedule" "run" {
  for_each                     = local.schedules
  name                         = "${local.name}-${each.key}"
  description                  = each.value.description
  schedule_expression          = each.value.cron
  schedule_expression_timezone = "America/New_York"

  flexible_time_window {
    mode = "OFF" # start exactly on time
  }

  target {
    arn      = each.value.function
    role_arn = aws_iam_role.scheduler.arn
    retry_policy {
      maximum_retry_attempts = 0 # a failed run just waits for the next one (no double AI costs)
    }
  }
}

# -----------------------------------------------------------------------------
# 5. Billing alarm: an email when the WHOLE AWS account costs too much
# -----------------------------------------------------------------------------
#   spending this month ──▶ $4 (80%) reached        ──▶ email
#                      └──▶ forecast says > $5 (100%) ──▶ email
# AWS checks costs a few times a day, so the email can come some hours late.
# The first 2 budgets in an account are free.
resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL" # money already spent
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED" # AWS's guess for the end of the month
    subscriber_email_addresses = [var.alert_email]
  }
}
