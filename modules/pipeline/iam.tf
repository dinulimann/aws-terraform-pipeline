# One role per function, each limited to the exact buckets/prefixes, table,
# queue and log group it touches. No wildcard actions, no "*" resources.
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ingest" {
  statement {
    sid       = "ReadUploads"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.landing.arn}/incoming/*"]
  }
  statement {
    sid     = "WriteCuratedAndQuarantine"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/curated/*",
      "${aws_s3_bucket.lake.arn}/quarantine/*",
    ]
  }
  statement {
    sid       = "RecordCounters"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.pipeline.arn]
  }
  statement {
    sid       = "DeadLetter"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_dlq.arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }
}

data "aws_iam_policy_document" "report" {
  statement {
    sid       = "ReadCounters"
    actions   = ["dynamodb:Query"]
    resources = [aws_dynamodb_table.pipeline.arn]
  }
  statement {
    sid       = "WriteReports"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/reports/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.report.arn}:*"]
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${var.name_prefix}-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_iam_role" "report" {
  name               = "${var.name_prefix}-report"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "report" {
  name   = "report"
  role   = aws_iam_role.report.id
  policy = data.aws_iam_policy_document.report.json
}
