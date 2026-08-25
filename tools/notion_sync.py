import os
import json
import requests
from dotenv import load_dotenv
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from tools.database import init_db, get_records_by_state, update_record_state, LifecycleState, get_tactical_audit_sync_targets

load_dotenv()
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
EFFICIENCY_DB_ID = os.getenv("EFFICIENCY_DB_ID", os.getenv("NOTION_DATABASE_ID"))
TACTICAL_DB_ID = os.getenv("TACTICAL_DB_ID", os.getenv("NOTION_DATABASE_ID"))

headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

class NotionAPIError(Exception):
    pass

class RateLimitError(Exception):
    pass

def safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError, Exception):
        return None

def map_efficiency_payload(trade_id: str, asset: str, eff: dict, eff_audit: dict, created_at) -> dict:
    props = {
        "Trade ID": {"title": [{"text": {"content": trade_id}}]},
        "Asset": {"rich_text": [{"text": {"content": asset}}]},
        "Market Bias": {"select": {"name": eff.get("Market_Bias", "N/A")}},
        "Calc Edge": {"number": safe_float(eff.get("Calc_edge"))},
        "Bias A": {"select": {"name": eff_audit.get("bias_a", "N/A")}},
        "Real Bias B": {"select": {"name": eff_audit.get("real_bias_b", "N/A")}},
        "Resolution Type": {"select": {"name": eff_audit.get("resolution_type", "N/A")}},
        "Structural Resolution": {"select": {"name": eff_audit.get("structural_resolution", "N/A")}},
        "Failure Reason": {"select": {"name": eff_audit.get("failure_reason", "N/A")}},
        "Specific Bias Compliance": {"select": {"name": eff_audit.get("specific_bias_compliance", "N/A")}},
        "False Regime Rate": {"select": {"name": eff_audit.get("false_regime_rate", "N/A")}},
        "Edge Validation Price": {"number": safe_float(eff.get("Edge_Validation_Price"))},
        "Structural Invalidation": {"number": safe_float(eff.get("Structural_Invalidation"))},
    }
    if hasattr(created_at, 'strftime'):
        props["Created Date"] = {"date": {"start": created_at.strftime('%Y-%m-%dT%H:%M:%S-06:00')}}

    return {
        "parent": {"database_id": EFFICIENCY_DB_ID},
        "properties": props
    }

def map_tactical_payload(trade_id: str, tact: dict, tact_audit: dict, eff_page_id: str, created_at) -> dict:
    props = {
        "Trade ID": {"title": [{"text": {"content": trade_id}}]},
        "Tactical Classification": {"select": {"name": tact.get("tactical_classification", "N/A")}},
        "Calc Edge": {"number": safe_float(tact.get("calc_edge", 0.0))},
        "Order Filled": {"checkbox": bool(tact_audit.get("order_filled", True))},
        "Efficiency_Relation": {"relation": [{"id": eff_page_id}]}
    }
    if hasattr(created_at, 'strftime'):
        props["Created Date"] = {"date": {"start": created_at.strftime('%Y-%m-%dT%H:%M:%S-06:00')}}
    if tact_audit.get("entry_time") and hasattr(tact_audit["entry_time"], 'strftime'):
        props["Entry Time"] = {"date": {"start": tact_audit["entry_time"].strftime('%Y-%m-%dT%H:%M:%S-06:00')}}
    if tact_audit.get("exit_time") and hasattr(tact_audit["exit_time"], 'strftime'):
        props["Exit Time"] = {"date": {"start": tact_audit["exit_time"].strftime('%Y-%m-%dT%H:%M:%S-06:00')}}

    return {
        "parent": {"database_id": TACTICAL_DB_ID},
        "properties": props
    }

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5), retry=retry_if_exception_type(RateLimitError))
def post_to_notion(payload: dict) -> dict:
    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code in [429, 500, 502, 503, 504]:
        raise RateLimitError(f"Rate limit or server error: {resp.status_code}")
    if resp.status_code >= 400:
        raise NotionAPIError(f"API Error {resp.status_code}: {resp.text}")
    return resp.json()

def _log_sync_error(error_msg: str):
    os.makedirs(".tmp", exist_ok=True)
    print(error_msg)
    with open(".tmp/sync_errors.log", "a") as f:
        f.write(error_msg + "\n")


def sync_records():
    """Two passes: (1) analyses still in READY_FOR_NOTION/FAILED get their
    Efficiency page created (once) and every not-yet-synced tactical_audit row
    gets its own Tactical page, linked to that Efficiency page; the parent only
    flips to SYNCED once every row it currently has is synced. (2) analyses
    already SYNCED can still gain new tactical_audit rows later (e.g. scaling
    into the same setup, or retrying it another day) -- those get their own
    Tactical page independently, without touching the already-synced parent."""
    from sqlalchemy.orm import Session
    from tools.database import engine_default, UnifiedDepartment

    engine = engine_default or init_db()

    # --- Pass 1: analyses still pending their first full sync ---
    records = get_records_by_state([LifecycleState.READY_FOR_NOTION, LifecycleState.FAILED])
    if not records:
        print("No records pending first sync.")
    else:
        with Session(engine) as session:
            for r in records:
                try:
                    db_record = session.get(UnifiedDepartment, r["id"])
                    if not db_record or db_record.state == LifecycleState.SYNCED.value:
                        continue

                    payload = r["payload"]
                    eff = payload.get("efficiency", {})
                    eff_audit = payload.get("audit_efficiency", {})
                    tact = payload.get("tactical", {})
                    asset = payload.get("asset", "Unknown")

                    eff_page_id = db_record.efficiency_page_id
                    if not eff_page_id:
                        eff_notion_payload = map_efficiency_payload(r["id"], asset, eff, eff_audit, r["created_at"])
                        eff_resp = post_to_notion(eff_notion_payload)
                        eff_page_id = eff_resp["id"]
                        db_record.efficiency_page_id = eff_page_id
                        session.commit()
                        print(f"Created Efficiency page in Notion: {eff_page_id}")
                    else:
                        print(f"Skipping Efficiency page creation, reusing ID: {eff_page_id}")

                    all_synced = True
                    for ta in db_record.tactical_audits:
                        if ta.notion_page_id:
                            continue
                        try:
                            tact_audit = {
                                "order_filled": ta.order_filled,
                                "entry_time": ta.entry_time,
                                "exit_time": ta.exit_time,
                            }
                            tact_notion_payload = map_tactical_payload(r["id"], tact, tact_audit, eff_page_id, ta.created_at)
                            tact_resp = post_to_notion(tact_notion_payload)
                            ta.notion_page_id = tact_resp["id"]
                            session.commit()
                            print(f"Created Tactical page in Notion for execution {ta.id}: {ta.notion_page_id}")
                        except Exception as e:
                            session.rollback()
                            all_synced = False
                            _log_sync_error(f"Failed to sync tactical_audit {ta.id} of trade {r['id']}: {e}")

                    if not db_record.tactical_audits:
                        # Shouldn't happen -- the READY_FOR_NOTION promotion rule
                        # requires at least one tactical_audit row -- but don't
                        # flip state on a data anomaly with no actual sync error.
                        _log_sync_error(f"Trade {r['id']} is READY_FOR_NOTION with no tactical_audit rows; leaving state untouched.")
                    elif all_synced:
                        db_record.state = LifecycleState.SYNCED.value
                        session.commit()
                        print(f"Successfully synced trade {r['id']}")
                    else:
                        db_record.state = LifecycleState.FAILED.value
                        session.commit()

                except Exception as e:
                    session.rollback()
                    _log_sync_error(f"Failed to sync trade {r['id']}: {e}")
                    try:
                        with Session(engine) as err_session:
                            err_record = err_session.get(UnifiedDepartment, r["id"])
                            if err_record:
                                err_record.state = LifecycleState.FAILED.value
                                err_session.commit()
                    except Exception as db_err:
                        print(f"Failed to set state to FAILED: {db_err}")

    # --- Pass 2: new executions added to analyses that are already SYNCED ---
    late_targets = [t for t in get_tactical_audit_sync_targets(engine=engine) if t["eff_page_id"]]
    if not late_targets:
        print("No late tactical executions to sync.")
        return

    with Session(engine) as session:
        from tools.database import TacticalAudit
        for t in late_targets:
            try:
                ta = session.get(TacticalAudit, t["tactical_audit_id"])
                if not ta or ta.notion_page_id:
                    continue
                tact_notion_payload = map_tactical_payload(t["trade_id"], t["tactical"], t["audit_tactical"], t["eff_page_id"], t["created_at"])
                tact_resp = post_to_notion(tact_notion_payload)
                ta.notion_page_id = tact_resp["id"]
                session.commit()
                print(f"Created Tactical page in Notion for late execution {ta.id}: {ta.notion_page_id}")
            except Exception as e:
                session.rollback()
                _log_sync_error(f"Failed to sync late tactical_audit {t['tactical_audit_id']} of trade {t['trade_id']}: {e}")

if __name__ == "__main__":
    init_db()
    sync_records()
