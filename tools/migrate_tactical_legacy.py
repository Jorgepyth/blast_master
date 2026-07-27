import sys
import os
from sqlalchemy import text

# Ensure configuration is loaded using absolute path to project root
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
from dotenv import load_dotenv
load_dotenv(env_path)

from tools.database import init_db
from sqlalchemy.orm import Session
from cli.schemas.audit_tactical import ConfirmationStatus
from tools.database import TacticalAudit

def main():
    engine = init_db()
    with Session(engine) as session:
        # Extract all TacticalAudit where gates_failed IS NULL
        records = session.query(TacticalAudit).filter(TacticalAudit.gates_failed.is_(None)).all()
        
        if not records:
            print("No legacy records found that need migration.")
            return
            
        status_map = {
            "Yes": "S1_CLEAN",
            "yes but bad entry point (too tight)": "S2_EARLY",
            "Yes but late entry": "S3_LATE",
            "yes but closed too early - Fear": "S4_FEAR_CLOSE",
            "No": "S6_FEAR_NO_ENTRY"
        }
        
        migrated_count = 0
        
        for record in records:
            # Map legacy status
            legacy_status = record.confirmation_status
            new_status = None
            if legacy_status:
                if isinstance(legacy_status, str):
                    for key, val in status_map.items():
                        if key.lower() in legacy_status.lower() or legacy_status.lower() in key.lower():
                            new_status = ConfirmationStatus[val]
                            break
                    if not new_status:
                        # Fallback heuristic
                        if "yes" in legacy_status.lower():
                            new_status = ConfirmationStatus.S1_CLEAN
                        else:
                            new_status = ConfirmationStatus.S6_FEAR_NO_ENTRY
                elif hasattr(legacy_status, 'name'):
                    legacy_str = legacy_status.name.replace("_", " ")
                    for key, val in status_map.items():
                        if key.lower() in legacy_str.lower() or legacy_str.lower() in key.lower():
                            new_status = ConfirmationStatus[val]
                            break
                    if not new_status:
                        if "yes" in legacy_str.lower():
                            new_status = ConfirmationStatus.S1_CLEAN
                        else:
                            new_status = ConfirmationStatus.S6_FEAR_NO_ENTRY

            if new_status:
                record.confirmation_status = new_status
            else:
                record.confirmation_status = ConfirmationStatus.S6_FEAR_NO_ENTRY

            # Nullify new gates/confirmations explicitly just in case
            record.gates_failed = 0
            record.confirmations_count = 0
            record.g1_trend_15m = False
            record.g2_fractal_trend = False
            record.g3_limit_order = False
            record.g4_breathing = False
            record.g5_manual_cooldown = False
            record.g6_sl_validated = False
            record.g7_tp_validated = False
            record.c1_kl_support = False
            record.c2_fractal_std = False
            record.c3_fractal_1m = False
            record.c4_fractal_1h = False
            record.c5_kl_target = False
            record.c6_liquidity = False
            record.c7_retracement = False
            record.c8_convergence_15m = False
            record.mfe_potencial_estimado = None
            
            migrated_count += 1
            
        session.commit()
        print(f"Successfully migrated {migrated_count} legacy tactical records to Motor B architecture.")

if __name__ == "__main__":
    main()
