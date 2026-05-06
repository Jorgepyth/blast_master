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
    records = get_records_by_state(LifecycleState.READY_FOR_NOTION)
    if not records:
        print("No records to sync.")
        return

    os.makedirs(".tmp", exist_ok=True)
    
    for r in records:
        try:
            payload = r["payload"]
            eff = payload.get("efficiency", {})
            eff_audit = payload.get("audit_efficiency", {})
            tact = payload.get("tactical", {})
            tact_audit = payload.get("audit_tactical", {})
            asset = payload.get("asset", "Unknown")

            eff_notion_payload = map_efficiency_payload(r["id"], asset, eff, eff_audit)
            eff_resp = post_to_notion(eff_notion_payload)
            eff_page_id = eff_resp["id"]

            tact_notion_payload = map_tactical_payload(r["id"], tact, tact_audit, eff_page_id)
            post_to_notion(tact_notion_payload)

            update_record_state(r["id"], LifecycleState.SYNCED)
            print(f"Successfully synced trade {r['id']}")

        except Exception as e:
            error_msg = f"Failed to sync trade {r['id']}: {str(e)}"
            print(error_msg)
            with open(".tmp/sync_errors.log", "a") as f:
                f.write(error_msg + "\n")
            update_record_state(r["id"], LifecycleState.FAILED)

if __name__ == "__main__":
    init_db()
    sync_records()
