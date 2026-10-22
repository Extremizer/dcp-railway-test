variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "producer_ami" {
  type        = string
  description = "Explicit immutable, independently reviewed and pinned Linux AMI. Provenance NOT VERIFIED by IaC."
  validation {
    condition     = can(regex("^ami-[a-f0-9]{8,17}$", var.producer_ami))
    error_message = "Reviewed AMI ID required"
  }
}

variable "mock_only_approval" {
  type    = bool
  default = false
}
