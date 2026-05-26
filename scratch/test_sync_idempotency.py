import os
import sys
import unittest
import requests_mock

# Add workspace to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.database import init_db, UnifiedDepartment, LifecycleState
from tools.notion_sync import sync_records
import tools.database
from sqlalchemy.orm import Session

class TestSyncIdempotency(unittest.TestCase):
    def setUp(self):
        # Initialize an in-memory database for testing
        self.engine = init_db("sqlite:///:memory:")
        tools.database.engine_default = self.engine
        
        # Insert a sample trade record
        with Session(self.engine) as session:
            record = UnifiedDepartment(
                id="test-trade-id-999",
                state=LifecycleState.READY_FOR_NOTION.value,
                asset="BTC/USDT",
                market_bias="Bullish",
                calc_edge=1.5,
                edge_description="Solid bullish support breakout thesis",
                p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
                p1_timeframe="15M",
                p1_type="1st_iteration",
                nodes_l1=3,
                nodes_l2=2,
                tactical_classification="Continuation_Pressure",
                long_prob=0.6,
                short_prob=0.2,
                no_trade_prob=0.2
            )
            session.add(record)
            session.commit()

    def test_sync_partial_failure_and_resume(self):
        url = "https://api.notion.com/v1/pages"
        
        with requests_mock.Mocker() as m:
            # First sync run: Efficiency POST succeeds, but Tactical POST fails (500 Server Error)
            m.post(url, [
                {"json": {"id": "eff_notion_page_123"}, "status_code": 200},
                {"text": "Internal Server Error", "status_code": 500}
            ])
            
            print("\n[Running First Sync Session - Simulating Tactical Fail]")
            sync_records()
            
            # Check database state after partial failure
            with Session(self.engine) as session:
                record = session.get(UnifiedDepartment, "test-trade-id-999")
                self.assertIsNotNone(record)
                self.assertEqual(record.state, LifecycleState.FAILED.value)
                self.assertEqual(record.efficiency_page_id, "eff_notion_page_123")
                self.assertIsNone(record.tactical_page_id)
                print("[SUCCESS] First sync successfully persisted efficiency_page_id and marked record FAILED")

        with requests_mock.Mocker() as m:
            # Second sync run: Resuming from FAILED state.
            # Efficiency should be SKIPPED, only Tactical POST is made and succeeds
            m.post(url, json={"id": "tact_notion_page_456"}, status_code=200)
            
            print("\n[Running Second Sync Session - Resuming Sync]")
            sync_records()
            
            # Verify network calls
            self.assertEqual(m.call_count, 1) # Only Tactical is posted
            print("[SUCCESS] Efficiency POST skipped correctly (idempotency verified)")
            
            # Check database state after successful resumption
            with Session(self.engine) as session:
                record = session.get(UnifiedDepartment, "test-trade-id-999")
                self.assertIsNotNone(record)
                self.assertEqual(record.state, LifecycleState.SYNCED.value)
                self.assertEqual(record.efficiency_page_id, "eff_notion_page_123")
                self.assertEqual(record.tactical_page_id, "tact_notion_page_456")
                print("[SUCCESS] Second sync successfully completed, saved tactical_page_id, and transitioned state to SYNCED")

if __name__ == "__main__":
    unittest.main()
