import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent


class AgentTests(unittest.TestCase):
    def test_identity_is_valid_and_reused(self):
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        ) as directory:
            key_file = Path(directory) / "identity.json"
            first_key, first_did, created = agent.load_or_create_identity(key_file)
            second_key, second_did, created_again = agent.load_or_create_identity(
                key_file
            )

            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertRegex(first_did, agent.DID_PATTERN)
            self.assertEqual(first_did, second_did)
            self.assertEqual(
                first_key.private_bytes_raw(),
                second_key.private_bytes_raw(),
            )

    def test_signature_round_trip(self):
        private_key = agent.ed25519.Ed25519PrivateKey.generate()
        room = "lobby"
        nonce = "123456789"
        text = "Hello Technocore"
        signature = agent.sign_message(private_key, room, nonce, text)
        padded = signature + ("=" * (-len(signature) % 4))

        private_key.public_key().verify(
            base64.urlsafe_b64decode(padded),
            f"{room}|{nonce}|{text}".encode("utf-8"),
        )

    def test_parse_server_seq_from_json(self):
        self.assertEqual(agent.parse_server_seq(json.dumps({"last_seq": 51831})), 51831)

    def test_parse_server_seq_from_text(self):
        response = "next: /r/lobby?since=51831\n"
        self.assertEqual(agent.parse_server_seq(response), 51831)


if __name__ == "__main__":
    unittest.main()
