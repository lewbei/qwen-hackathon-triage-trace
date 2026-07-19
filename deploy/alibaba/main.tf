terraform {
  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.220"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
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

# Security group exposing public HTTP only and SSH from a chosen CIDR.
resource "alicloud_security_group" "triagetrace" {
  security_group_name = "triagetrace-sg"
  vpc_id              = alicloud_vpc.triagetrace.id
}

resource "alicloud_security_group_rule" "ssh" {
  count             = var.ssh_cidr != "" ? 1 : 0
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "internet"
  policy            = "accept"
  port_range        = "22/22"
  priority          = 1
  security_group_id = alicloud_security_group.triagetrace.id
  cidr_ip           = var.ssh_cidr
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

# Latest official Ubuntu 22.04 image, with a variable override if needed.
data "alicloud_images" "ubuntu" {
  owners      = "system"
  name_regex  = "^ubuntu_22_04"
  most_recent = true
}

# Internal PostgreSQL password for the ECS-hosted pgvector container.
resource "random_password" "db" {
  length  = 24
  special = false
}

# ECS instance running backend + frontend + pgvector
resource "alicloud_instance" "triagetrace" {
  image_id                   = var.image_id != "" ? var.image_id : data.alicloud_images.ubuntu.images[0].id
  instance_type              = var.instance_type
  security_groups            = [alicloud_security_group.triagetrace.id]
  vswitch_id                 = alicloud_vswitch.triagetrace.id
  instance_name              = "triagetrace-api"
  system_disk_category       = "cloud_essd"
  system_disk_size           = 40
  internet_charge_type       = "PayByTraffic"
  internet_max_bandwidth_out = 100
  key_name                   = var.key_name

  user_data = base64encode(templatefile("${path.module}/cloud-init.sh", {
    db_host      = "db"
    db_name      = "triagetrace"
    db_user      = var.db_user
    db_password  = random_password.db.result
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
