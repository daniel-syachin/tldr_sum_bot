# Роль исполнения, API Gateway, вебхук — уже настроены руками и работают.
# Terraform их не трогает и не импортирует: единственная задача этого стека —
# управляемо (через CI) обновлять код и конфиг самой Lambda-функции.
data "aws_iam_role" "existing" {
  name = var.lambda_role_name
}

# Собирается GitHub Actions перед `terraform apply`: cp src/lambda_function.py build/
# Зависимости (httpx, anthropic и т.д.) НЕ кладутся в этот zip — они уже едут
# отдельным Lambda Layer (см. lambda_layer_arn ниже), собирать их заново не нужно.
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../build"
  output_path = "${path.module}/../build.zip"
}

resource "aws_lambda_function" "bot" {
  function_name    = var.function_name
  role             = data.aws_iam_role.existing.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.14"
  timeout          = 240
  memory_size      = 512
  layers           = [var.lambda_layer_arn]
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  # Соответствует текущему состоянию в AWS — без этого блока Terraform на
  # каждом plan пытается его снять (безобидно, но шумно в диффе).
  snap_start {
    apply_on = "PublishedVersions"
  }

  lifecycle {
    # Переменные окружения (токены) заданы вручную в консоли AWS —
    # Terraform не должен их читать/перезаписывать/стирать.
    ignore_changes = [environment]
  }
}
