"""Review-only fail-closed structural IaC scanner. No AWS credentials, no apply."""
from pathlib import Path
import re
p=Path(__file__).parent
main=(p/"main.tf").read_text()
variables=(p/"variables.tf").read_text()
def must(ok,reason):
    if not ok:raise SystemExit("IAC_SECURITY_FAIL:"+reason)
resources=re.findall(r'resource\s+"([^"]+)"\s+"([^"]+)"',main)
allowed={"aws_vpc","aws_subnet","aws_route_table","aws_route_table_association","aws_security_group","aws_security_group_rule","aws_instance"}
must(set(t for t,n in resources)<=allowed,"unapproved_resource")
must(len([1 for t,n in resources if t=="aws_instance"])==2,"instance_types")
must(len([1 for t,n in resources if t=="aws_subnet"])==3,"three_subnets")
must(len([1 for t,n in resources if t=="aws_security_group_rule"])==2,"sg_rule_count")
for token in ["aws_internet_gateway","aws_nat_gateway","aws_route\"","aws_network_interface","aws_vpc_peering","aws_ec2_transit_gateway","aws_eip","aws_lb","aws_iam_access_key"]:
    must(token not in main,"forbidden_"+token)
must('assign_generated_ipv6_cidr_block = false' in main,"ipv6")
must(main.count("map_public_ip_on_launch = false")==3,"public_subnet")
must(main.count("associate_public_ip_address = false")==2,"public_instance")
must(main.count("metadata_options { http_endpoint = \"disabled\" }")==2,"metadata")
must(main.count("encrypted = true")==2,"unencrypted_disk")
must(main.count("egress = []")==2 and main.count("ingress = []")==2,"default_deny")
must(main.count('source_security_group_id = aws_security_group.broker.id')==1,"egress_destination")
must(main.count('source_security_group_id = aws_security_group.producers.id')==1,"ingress_source")
must(main.count('from_port = 8800')==2 and main.count('to_port = 8800')==2,"ports")
must("0.0.0.0/0" not in main and "::/0" not in main,"world_route")
must('mock_only_approval" { type = bool default = false }' in variables,"default_disabled")
must('can(regex("^sha256:[a-f0-9]{64}$",var.mock_image_digest))' in variables,"digest_pin")
must("user_data" not in main and "remote-exec" not in main,"runtime_bootstrap")
must(not any(x in main.lower() for x in ("dealercostparts","railway.app","oemixi.pro","extremizer.ru")),"production_dependency")
print("IAC_STATIC_SECURITY_GATE_PASS",len(resources),"allowed resources")
