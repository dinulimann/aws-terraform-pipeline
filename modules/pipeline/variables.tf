variable "name_prefix" {
  description = "Prefix for every resource name. Bucket names must be globally unique on real AWS."
  type        = string
}

variable "async_retries" {
  description = "Lambda retries for a failed S3 event before it goes to the dead-letter queue (0-2)."
  type        = number
  default     = 2
}

variable "report_schedule" {
  description = "When the daily report runs. 17:10 UTC is 00:10 in Jakarta (WIB, UTC+7)."
  type        = string
  default     = "cron(10 17 * * ? *)"
}

variable "landing_retention_days" {
  description = "Raw uploads are deleted from the landing bucket after this many days."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete non-empty buckets. Only for throwaway environments."
  type        = bool
  default     = false
}
