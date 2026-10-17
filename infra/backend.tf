terraform {
  # 1.11 is where S3-native state locking (use_lockfile) became GA
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  backend "s3" {
    bucket = "tfstate-tgbot-456824039329-eu-north-1-an"
    key    = "tg-bot/terraform.tfstate"
    region = "eu-north-1"

    # Two overlapping applies (two quick pushes, or a local apply during a
    # CI run) could otherwise both write the state and corrupt it. This
    # takes a lock object next to the state (<key>.tflock) — no DynamoDB
    # table needed.
    use_lockfile = true
  }
}
