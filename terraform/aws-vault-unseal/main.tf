# KMS key Vault uses to auto-unseal, and a least-privilege IAM user for it.
# The access key is created out-of-band (see README) so it never lands in Terraform state.

terraform {
  required_version = ">= 1.16"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67"
    }
  }
  # TODO: remote state (S3) once there's more than this one stack
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project    = "dagalab"
      managed-by = "terraform"
    }
  }
}

resource "aws_kms_key" "vault_unseal" {
  description             = "dagalab Vault auto-unseal"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "vault_unseal" {
  name          = "alias/dagalab-vault-unseal"
  target_key_id = aws_kms_key.vault_unseal.key_id
}

resource "aws_iam_user" "vault_unseal" {
  name = "dagalab-vault-unseal"
}

data "aws_iam_policy_document" "vault_unseal" {
  statement {
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:DescribeKey"]
    resources = [aws_kms_key.vault_unseal.arn]
  }
}

resource "aws_iam_user_policy" "vault_unseal" {
  name   = "vault-unseal"
  user   = aws_iam_user.vault_unseal.name
  policy = data.aws_iam_policy_document.vault_unseal.json
}
