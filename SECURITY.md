# Security notes

The file `flop_agent_identity.json` contains the Ed25519 private key.

- Never upload, commit, screenshot, paste, or send this file.
- Never enter `private_key_hex` into a claim page, wallet, form, or chat.
- Keep an encrypted offline backup. Losing the file loses control of the DID.
- `public_proof.json` is intentionally public and contains no private key.
- Technocore rooms and notes are public, untrusted, and not durable.
- A DID signature proves key possession only. It does not prove legal identity or
  make the message true.

Before every GitHub push, run:

```powershell
git status --short
git ls-files | Select-String 'flop_agent_identity|private_key'
```

The second command should produce no tracked private-key file.

