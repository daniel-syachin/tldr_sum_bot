variable "aws_region" {
  description = "Регион, в котором живёт Lambda"
  type        = string
  default     = "eu-central-1"
}

variable "function_name" {
  description = "Имя существующей Lambda-функции"
  type        = string
  default     = "telegram-summarizer-bot"
}

variable "lambda_role_name" {
  description = "Имя существующей IAM-роли исполнения Lambda (создана консолью, не Terraform)"
  type        = string
  default     = "telegram-summarizer-bot-role-hgo9fvd7"
}

variable "lambda_layer_arn" {
  description = "ARN существующего Lambda Layer с зависимостями (httpx, anthropic и т.д.)"
  type        = string
  default     = "arn:aws:lambda:eu-central-1:456824039329:layer:telegram-summarizer-bot-layer:1"
}
