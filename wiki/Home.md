<p align="center">
  <a href="https://github.com/TheNINJALLO/endstone-antigrief/releases/tag/v1.5.13">
    <img src="https://raw.githubusercontent.com/TheNINJALLO/endstone-antigrief/main/assets/banner.svg" alt="AntiGrief for Endstone" width="100%">
  </a>
</p>

<p align="center">
  <a href="https://github.com/TheNINJALLO/endstone-antigrief/releases/tag/v1.5.13"><img alt="Release v1.5.13" src="https://img.shields.io/badge/Release-v1.5.13-8b5cf6?style=for-the-badge&logo=github"></a>
  <a href="https://github.com/TheNINJALLO/endstone-antigrief/actions/workflows/ci.yml"><img alt="Build" src="https://img.shields.io/github/actions/workflow/status/TheNINJALLO/endstone-antigrief/ci.yml?branch=main&style=for-the-badge"></a>
  <img alt="Endstone" src="https://img.shields.io/badge/Endstone-0.11.6-10b981?style=for-the-badge">
  <img alt="BDS" src="https://img.shields.io/badge/BDS-1.26.33-2563eb?style=for-the-badge">
</p>

# AntiGrief Official Documentation Wiki

Welcome to the **AntiGrief for Endstone** official documentation wiki. AntiGrief is an advanced, high-performance moderation, audit logging, precise rollback, item recovery, and evidence generation framework designed for Minecraft Bedrock Edition (BDS) servers powered by Endstone.

> [!IMPORTANT]
> **AntiGrief v1.5.13** requires **Endstone 0.11.6** and **BlockData API v0.4.8+**. Ensure the `blockdata_api` C++ native extension and Python wheel are installed prior to starting AntiGrief.

---

## 📚 Documentation Index

| Topic | Description | Link |
|---|---|---|
| 🚀 **Installation Guide** | Prerequisites, wheel installation, dependency loading, and verification steps. | [Read Guide](Installation) |
| ⚙️ **Configuration Reference** | Complete `config.json` options, WebUI setup, tuning parameters, and retention policies. | [Read Reference](Configuration) |
| 📜 **Command Reference** | Comprehensive list of member and operator commands, permissions, and usage examples. | [Read Commands](Commands) |
| 🌐 **WebUI Security Dashboard** | Web dashboard overview, authentication, live search, NBT inspector, and evidence viewer. | [Read WebUI Guide](Webui) |
| 🔄 **Rollback & Recovery** | 3-Phase atomic block/metadata/inventory rollback engine & `/agback` workflow. | [Read Rollback Guide](Rollback-And-Recovery) |
| 📦 **Inventories & Bundles** | Container NBT snapshotting, Ender Chest inspection, nested bundles, and UTF-8 safety. | [Read Inventories Guide](Inventories-And-Bundles) |
| 📋 **Grief Evidence Reports** | Immutable SHA-256 evidence hashing, printable PDF reports, and incident verification. | [Read Reports Guide](Grief-Reports) |
| 🗄️ **Database & Maintenance** | SQLite WAL mode schema, automated migration logic, and `/agclean` retention tasks. | [Read Database Guide](Database) |
| 🩺 **Troubleshooting Matrix** | Diagnostic steps for BlockData connectivity, malformed NBT, and permission issues. | [Read Troubleshooting](Troubleshooting) |
| 🦖 **Pterodactyl Deployment** | Setup instructions for Pterodactyl Panel containers, environment flags, and egg setup. | [Read Pterodactyl Guide](Pterodactyl) |
| 📦 **Release & Build Process** | Guide on building wheels locally, running pytest test suites, and GitHub release automation. | [Read Release Guide](Release-Process) |

---

## 🏗️ Architecture Overview

AntiGrief operates via a non-blocking asynchronous architecture. High-frequency block, container, and entity events are captured with microsecond precision and stored in SQLite WAL mode. Live player inventories and container NBT are snapshot via the BlockData native C++ bridge.

<p align="center">
  <img src="https://raw.githubusercontent.com/TheNINJALLO/endstone-antigrief/main/assets/architecture.svg" alt="AntiGrief System Architecture" width="90%">
</p>

---

## ✨ Core Highlights

- **Precise 3-Phase Atomic Rollback**: Rolls back placed, broken, exploded, and looted blocks without world corruption.
- **Operator-Confirmed Item Recovery**: Items are only recovered after an operator explicitly runs `/agback`, preventing false accusations.
- **Deep Container & Bundle NBT Inspection**: Full support for double chests, barrels, shulker boxes, chiseled bookshelves, bundles, and nested storage items.
- **FastAPI WebUI Security Operations Center**: Features real-time log search, player Ender Chest inspection, and printable PDF case reports.
- **Cryptographic Evidence Integrity**: Every `/agback` incident generates a tamper-evident SHA-256 hash covering affected coordinates, source events, and restored NBT states.

---

*Need immediate help? Check out the [Troubleshooting Matrix](Troubleshooting) or read the [Installation Guide](Installation).*
