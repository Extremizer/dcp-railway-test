import unittest
from dp_cloud_producer_client import CloudV2Producer, ProducerContractError

class CloudClientContract(unittest.TestCase):
    def test_requires_ownership_before_claim(self):
        p=CloudV2Producer("cloud-host-000001",b"x"*32,lambda *args: {})
        with self.assertRaises(ProducerContractError):
            p.next()
        with self.assertRaises(ProducerContractError):
            p.renew()


    def test_signed_owner_lifecycle(self):
        calls=[]
        def send(method,path,body,headers):
            calls.append((method,path,body,headers))
            if path.endswith("/acquire"):
                return {"status":"ACQUIRED","owner_epoch":"epoch"}
            if path.endswith("/renew"):
                return {"status":"RENEWED"}
            if path.endswith("/release"):
                return {"status":"RELEASED"}
            return {"request":None}
        p=CloudV2Producer("cloud-host-000001",b"x"*32,send)
        p.acquire()
        p.renew()
        self.assertIsNone(p.next())
        p.release()
        self.assertIsNone(p.owner_epoch)
        self.assertEqual(len(calls),4)

if __name__=="__main__":
    unittest.main()
