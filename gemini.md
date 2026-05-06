# Project Constitution (gemini.md)

## 1. Project Overview
Headless CLI Analytics Engine for manual trading. The system processes Structural and Tactical analysis, calculates probabilistic Edge, dictates deterministic risk tiers (5% or 10%), and manages asynchronous SQLite-to-Notion synchronization. Single-user execution.

## 2. Data Schemas (Input/Output Shapes)
**SQLite Journal Entry (Primary State Machine - `journal.db`)**
```json
{
  "trade_id": "string (UUID)",
  "timestamp": "string (ISO 8601)",
  "asset": "string (e.g., BTC/USDT)",
  "structural_analysis": "string",
  "tactical_analysis": "string",
  "edge_score": "float",
  "risk_tier": "string ('5%' or '10%')",
  "status": "string ('PENDING', 'EXECUTED', 'CANCELLED')",
  "sync_status": "string ('UNSYNCED', 'SYNCED')"
}
```

**Notion Payload (Output)**
```json
{
  "parent": { "database_id": "string" },
  "properties": {
    "Trade ID": { "title": [{ "text": { "content": "UUID" } }] },
    "Asset": { "rich_text": [{ "text": { "content": "BTC/USDT" } }] },
    "Structural Analysis": { "rich_text": [{ "text": { "content": "..." } }] },
    "Tactical Analysis": { "rich_text": [{ "text": { "content": "..." } }] },
    "Edge Score": { "number": 0.0 },
    "Risk Tier": { "select": { "name": "5%" } },
    "Status": { "status": { "name": "PENDING" } },
    "Date": { "date": { "start": "ISO 8601" } }
  }
}
```

## 3. Validation Logic
- **Completeness:** `asset`, `structural_analysis`, and `tactical_analysis` must not be empty. If empty, the CLI rejects input and re-prompts.
- **Risk Enforcements:** `risk_tier` must evaluate strictly to "5%" or "10%". Any deviation raises a terminal error and prevents SQLite persistence.
- **Sync Resiliency:** If Notion API is unreachable (timeout, 4xx, 5xx), the `sync_status` remains `UNSYNCED`. The local database serves as the unbreakable source of truth.

## 4. Behavioral Rules & Tone
- **Tone:** Instructive and clinical.
- **Role:** The CLI acts as an Information Gate, enforcing strict analytical compliance before the operator executes trades externally.

## 5. Architectural Invariants
- **Layer 1:** `architecture/` - SOPs in Markdown. Update before changing code.
- **Layer 2:** Navigation - Decision making and routing.
- **Layer 3:** `tools/` - Deterministic, testable scripts.
- **Security:** Zero-trust storage for Notion API keys. No broker APIs integrated.

## 6. Rollback Strategy & Maintenance Log
- **Rollback:** In the event of a Notion sync failure, simply re-run the sync command. The local SQLite database acts as a permanent ledger, allowing for manual correction. Reversal is as simple as deleting an unsynced row from `journal.db`.
