output "lambda_function_arn" {
  description = "ARN задеплоенной функции"
  value       = aws_lambda_function.bot.arn
}

output "lambda_last_modified" {
  description = "Метка времени последнего обновления кода — удобно сверять после деплоя"
  value       = aws_lambda_function.bot.last_modified
}
