from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

root=Path(__file__).parent

class GateTests(unittest.TestCase):
    def check(self,transform=None,addition=None):
        with tempfile.TemporaryDirectory() as td:
            for name in ("main.tf","variables.tf","static_gate.py"):
                content=(root/name).read_text()
                if name=="main.tf" and transform:content=transform(content)
                (Path(td)/name).write_text(content)
            if addition:
                (Path(td)/"extra.tf").write_text(addition)
            return subprocess.run([sys.executable,str(Path(td)/"static_gate.py")],capture_output=True,text=True)
    def reject(self,change):
        self.assertNotEqual(self.check(change).returncode,0)
    def test_approved_static(self):
        r=self.check();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_public_route_rejected(self):
        self.reject(lambda x:x+'\nresource "aws_route" "public" { route_table_id = "x" destination_cidr_block = "0.0.0.0/0" gateway_id = "igw-x" }\n')
    def test_public_ip_rejected(self):
        self.reject(lambda x:x.replace("associate_public_ip_address = false","associate_public_ip_address = true",1))
    def test_extra_egress_rejected(self):
        self.reject(lambda x:x.replace("egress = []","egress { from_port = 0 to_port = 0 protocol = \"-1\" cidr_blocks = [\"0.0.0.0/0\"] }",1))
    def test_second_egress_rule_rejected(self):
        self.reject(lambda x:x.replace('cidr_blocks = ["10.73.10.10/32"]','cidr_blocks = ["10.73.10.10/32", "0.0.0.0/0"]',1))
    def test_new_nic_rejected(self):
        self.reject(lambda x:x+'\nresource "aws_network_interface" "unreviewed" {}\n')
    def test_ipv6_rejected(self):
        self.reject(lambda x:x.replace("assign_generated_ipv6_cidr_block = false","assign_generated_ipv6_cidr_block = true"))
    def test_metadata_exception_rejected(self):
        self.reject(lambda x:x.replace('http_endpoint = "disabled"','http_endpoint = "enabled"',1))
    def test_prod_dependency_rejected(self):
        self.reject(lambda x:x+'\nresource "aws_ssm_parameter" "prod" { name = "railway.app" type = "String" value = "secret" }\n')
    def test_unexpected_file_rejected(self):
        self.assertNotEqual(self.check(addition='resource "aws_security_group_rule" "extra" {}').returncode,0)
