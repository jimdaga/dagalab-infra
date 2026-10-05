output "kms_key_alias" {
  value = aws_kms_alias.vault_unseal.name
}

output "iam_user" {
  value = aws_iam_user.vault_unseal.name
}
