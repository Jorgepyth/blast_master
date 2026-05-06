import click
import uuid
import datetime
import subprocess
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, IntPrompt

from cli.schemas.efficiency import EfficiencyAnalysis, Direction, Strength
from cli.schemas.audit_efficiency import EfficiencyAudit, StructuralBias, ResolutionType, StructuralResolution, FailureReason
from cli.schemas.tactical import TacticalAnalysis, Hierarchy, Timeframe, FractalType, TacticalClassification
from cli.schemas.audit_tactical import TacticalAudit, TradeStatus, ComplianceState
from tools.database import init_db, create_record, update_record_state, get_records_by_state, LifecycleState

console = Console()

def get_enum_choice(prompt_text, enum_class):
    choices = list(enum_class)
    menu = "\n".join([f"[{i+1}] {e.value}" for i, e in enumerate(choices)])
    console.print(Panel(menu, title=prompt_text, border_style="dim"))
    while True:
        idx = IntPrompt.ask("Select option")
        if 1 <= idx <= len(choices):
            return choices[idx-1]
        console.print("[red]Invalid selection. Try again.[/red]")

@click.group()
def cli():
    """B.L.A.S.T Interactive Engine"""
    init_db()

@cli.command()
def start():
    """Main Menu"""
    while True:
        console.print()
        console.print(Panel("[1] New Trade Analysis\n[2] Pending Audits\n[3] Run Notion Sync\n[4] Exit", title="Main Menu", border_style="cyan"))
        choice = IntPrompt.ask("Select action", choices=["1", "2", "3", "4"])
        
        if choice == 1:
            flow_new_analysis()
        elif choice == 2:
            flow_pending_audits()
        elif choice == 3:
            console.print("[cyan]Starting background Notion Sync daemon...[/cyan]")
            subprocess.Popen(["conda", "run", "-n", "blast_master", "env", "PYTHONPATH=.", "python", "tools/notion_sync.py"])
            console.print("[green]Daemon started successfully![/green]")
        else:
            console.print("[yellow]Exiting B.L.A.S.T...[/yellow]")
            break

def flow_new_analysis():
    trade_id = str(uuid.uuid4())
    console.print(f"\n[dim]Initialized new trade context: {trade_id}[/dim]")
    asset = Prompt.ask("Enter Asset (e.g., BTC/USDT)")

    # Step A (Efficiency)
    console.print(Panel("Step A: Efficiency Analysis", style="bold blue"))
    p0_dir = get_enum_choice("P0 Direction", Direction)
    p0_str = get_enum_choice("P0 Strength", Strength)
    p2_dir = get_enum_choice("P2 Direction", Direction)
    p2_str = get_enum_choice("P2 Strength", Strength)
    p3_dir = get_enum_choice("P3 Direction", Direction)
    p3_str = get_enum_choice("P3 Strength", Strength)

    efficiency = EfficiencyAnalysis(
        P0_direction=p0_dir, P0_strength=p0_str,
        P2_direction=p2_dir, P2_strength=p2_str,
        P3_direction=p3_dir, P3_strength=p3_str
    )

    console.print(f"Market Bias: [bold]{efficiency.Market_Bias}[/bold]")
    console.print(f"Calc Edge: {efficiency.Calc_edge:.2f}")

    payload = {"asset": asset, "efficiency": efficiency.model_dump(mode='json')}
    create_record(trade_id, payload)

    # Step B (Audit Init)
    console.print(Panel("Step B: Structural Bias (Bias A)", style="bold blue"))
    if efficiency.Market_Bias == "Choppy / Neutral":
        bias_a = StructuralBias.NO_BIAS_CHOPPY
        console.print("[dim]Auto-injected Bias A: No_Bias(Choppy)[/dim]")
    else:
        bias_a = get_enum_choice("Select Initial Structural Bias (Bias A)", StructuralBias)

    # Step C (The Choppy Gate)
    if efficiency.Market_Bias == "Choppy / Neutral":
        console.print(Panel("BLOCKING ALERT: Choppy Market Detected. Halting Execution.", style="bold red"))
        console.print("Prompting for Immediate Efficiency Audit...")
        
        res_type = get_enum_choice("Resolution Type", ResolutionType)
        real_bias_b = get_enum_choice("Real Bias B", StructuralBias)
        struct_res = get_enum_choice("Structural Resolution", StructuralResolution)
        fail_reason = get_enum_choice("Failure Reason", FailureReason)

        audit_eff = EfficiencyAudit(
            efficiency_id=trade_id,
            bias_a=bias_a,
            resolution_type=res_type,
            real_bias_b=real_bias_b,
            structural_resolution=struct_res,
            failure_reason=fail_reason,
            resolution_time=datetime.datetime.now()
        )

        update_record_state(trade_id, LifecycleState.READY_FOR_NOTION, {"audit_efficiency": audit_eff.model_dump(mode='json')})
        console.print("[green]Audit saved. State updated to READY_FOR_NOTION.[/green]")
        return

    # Step D (Tactical)
    console.print(Panel("Step D: Tactical Analysis", style="bold blue"))
    p4_dir = get_enum_choice("P4 Direction", Direction)
    p4_str = get_enum_choice("P4 Strength", Strength)
    p4_hier = get_enum_choice("P4 Hierarchy", Hierarchy)

    p1_dir = get_enum_choice("P1 Direction", Direction)
    p1_str = get_enum_choice("P1 Strength", Strength)
    p1_tf = get_enum_choice("P1 Timeframe", Timeframe)
    p1_type = get_enum_choice("P1 Fractal Type", FractalType)
    
    nodes_l1 = IntPrompt.ask("Nodes L1")
    nodes_l2 = IntPrompt.ask("Nodes L2")

    tact_class = get_enum_choice("Tactical Classification", TacticalClassification)

    tactical = TacticalAnalysis(
        p4_direction=p4_dir, p4_strength=p4_str, p4_hierarchy=p4_hier,
        p1_direction=p1_dir, p1_strength=p1_str, p1_timeframe=p1_tf, p1_type=p1_type,
        nodes_l1=nodes_l1, nodes_l2=nodes_l2,
        tactical_classification=tact_class
    )

    # Step E (Risk Routing)
    tactical_bias = "Bullish" if tactical.calc_edge > 0 else "Bearish" if tactical.calc_edge < 0 else "Choppy / Neutral"
    
    if tactical_bias != efficiency.Market_Bias:
        console.print(Panel("DIVERGENCE DETECTED. AUTHORIZED RISK MAX: 5%", style="bold yellow"))
    else:
        console.print(Panel("DIRECTIONAL CONFLUENCE. AUTHORIZED RISK MAX: 10%", style="bold green"))

    # We store tactical data and shift state
    # Including bias_a since we collected it
    update_record_state(trade_id, LifecycleState.CLOSED_PENDING, {
        "tactical": tactical.model_dump(mode='json'),
        "bias_a": bias_a.value
    })
    console.print("[cyan]Saved Analysis. Proceed to execute manually in broker.[/cyan]")

def flow_pending_audits():
    records = get_records_by_state(LifecycleState.CLOSED_PENDING)
    if not records:
        console.print("[yellow]No pending audits currently in CLOSED_PENDING state.[/yellow]")
        return
        
    table = Table(title="Pending Audits")
    table.add_column("ID", style="dim")
    table.add_column("Asset")
    table.add_column("Date")
    
    for r in records:
        short_id = r["id"][:8]
        asset = r["payload"].get("asset", "Unknown")
        date_str = r["created_at"].strftime("%Y-%m-%d %H:%M")
        table.add_row(short_id, asset, date_str)
        
    console.print(table)
    
    short_id_choice = Prompt.ask("Enter short ID to audit (or 'c' to cancel)")
    if short_id_choice.lower() == 'c':
        return
        
    target_record = next((r for r in records if r["id"].startswith(short_id_choice)), None)
    if not target_record:
        console.print("[red]Record not found.[/red]")
        return
        
    # Ask for Tactical Audit
    console.print(Panel("Tactical Audit", style="bold magenta"))
    t_status = get_enum_choice("Trade Status", TradeStatus)
    t_comp = get_enum_choice("Compliance State", ComplianceState)
    
    audit_tactical = TacticalAudit(
        tactical_id=target_record["id"],
        trade_status=t_status,
        compliance=t_comp
    )
    
    update_record_state(target_record["id"], LifecycleState.READY_FOR_NOTION, {"audit_tactical": audit_tactical.model_dump(mode='json')})
    console.print("[green]Audit saved. State updated to READY_FOR_NOTION.[/green]")

if __name__ == "__main__":
    cli()
