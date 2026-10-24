data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../../src/ingest"
  output_path = "${path.module}/../../build/ingest.zip"
  excludes    = ["__pycache__"]
}

data "archive_file" "report" {
  type        = "zip"
  source_dir  = "${path.module}/../../src/report"
  output_path = "${path.module}/../../build/report.zip"
  excludes    = ["__pycache__"]
}

# Log groups are created here (not implicitly by Lambda) so retention is set
# and the IAM policies can name them exactly.
resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${var.name_prefix}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "report" {
  name              = "/aws/lambda/${var.name_prefix}-report"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${var.name_prefix}-ingest"
  role             = aws_iam_role.ingest.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  timeout          = 120
  memory_size      = 512
  environment {
    variables = {
      LAKE_BUCKET = aws_s3_bucket.lake.id
      TABLE_NAME  = aws_dynamodb_table.pipeline.name
    }
  }
  dead_letter_config {
    target_arn = aws_sqs_queue.ingest_dlq.arn
  }
  depends_on = [aws_cloudwatch_log_group.ingest, aws_iam_role_policy.ingest]
}

resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name          = aws_lambda_function.ingest.function_name
  maximum_retry_attempts = var.async_retries
}

resource "aws_lambda_permission" "s3_invoke_ingest" {
  statement_id   = "AllowLandingBucket"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.ingest.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.landing.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_lambda_function" "report" {
  function_name    = "${var.name_prefix}-report"
  role             = aws_iam_role.report.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.report.output_path
  source_code_hash = data.archive_file.report.output_base64sha256
  timeout          = 60
  memory_size      = 256
  environment {
    variables = {
      LAKE_BUCKET = aws_s3_bucket.lake.id
      TABLE_NAME  = aws_dynamodb_table.pipeline.name
    }
  }
  depends_on = [aws_cloudwatch_log_group.report, aws_iam_role_policy.report]
}

resource "aws_cloudwatch_event_rule" "daily_report" {
  name                = "${var.name_prefix}-daily-report"
  description         = "Daily sales and ingest report for the previous Jakarta day"
  schedule_expression = var.report_schedule
}

resource "aws_cloudwatch_event_target" "daily_report" {
  rule = aws_cloudwatch_event_rule.daily_report.name
  arn  = aws_lambda_function.report.arn
}

resource "aws_lambda_permission" "events_invoke_report" {
  statement_id  = "AllowDailyReportRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.report.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_report.arn
}
