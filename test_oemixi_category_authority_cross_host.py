import unittest

import oemixi_category_authority_client as client
import oemixi_category_authority_transport as server


class CrossHostContractTests(unittest.TestCase):
    def test_protocol_contract_matches(self):
        self.assertEqual(client.PATH, server.PATH)
        self.assertEqual(client.PROTOCOL_VERSION, server.PROTOCOL_VERSION)

    def test_canonical_body_matches(self):
        self.assertEqual(
            client.canonical_body("Polaris", "2205250"),
            server.canonical_body("Polaris", "2205250"),
        )

    def test_signature_matches(self):
        secret = "0123456789abcdef0123456789abcdef"
        timestamp = "1760640000"
        body = client.canonical_body("Polaris", "2205250")
        self.assertEqual(
            client.sign_request(secret, timestamp=timestamp, body=body),
            server.sign_request(secret, timestamp=timestamp, body=body),
        )

    def test_identity_normalization_matches(self):
        self.assertEqual(
            client.normalize_identity("Polaris", "2205250"),
            server.normalize_identity("Polaris", "2205250"),
        )


if __name__ == "__main__":
    unittest.main()
