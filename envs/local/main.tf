# LocalStack (community 4.14.0, see docker-compose.yml). Same module as envs/aws.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

variable "endpoint" {
  type    = string
  default = "http://localhost:4566"
}

provider "aws" {
  region                      = "ap-southeast-3" # Jakarta
  access_key                  = "test"
  secret_key                  = "test"
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    cloudwatchlogs = var.endpoint
    dynamodb       = var.endpoint
    events         = var.endpoint
    iam            = var.endpoint
    lambda         = var.endpoint
    s3             = var.endpoint
    sqs            = var.endpoint
    sts            = var.endpoint
  }
}

module "pipeline" {
  source        = "../../modules/pipeline"
  name_prefix   = "store-sales"
  async_retries = 0 # fail straight to the DLQ so tests do not wait for retry backoff
  force_destroy = true
}

output "pipeline" {
  value = module.pipeline
}
