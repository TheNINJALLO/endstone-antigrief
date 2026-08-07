# 📋 Grief Proof Reports & Evidence Integrity

AntiGrief features an automated **Grief Proof Report Generator** that produces immutable, audit-ready evidence files whenever an operator executes `/agback`.

---

## 🔒 SHA-256 Cryptographic Evidence Integrity

Every generated report contains a SHA-256 evidence hash calculated over:

1. The exact `/agback` command parameters (time window, origin coordinates, radius, target player).
2. All source interaction log entries within the selected volume.
3. The pre-rollback and post-rollback block state snapshots.
4. The item recovery queue rows created for affected containers.

```text
SHA-256: 7f8a3b2c...d9e1f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1
```

If an event or database row is tampered with, the SHA-256 signature verification will fail.

---

## 📄 Printable Evidence Reports

Reports can be viewed and printed directly from the WebUI:

- **Timeline Table**: Chronological breakdown of player actions within the target zone.
- **Coordinate Bounds**: Minimum and maximum `(x, y, z)` spatial box affected by the incident.
- **Restoration Summary**: Total blocks placed, broken, restored, and container items recovered.
- **Print & PDF Styling**: Fully styled for browser printing (`Ctrl+P` / `Cmd+P`) and saving as a formal PDF document.
