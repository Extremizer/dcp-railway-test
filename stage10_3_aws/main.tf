terraform {
  required_version = ">= 1.8.0, < 2.0.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}
provider "aws" { region = var.region }
locals { prefix = "dcp-s10-3-mock" }
resource "aws_vpc" "mock" {
  cidr_block = "10.73.0.0/16"
  assign_generated_ipv6_cidr_block = false
  enable_dns_support = false
  enable_dns_hostnames = false
  tags = { Name = "${local.prefix}-vpc", Environment = "isolated-mock", Production = "false" }
}
resource "aws_subnet" "broker" {
  vpc_id = aws_vpc.mock.id
  cidr_block = "10.73.10.0/28"
  availability_zone = "${var.region}a"
  map_public_ip_on_launch = false
  assign_ipv6_address_on_creation = false
}
resource "aws_subnet" "old" {
  vpc_id = aws_vpc.mock.id
  cidr_block = "10.73.20.0/28"
  availability_zone = "${var.region}a"
  map_public_ip_on_launch = false
  assign_ipv6_address_on_creation = false
}
resource "aws_subnet" "new" {
  vpc_id = aws_vpc.mock.id
  cidr_block = "10.73.30.0/28"
  availability_zone = "${var.region}b"
  map_public_ip_on_launch = false
  assign_ipv6_address_on_creation = false
}
resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.mock.id
  # Only the automatically installed VPC-local route. No IGW/NAT/TGW/peering.
}
resource "aws_route_table_association" "broker" {
  subnet_id = aws_subnet.broker.id
  route_table_id = aws_route_table.isolated.id
}
resource "aws_route_table_association" "old" {
  subnet_id = aws_subnet.old.id
  route_table_id = aws_route_table.isolated.id
}
resource "aws_route_table_association" "new" {
  subnet_id = aws_subnet.new.id
  route_table_id = aws_route_table.isolated.id
}
resource "aws_security_group" "broker" {
  name = "${local.prefix}-broker"
  vpc_id = aws_vpc.mock.id
  ingress = []
  egress = []
}
resource "aws_security_group" "producers" {
  name = "${local.prefix}-producer"
  vpc_id = aws_vpc.mock.id
  ingress = []
  egress = []
}
resource "aws_security_group_rule" "broker_ingress" {
  type = "ingress"
  security_group_id = aws_security_group.broker.id
  source_security_group_id = aws_security_group.producers.id
  from_port = 8800
  to_port = 8800
  protocol = "tcp"
}
resource "aws_security_group_rule" "producer_egress" {
  type = "egress"
  security_group_id = aws_security_group.producers.id
  source_security_group_id = aws_security_group.broker.id
  from_port = 8800
  to_port = 8800
  protocol = "tcp"
}
resource "aws_instance" "broker" {
  count = var.mock_only_approval ? 1 : 0
  ami = var.producer_ami
  instance_type = "t3.micro"
  subnet_id = aws_subnet.broker.id
  associate_public_ip_address = false
  vpc_security_group_ids = [aws_security_group.broker.id]
  monitoring = true
  metadata_options { http_endpoint = "disabled" }
  root_block_device { volume_type = "gp3" volume_size = 16 encrypted = true }
  tags = { Name = "${local.prefix}-broker", Stage = "MOCK_ONLY" }
}
resource "aws_instance" "producer" {
  for_each = var.mock_only_approval ? toset(["old", "new"]) : toset([])
  ami = var.producer_ami
  instance_type = "t3.micro"
  subnet_id = each.key == "old" ? aws_subnet.old.id : aws_subnet.new.id
  associate_public_ip_address = false
  vpc_security_group_ids = [aws_security_group.producers.id]
  monitoring = true
  metadata_options { http_endpoint = "disabled" }
  root_block_device { volume_type = "gp3" volume_size = 16 encrypted = true }
  tags = { Name = "${local.prefix}-${each.key}", Stage = "MOCK_ONLY", Host = each.key }
}
