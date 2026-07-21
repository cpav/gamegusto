terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # Local state on purpose: this module CREATES the remote state bucket that
  # every other stack uses, so it cannot store its own state there. The state
  # file is git-ignored. Losing it is recoverable — the resources here are
  # stable and importable — so it is not worth the chicken-and-egg dance of
  # migrating this module's own backend.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "gamegusto"
      ManagedBy = "terraform/bootstrap"
    }
  }
}
