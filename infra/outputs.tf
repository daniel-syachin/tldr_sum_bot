output "lambda_function_arn" {
  description = "ARN of the deployed function"
  value       = aws_lambda_function.bot.arn
}

output "lambda_last_modified" {
  description = "Timestamp of the last code update — handy to check after a deploy"
  value       = aws_lambda_function.bot.last_modified
}
