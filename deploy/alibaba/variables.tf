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

variable "key_name" {
  description = "ECS SSH key pair name (optional)"
  type        = string
  default     = ""
}

variable "ssh_cidr" {
  description = "CIDR block allowed to SSH to the ECS instance (required; set to your IP/32)"
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "ECS instance type"
  type        = string
  default     = "ecs.c6.large"
}

variable "image_id" {
  description = "Ubuntu 22.04 image ID"
  type        = string
  default     = "ubuntu_22_04_x64_20G_alibase_20230627.vhd"
}

variable "db_instance_class" {
  description = "RDS PostgreSQL instance class"
  type        = string
  default     = "pg.n2.medium.1"
}

variable "db_storage" {
  description = "RDS storage in GB"
  type        = number
  default     = 50
}

variable "db_user" {
  description = "RDS master username"
  type        = string
  default     = "triagetrace"
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "qwen_api_key" {
  description = "DashScope/Qwen Cloud API key"
  type        = string
  sensitive   = true
}
