# landing: stores upload here (incoming/<STORE>/<name>.csv)
# lake:    curated/, quarantine/ and reports/ written by the Lambdas
resource "aws_s3_bucket" "landing" {
  bucket        = "${var.name_prefix}-landing"
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket" "lake" {
  bucket        = "${var.name_prefix}-lake"
  force_destroy = var.force_destroy
}

locals {
  buckets = { landing = aws_s3_bucket.landing.id, lake = aws_s3_bucket.lake.id }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = each.value
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id
  rule {
    id     = "expire-raw-uploads"
    status = "Enabled"
    filter { prefix = "incoming/" }
    expiration { days = var.landing_retention_days }
  }
}

resource "aws_s3_bucket_notification" "landing" {
  bucket = aws_s3_bucket.landing.id
  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "incoming/"
    filter_suffix       = ".csv"
  }
  depends_on = [aws_lambda_permission.s3_invoke_ingest]
}

# Single table:
#   SALES#<date>   / STORE#<id>          per-day counters (ADD, so files accumulate)
#   INGEST#<date>  / FILE#<src>#<etag>   one entry per processed file
#   FILE#<src>     / ETAG#<etag>         idempotency marker
resource "aws_dynamodb_table" "pipeline" {
  name         = "${var.name_prefix}-pipeline"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
}

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${var.name_prefix}-ingest-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true
}
