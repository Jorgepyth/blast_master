import os
import json
import requests
from dotenv import load_dotenv
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from tools.database import init_db, get_records_by_state, update_record_state, LifecycleState

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

def map_efficiency_payload(trade_id: str, asset: str, eff: dict, eff_audit: dict) -> dict:
    return {
        "parent": {"database_id": EFFICIENCY_DB_ID},
        "properties": {
            "Trade ID": {"title": [{"text": {"content": trade_id}}]},
            "Asset": {"rich_text": [{"text": {"content": asset}}]},
            "Market Bias": {"select": {"name": eff.get("Market_Bias", "N/A")}},
            "Calc Edge": {"number": eff.get("Calc_edge", 0.0)},
            "Bias A": {"select": {"name": eff_audit.get("bias_a", "N/A")}},
            "Real Bias B": {"select": {"name": eff_audit.get("real_bias_b", "N/A")}},
            "Resolution Type": {"select": {"name": eff_audit.get("resolution_type", "N/A")}},
            "Structural Resolution": {"select": {"name": eff_audit.get("structural_resolution", "N/A")}},
            "Failure Reason": {"select": {"name": eff_audit.get("failure_reason", "N/A")}},
            "Specific Bias Compliance": {"select": {"name": eff_audit.get("specific_bias_compliance", "N/A")}},
            "False Regime Rate": {"select": {"name": eff_audit.get("false_regime_rate", "N/A")}},
        }
    }

def map_tactical_payload(trade_id: str, tact: dict, tact_audit: dict, eff_page_id: str) -> dict:
    return {
        "parent": {"database_id": TACTICAL_DB_ID},
        "properties": {
            "Trade ID": {"title": [{"text": {"content": trade_id}}]},
            "Tactical Classification": {"select": {"name": tact.get("tactical_classification", "N/A")}},
            "Calc Edge": {"number": tact.get("calc_edge", 0.0)},
            "Trade Status": {"select": {"name": tact_audit.get("trade_status", "N/A")}},
            "Compliance State": {"select": {"name": tact_audit.get("compliance", "N/A")}},
            "Efficiency_Relation": {"relation": [{"id": eff_page_id}]}
        }
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

def sync_records():
    # Fetch both READY_FOR_NOTION and FAILED states to allow retry!
    records = get_records_by_state([LifecycleState.READY_FOR_NOTION, LifecycleState.FAILED])
    if not records:
        print("No records to sync.")
        return

    os.makedirs(".tmp", exist_ok=True)
    
    from sqlalchemy.orm import Session
    from tools.database import engine_default, UnifiedDepartment
    import tools.database
    
    engine = engine_default or init_db()
    
    with Session(engine) as session:
        for r in records:
            try:
                db_record = session.get(UnifiedDepartment, r["id"])
                if not db_record:
                    continue
                
                # If already fully synced, skip
                if db_record.state == LifecycleState.SYNCED.value:
                    continue

                payload = r["payload"]
                eff = payload.get("efficiency", {})
                eff_audit = payload.get("audit_efficiency", {})
                tact = payload.get("tactical", {})
                tact_audit = payload.get("audit_tactical", {})
                asset = payload.get("asset", "Unknown")

                # Before generating a new page in Notion for the Efficiency database,
                # query the local database record. If an efficiency_page_id is already populated,
                # skip the network call and reuse the identifier.
                eff_page_id = db_record.efficiency_page_id
                if not eff_page_id:
                    eff_notion_payload = map_efficiency_payload(r["id"], asset, eff, eff_audit)
                    eff_resp = post_to_notion(eff_notion_payload)
                    eff_page_id = eff_resp["id"]
                    db_record.efficiency_page_id = eff_page_id
                    session.commit()
                    print(f"Created Efficiency page in Notion: {eff_page_id}")
                else:
                    print(f"Skipping Efficiency page creation, reusing ID: {eff_page_id}")

                # Execute the Tactical database network call.
                tact_page_id = db_record.tactical_page_id
                if not tact_page_id:
                    tact_notion_payload = map_tactical_payload(r["id"], tact, tact_audit, eff_page_id)
                    tact_resp = post_to_notion(tact_notion_payload)
                    tact_page_id = tact_resp["id"]
                    db_record.tactical_page_id = tact_page_id
                    session.commit()
                    print(f"Created Tactical page in Notion: {tact_page_id}")

                # Transition record state to SYNCED
                db_record.state = LifecycleState.SYNCED.value
                session.commit()
                print(f"Successfully synced trade {r['id']}")

            except Exception as e:
                session.rollback()
                error_msg = f"Failed to sync trade {r['id']}: {str(e)}"
                print(error_msg)
                with open(".tmp/sync_errors.log", "a") as f:
                    f.write(error_msg + "\n")
                
                try:
                    with Session(engine) as err_session:
                        err_record = err_session.get(UnifiedDepartment, r["id"])
                        if err_record:
                            err_record.state = LifecycleState.FAILED.value
                            err_session.commit()
                except Exception as db_err:
                    print(f"Failed to set state to FAILED: {db_err}")

if __name__ == "__main__":
    init_db()
    sync_records()
