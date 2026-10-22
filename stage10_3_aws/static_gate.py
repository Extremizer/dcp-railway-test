"""Parse every Terraform HCL file; fail closed for unreviewed resources and paths."""
from pathlib import Path
import ipaddress
import hcl2

root=Path(__file__).parent
files=sorted(root.glob("*.tf"))
def require(ok,why):
    if not ok:raise SystemExit("IAC_SECURITY_FAIL:"+why)
require(set(p.name for p in files)=={"main.tf","variables.tf"},"unexpected_tf_file")
parsed=[hcl2.loads(p.read_text()) for p in files]
resources={}
for doc in parsed:
    for entry in doc.get("resource",[]):
        for typ,names in entry.items():
            for name,config in names.items():
                require((typ,name) not in resources,"duplicate_resource")
                resources[typ,name]=config
expected={("aws_vpc","mock"),("aws_subnet","broker"),("aws_subnet","old"),("aws_subnet","new"),("aws_route_table","isolated"),("aws_route_table_association","broker"),("aws_route_table_association","old"),("aws_route_table_association","new"),("aws_security_group","broker"),("aws_security_group","producers"),("aws_instance","broker"),("aws_instance","producer")}
require(set(resources)==expected,"unexpected_resource")
vpc=resources["aws_vpc","mock"]
require(vpc.get("assign_generated_ipv6_cidr_block") is False,"ipv6_vpc")
require(vpc.get("enable_dns_support") is False and vpc.get("enable_dns_hostnames") is False,"dns")
for name,cidr in (("broker","10.73.10.0/28"),("old","10.73.20.0/28"),("new","10.73.30.0/28")):
    subnet=resources["aws_subnet",name]
    require(subnet.get("cidr_block")==cidr,"subnet_cidr_"+name)
    require(subnet.get("map_public_ip_on_launch") is False and subnet.get("assign_ipv6_address_on_creation") is False,"public_subnet_"+name)
route=resources["aws_route_table","isolated"]
require("route" not in route,"public_route")
for name in ("broker","old","new"):
    require("route_table_id" in resources["aws_route_table_association",name],"association")
broker=resources["aws_security_group","broker"]
producer=resources["aws_security_group","producers"]
def single(block,key):
    value=block.get(key,[])
    require(isinstance(value,list) and len(value)==1,"rule_"+key)
    return value[0]
require(broker.get("egress")==[],"broker_egress")
require(producer.get("ingress")==[],"producer_ingress")
for rule,source in ((single(broker,"ingress"),["10.73.20.0/28","10.73.30.0/28"]),(single(producer,"egress"),["10.73.10.10/32"])):
    require(rule.get("protocol")=="tcp" and rule.get("from_port")==8800 and rule.get("to_port")==8800,"mock_port")
    require(rule.get("cidr_blocks")==source and not rule.get("ipv6_cidr_blocks") and not rule.get("prefix_list_ids") and not rule.get("security_groups"),"unreviewed_sg_destination")
for name in ("broker","producer"):
    inst=resources["aws_instance",name]
    require(inst.get("associate_public_ip_address") is False,"public_instance")
    require("network_interface" not in inst and "ipv6_addresses" not in inst and "secondary_private_ips" not in inst,"extra_nic")
    require("user_data" not in inst and "user_data_base64" not in inst and "provisioner" not in inst,"bootstrap")
    meta=single(inst,"metadata_options")
    require(meta.get("http_endpoint")=="disabled","metadata")
    disk=single(inst,"root_block_device")
    require(disk.get("encrypted") is True,"disk")
    require(inst.get("ami")=="${var.producer_ami}","ami_not_explicit")
require(resources["aws_instance","broker"].get("private_ip")=="10.73.10.10","broker_ip")
alltext="\n".join(p.read_text().lower() for p in files)
for forbidden in ("dealercostparts","railway.app","oemixi.pro","extremizer.ru","secret_access_key","aws_access_key_id","aws_secret_access_key"):
    require(forbidden not in alltext,"production_or_secret_"+forbidden)
variables={}
for doc in parsed:
    for obj in doc.get("variable",[]):
        variables.update(obj)
require(set(variables)=={"region","producer_ami","mock_only_approval"},"unexpected_variable")
require(variables["mock_only_approval"].get("default") is False,"not_default_off")
require("default" not in variables["producer_ami"],"unpinned_ami_default")
print("IAC_SEMANTIC_SECURITY_GATE_PASS",len(resources),"resources")
