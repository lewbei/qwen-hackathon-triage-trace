variable "region" {
  description = "Alibaba Cloud region"
  type        = string
  default     = "cn-hongkong"
}

variable "zone_id" {
  description = "Availability zone for ECS and VSwitch"
  type        = string
  default     = "cn-hongkong-b"
}

variable "vpc_cidr" {
  description = "CIDR block for the TriageTrace VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "ssh_cidr" {
  description = "CIDR block allowed to SSH to the ECS instance (required; set to your IP/32)"
  type        = string
  default     = ""
}

variable "key_name" {
  description = "ECS SSH key pair name (optional)"
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "ECS instance type"
  type        = string
  default     = "ecs.c6.large"
}

variable "image_id" {
  description = "ECS image ID override; if empty, the latest official Ubuntu 22.04 image is used"
  type        = string
  default     = ""
}

variable "db_user" {
  description = "Internal PostgreSQL username for the ECS-hosted pgvector container"
  type        = string
  default     = "postgres"
}

variable "qwen_api_key" {
  description = "DashScope/Qwen Cloud API key"
  type        = string
  sensitive   = true
}
