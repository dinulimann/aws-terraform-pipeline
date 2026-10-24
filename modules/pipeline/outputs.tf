output "landing_bucket" { value = aws_s3_bucket.landing.id }
output "lake_bucket" { value = aws_s3_bucket.lake.id }
output "table_name" { value = aws_dynamodb_table.pipeline.name }
output "dlq_url" { value = aws_sqs_queue.ingest_dlq.url }
output "ingest_function" { value = aws_lambda_function.ingest.function_name }
output "report_function" { value = aws_lambda_function.report.function_name }
output "report_rule" { value = aws_cloudwatch_event_rule.daily_report.name }
output "iam_policies" {
  value = {
    ingest = jsondecode(data.aws_iam_policy_document.ingest.json)
    report = jsondecode(data.aws_iam_policy_document.report.json)
  }
}
