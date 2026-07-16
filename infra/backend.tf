terraform {
  required_version = ">= 1.5"

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
  }
}
