terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.100"
    }
  }

  # State lives in the bucket bootstrap created. Native S3 locking (1.11+),
  # so there is no DynamoDB lock table to own.
  backend "s3" {
    bucket       = "gamegusto-tfstate-135878023361"
    key          = "stack/terraform.tfstate"
    region       = "eu-north-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "gamegusto"
      ManagedBy = "terraform/stack"
    }
  }
}

data "aws_caller_identity" "current" {}

# The live library. Referenced, never managed — the deploy role is allowed
# DescribeTable on it and explicitly denied everything else, so Terraform
# cannot reshape or delete the data. See infra/bootstrap/deploy.tf.
data "aws_dynamodb_table" "library" {
  name = var.library_table_name
}

locals {
  account = data.aws_caller_identity.current.account_id
  prefix  = var.name_prefix
}
