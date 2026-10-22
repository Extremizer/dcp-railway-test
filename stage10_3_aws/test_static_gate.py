from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
root=Path(__file__).parent
class GateTests(unittest.TestCase):
    def check(self,transform=None):
        with tempfile.TemporaryDirectory() as td:
            for name in ("main.tf","variables.tf","static_gate.py"):
                content=(root/name).read_text()
                if name=="main.tf" and transform:content=transform(content)
                (Path(td)/name).write_text(content)
            return subprocess.run([sys.executable,str(Path(td)/"static_gate.py")],capture_output=True,text=True)
    def test_approved_static(self):
        r=self.check();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_world_route_rejected(self):
        self.assertNotEqual(self.check(lambda x:x+'\n# 0.0.0.0/0\n').returncode,0)
    def test_public_ip_rejected(self):
        self.assertNotEqual(self.check(lambda x:x.replace("associate_public_ip_address = false","associate_public_ip_address = true",1)).returncode,0)
    def test_egress_rejected(self):
        self.assertNotEqual(self.check(lambda x:x.replace("egress = []","egress = [{}]",1)).returncode,0)
    def test_new_nic_rejected(self):
        self.assertNotEqual(self.check(lambda x:x+'\nresource "aws_network_interface" "unreviewed" {}\n').returncode,0)
    def test_prod_dependency_rejected(self):
        self.assertNotEqual(self.check(lambda x:x+'\n# railway.app\n').returncode,0)
