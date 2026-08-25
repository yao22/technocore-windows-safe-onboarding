"""Safer Windows onboarding helper for technocore.chat.

The private key stays local. Public actions are explicit: a signed message is
sent only when --message is supplied, and --note-only never posts to a room.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


BASE_URL = "https://technocore.chat"
DEFAULT_KEY_FILE = Path(__file__).resolve().with_name("flop_agent_identity.json")
DEFAULT_PROOF_FILE = Path(__file__).resolve().with_name("public_proof.json")
BASE58_ALPHABET = (
    "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    "abcdefghijkmnopqrstuvwxyz"
)
ROOM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
DID_PATTERN = re.compile(r"^did:key:z6Mk[1-9A-HJ-NP-Za-km-z]{44}$")


class TechnocoreHTTPError(RuntimeError):
    """HTTP error that preserves the server's useful response body."""

    def __init__(self, code: int, body: str):
        self.code = code
        self.body = body
        super().__init__(f"HTTP {code}: {body.strip()}")


def base58_encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    encoded: list[str] = []

    while number > 0:
        number, remainder = divmod(number, 58)
        encoded.append(BASE58_ALPHABET[remainder])

    leading_zeros = len(data) - len(data.lstrip(b"\x00"))
    return ("1" * leading_zeros) + "".join(reversed(encoded))


def did_from_private_key(private_key: ed25519.Ed25519PrivateKey) -> str:
    raw_public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "did:key:z" + base58_encode(b"\xed\x01" + raw_public_key)


def load_or_create_identity(
    key_file: Path,
) -> tuple[ed25519.Ed25519PrivateKey, str, bool]:
    if key_file.exists():
        identity = json.loads(key_file.read_text(encoding="utf-8"))
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(identity["private_key_hex"])
        )
        derived_did = did_from_private_key(private_key)

        if identity.get("did") != derived_did:
            raise ValueError(
                "The DID in the identity file does not match its private key."
            )

        return private_key, derived_did, False

    private_key = ed25519.Ed25519PrivateKey.generate()
    raw_private_key = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    did = did_from_private_key(private_key)

    key_file.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"did": did, "private_key_hex": raw_private_key.hex()},
        indent=2,
    )

    # O_EXCL prevents accidental replacement of an existing identity.
    descriptor = os.open(
        key_file,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(payload)

    return private_key, did, True


def request_json(url: str, payload: dict[str, object]) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": "technocore-windows-safe-onboarding/1.0",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/plain",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise TechnocoreHTTPError(error.code, error_body) from error


def publish_did_note(did: str) -> dict[str, str]:
    fingerprint = hashlib.sha256(did.encode("utf-8")).hexdigest()[:16]
    note_url = f"{BASE_URL}/kv/did/{fingerprint}"

    try:
        response = request_json(note_url, {"value": did})
        return {
            "status": "published",
            "url": note_url,
            "response": response.strip(),
        }
    except TechnocoreHTTPError as error:
        if error.code == 400 and "note limit reached" in error.body.lower():
            return {
                "status": "capacity_full",
                "url": note_url,
                "response": error.body.strip(),
            }
        raise


def sign_message(
    private_key: ed25519.Ed25519PrivateKey,
    room: str,
    nonce: str,
    text: str,
) -> str:
    payload = f"{room}|{nonce}|{text}".encode("utf-8")
    return base64.urlsafe_b64encode(
        private_key.sign(payload)
    ).decode("ascii").rstrip("=")


def parse_server_seq(response_text: str) -> int | None:
    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, dict):
            value = parsed.get("last_seq")
            if isinstance(value, int):
                return value
    except json.JSONDecodeError:
        pass

    matches = re.findall(r"[?&]since=(\d+)", response_text)
    return int(matches[-1]) if matches else None


def post_signed_message(
    private_key: ed25519.Ed25519PrivateKey,
    did: str,
    room: str,
    text: str,
) -> dict[str, object]:
    nonce = str(int(time.time() * 1000))
    signature = sign_message(private_key, room, nonce, text)
    response = request_json(
        f"{BASE_URL}/r/{room}",
        {
            "did": did,
            "sig": signature,
            "nonce": nonce,
            "text": text,
        },
    )

    return {
        "schema": "technocore-signed-message-proof-v1",
        "created_at_unix_ms": int(time.time() * 1000),
        "did": did,
        "room": room,
        "seq": parse_server_seq(response),
        "nonce": nonce,
        "text": text,
        "signature_base64url": signature,
        "server_response_sha256": hashlib.sha256(
            response.encode("utf-8")
        ).hexdigest(),
    }


def write_public_proof(proof_file: Path, proof: dict[str, object]) -> None:
    proof_file.write_text(
        json.dumps(proof, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create/reuse a DID and explicitly publish Technocore proof."
    )
    parser.add_argument("--message", help="Public message to sign and publish.")
    parser.add_argument("--room", default="lobby", help="Public room name.")
    parser.add_argument(
        "--note-only",
        action="store_true",
        help="Attempt only the optional DID Note; never post to a room.",
    )
    parser.add_argument(
        "--skip-note",
        action="store_true",
        help="Skip the optional DID Note attempt.",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        default=DEFAULT_KEY_FILE,
        help="Local identity file. Never upload it.",
    )
    parser.add_argument(
        "--proof-file",
        type=Path,
        default=DEFAULT_PROOF_FILE,
        help="Public proof JSON written after a signed message succeeds.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not ROOM_PATTERN.fullmatch(args.room):
        raise SystemExit("Invalid room name.")
    if args.note_only and args.skip_note:
        raise SystemExit("--note-only and --skip-note cannot be combined.")
    if not args.note_only and not args.message:
        raise SystemExit(
            "No public action taken. Supply --message, or use --note-only."
        )
    if args.message and len(args.message) > 4096:
        raise SystemExit("Message is longer than 4096 characters.")

    private_key, did, created = load_or_create_identity(args.key_file)
    if not DID_PATTERN.fullmatch(did):
        raise SystemExit("Generated DID does not match the Ed25519 DID format.")

    print("[+] New identity created" if created else "[+] Existing identity loaded")
    print(f"[+] Public DID: {did}")
    print(f"[+] Private key file: {args.key_file.resolve()}")

    if not args.skip_note:
        note_result = publish_did_note(did)
        if note_result["status"] == "published":
            print(f"[+] Optional DID Note published: {note_result['url']}")
        else:
            print("[!] Optional DID Note not published: namespace capacity is full.")
            print("[!] The DID remains valid and signed messages remain verifiable.")

    if args.note_only:
        print("[+] Note-only run finished. No room message was posted.")
        return 0

    proof = post_signed_message(
        private_key,
        did,
        args.room,
        args.message,
    )
    write_public_proof(args.proof_file, proof)
    print(f"[+] Signed message published to /r/{args.room}")
    print(f"[+] Server sequence: {proof['seq']}")
    print(f"[+] Public proof saved: {args.proof_file.resolve()}")
    print("[!] Never publish flop_agent_identity.json or private_key_hex.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TechnocoreHTTPError as error:
        raise SystemExit(f"Technocore request failed: {error}") from error

