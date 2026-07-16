variable "aws_region" {
  description = "Region the Lambda function lives in"
  type        = string
  default     = "eu-central-1"
}

variable "function_name" {
  description = "Name of the existing Lambda function"
  type        = string
  default     = "telegram-summarizer-bot"
}

variable "lambda_role_name" {
  description = "Name of the existing Lambda execution role (created via the console, not Terraform)"
  type        = string
  default     = "telegram-summarizer-bot-role-hgo9fvd7"
}

variable "lambda_layer_arn" {
  description = "ARN of the existing Lambda Layer with dependencies (httpx, anthropic, etc.)"
  type        = string
  default     = "arn:aws:lambda:eu-central-1:456824039329:layer:telegram-summarizer-bot-layer:1"
}
