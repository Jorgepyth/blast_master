import sys
from cli.main import render_ledger_table
from rich.console import Console

rows = [
    ("1234567890", "XAUUSDT", "Bullish", 0.5, "2026-06-10", "BOS", "BOS", "Open", "Pending", "Pending")
]
console = Console()
table = render_ledger_table(rows)
console.print(table)
