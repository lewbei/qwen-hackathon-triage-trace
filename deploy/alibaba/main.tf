terraform {
  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.220"
    }
  }
}

provider "alicloud" {
  region = var.region
}

# Networking
resource "alicloud_vpc" "triagetrace" {
  vpc_name   = "triagetrace-vpc"
  cidr_block = var.vpc_cidr
}

resource "alicloud_vswitch" "triagetrace" {
  vswitch_name = "triagetrace-vs"
  vpc_id       = alicloud_vpc.triagetrace.id
  cidr_block   = cidrsubnet(var.vpc_cidr, 8, 1)
  zone_id      = var.zone_id
}

# Security group allowing public HTTP/HTTPS and API/dashboard ports.
resource "alicloud_security_group" "triagetrace" {
  name   = "triagetrace-sg"
  vpc_id = alicloud_vpc.triagetrace.id
}

resource "alicloud_security_group_rule" "ssh" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "internet"
  policy            = "accept"
  port_range        = "22/22"
  priority          = 1
  security_group_id = alicloud_security_group.triagetrace.id
  cidr_ip           = "0.0.0.0/0"
}

resource "alicloud_security_group_rule" "http" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "internet"
  policy            = "accept"
  port_range        = "80/80"
  priority          = 1
  security_group_id = alicloud_security_group.triagetrace.id
  cidr_ip           = "0.0.0.0/0"
}

resource "alicloud_security_group_rule" "https" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "internet"
  policy            = "accept"
  port_range        = "443/443"
  priority          = 1
  security_group_id = alicloud_security_group.triagetrace.id
  cidr_ip           = "0.0.0.0/0"
}

resource "alicloud_security_group_rule" "api" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "internet"
  policy            = "accept"
  port_range        = "8000/8000"
  priority          = 1
  security_group_id = alicloud_security_group.triagetrace.id
  cidr_ip           = "0.0.0.0/0"
}

resource "alicloud_security_group_rule" "ui" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "internet"
  policy            = "accept"
  port_range        = "5173/5173"
  priority          = 1
  security_group_id = alicloud_security_group.triagetrace.id
  cidr_ip           = "0.0.0.0/0"
}

# RDS PostgreSQL with pgvector
resource "alicloud_db_instance" "triagetrace" {
  engine            = "PostgreSQL"
  engine_version    = "15.0"
  instance_type     = "Primary"
  instance_class    = var.db_instance_class
  instance_storage  = var.db_storage
  vswitch_id        = alicloud_vswitch.triagetrace.id
  security_ips      = [alicloud_vpc.triagetrace.cidr_block]
}

resource "alicloud_db_database" "triagetrace" {
  instance_id = alicloud_db_instance.triagetrace.id
  name        = "triagetrace"
  character_set = "UTF8"
}

resource "alicloud_db_account" "triagetrace" {
  db_instance_id   = alicloud_db_instance.triagetrace.id
  account_name     = var.db_user
  account_password = var.db_password
}

# ECS instance running backend + frontend
resource "alicloud_instance" "triagetrace" {
  image_id             = var.image_id
  instance_type        = var.instance_type
  security_groups      = [alicloud_security_group.triagetrace.id]
  vswitch_id           = alicloud_vswitch.triagetrace.id
  instance_name        = "triagetrace-api"
  system_disk_category = "cloud_essd"
  system_disk_size     = 40
  internet_charge_type = "PayByTraffic"
  internet_max_bandwidth_out = 100
  key_name             = var.key_name

  user_data = base64encode(templatefile("${path.module}/cloud-init.sh", {
    db_host     = alicloud_db_instance.triagetrace.connection_string
    db_name     = alicloud_db_database.triagetrace.name
    db_user     = var.db_user
    db_password = var.db_password
    qwen_api_key = var.qwen_api_key
  }))
}

resource "alicloud_eip_address" "triagetrace" {
  address_name         = "triagetrace-eip"
  isp                  = "BGP"
  internet_charge_type = "PayByTraffic"
  bandwidth            = "100"
}

resource "alicloud_eip_association" "triagetrace" {
  allocation_id = alicloud_eip_address.triagetrace.id
  instance_id   = alicloud_instance.triagetrace.id
}

output "public_ip" {
  description = "Public EIP of the TriageTrace ECS instance"
  value       = alicloud_eip_address.triagetrace.ip_address
}

output "rds_connection_string" {
  description = "RDS internal endpoint"
  value       = alicloud_db_instance.triagetrace.connection_string
  sensitive   = true
}
