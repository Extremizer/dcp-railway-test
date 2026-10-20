import unittest
from dp_cloud_producer_client import CloudV2Producer, ProducerContractError

class CloudClientContract(unittest.TestCase):
    def test_requires_ownership_before_claim(self):
        p=CloudV2Producer("cloud-host-000001",b"x"*32,lambda *args: {})
        with self.assertRaises(ProducerContractError):
            p.next()
        with self.assertRaises(ProducerContractError):
            p.renew()

if __name__=="__main__":
    unittest.main()
