# The execution role, API Gateway and webhook are already set up by hand and
# working. Terraform doesn't touch or import them: this stack's only job is
# to manage the Lambda function's code/config via CI.
data "aws_iam_role" "existing" {
  name = var.lambda_role_name
}

# Assembled by GitHub Actions before `terraform apply`: cp src/lambda_function.py build/
# Dependencies (httpx, anthropic, etc.) are NOT in this zip — they already ship
# in a separate Lambda Layer (see lambda_layer_arn below), no need to rebuild them.
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

  # Matches the function's current state in AWS — without this block Terraform
  # tries to remove it on every plan (harmless, but noisy in the diff).
  snap_start {
    apply_on = "PublishedVersions"
  }

  lifecycle {
    # Environment variables (tokens) are set by hand in the AWS console —
    # Terraform must never read/overwrite/clear them.
    ignore_changes = [environment]
  }
}
