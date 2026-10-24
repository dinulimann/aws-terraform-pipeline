# Real AWS. Validated in CI (terraform validate), not applied by this repo.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  # Use remote state for a shared environment, e.g.:
  # backend "s3" {
  #   bucket       = "my-terraform-state"
  #   key          = "store-sales/terraform.tfstate"
  #   region       = "ap-southeast-3"
  #   use_lockfile = true
  # }
}

variable "region" {
  type    = string
  default = "ap-southeast-3"
}

variable "name_prefix" {
  description = "Must make the bucket names globally unique, e.g. include the account or company."
  type        = string
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "store-sales-pipeline", managed_by = "terraform" }
  }
}

module "pipeline" {
  source      = "../../modules/pipeline"
  name_prefix = var.name_prefix
}

output "pipeline" {
  value = module.pipeline
}
