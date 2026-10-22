variable "region" { type = string default = "eu-central-1" }
variable "mock_image_digest" {
  type = string
  description = "Approved private ECR image digest, no floating tags"
  validation {
    condition = can(regex("^sha256:[a-f0-9]{64}$",var.mock_image_digest))
    error_message = "Image must be SHA256-pinned"
  }
}
variable "producer_ami" {
  type = string
  description = "Reviewed immutable Linux AMI ID; must be supplied explicitly"
  validation {
    condition = can(regex("^ami-[a-f0-9]{8,17}$",var.producer_ami))
    error_message = "Immutable AMI ID required"
  }
}
variable "controller_private_ip" { type = string default = "10.73.10.10" }
variable "mock_only_approval" { type = bool default = false }
