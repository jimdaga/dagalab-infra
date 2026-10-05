variable "region" {
  description = "AWS region for the KMS key (must match seal \"awskms\" region in argocd/apps/vault/values.yaml)"
  type        = string
  default     = "us-east-1"
}
