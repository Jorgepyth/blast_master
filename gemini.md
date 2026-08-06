# Project Constitution (gemini.md)

> Actualizado 2026-08-06 tras research de arquitectura verificado por comando — ver ARCHITECTURE.md para evidencia completa de cada claim.

## 1. Project Overview
Headless CLI Analytics Engine for manual trading. The system processes Structural and Tactical analysis, calculates probabilistic Edge, and manages asynchronous SQLite-to-Notion synchronization. Single-user execution.

## 2. Source of Truth (Data Schema)
Real schema is multi-DB per account/asset, not a single flat journal: each account has its own SQLite file (`flight_account_{cuenta}_{activo}.db`) holding 7 normalized tables (`unified_department`, `analysis_layer`, `efficiency_audit`, `tactical_audit`, `asset_config`, `asset_balance`, `emotion_catalog`). Record lifecycle and Notion sync state are tracked via `LifecycleState` on `unified_department.state`, not a separate `sync_status` field.

Full ER diagram, state model, and Notion payload shape (two separate databases — Efficiency and Tactical — with their own property sets): **see [ARCHITECTURE.md §5](ARCHITECTURE.md)**. Field-level detail also lives in [CLAUDE.md](CLAUDE.md). Do not duplicate that detail here — this file is the constitution of invariants and process rules, not a second copy of the technical docs.

## 3. Validation Logic
- **Completeness:** `asset`, `structural_analysis`, and `tactical_analysis` must not be empty. If empty, the CLI rejects input and re-prompts.
- **Sync Resiliency:** If Notion API is unreachable (timeout, 4xx, 5xx), the record's `state` stays out of `SYNCED` (typically `FAILED`, retried on the next sync pass). The local per-account database serves as the unbreakable source of truth.

## 4. Behavioral Rules & Tone
- **Tone:** Instructive and clinical.
- **Role:** The CLI acts as an Information Gate, enforcing strict analytical compliance before the operator executes trades externally.

## 5. Architectural Invariants
- **Layer 1:** Architecture docs — `ARCHITECTURE.md` and `CLAUDE.md` at repo root (Markdown SOPs). Update before changing code.
- **Layer 2:** Navigation - Decision making and routing.
- **Layer 3:** `tools/` - Deterministic, testable scripts.
- **Security:** Zero-trust storage for Notion API keys. No broker APIs integrated.

## 6. Rollback Strategy & Maintenance Log
- **Rollback:** In the event of a Notion sync failure, simply re-run the sync command. Each account's local SQLite database acts as a permanent ledger, allowing for manual correction. Reversal is as simple as resetting the affected record's lifecycle state.
- **Notion sync priority:** Sync remains deprioritized. The finding that `tools/notion_handshake.py` is a standalone diagnostic script with zero integration into the CLI flow (ARCHITECTURE.md §9(j)) does not change that decision.
