import typer
import uuid
import datetime
import subprocess
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from InquirerPy import inquirer

from cli.schemas.efficiency import EfficiencyAnalysis, Direction, Strength, AnalysisLayerInput
from cli.schemas.audit_efficiency import EfficiencyAudit, StructuralBias, ResolutionType, StructuralResolution, FailureReason
from cli.schemas.tactical import TacticalAnalysis, Hierarchy, Timeframe, FractalType, TacticalClassification, TradeStatus
from cli.schemas.audit_tactical import (
    TacticalAudit, ComplianceState, TierSetup, MarketState, Session, ExitType, TradeDecision, 
    FollowedPlan, PrimaryEmotion, SetupType, HTFTrendContext, ConfirmationStatus, Emotions, BehavioralErrors, CognitivePatterns
)
from tools.database import init_db, create_record, update_record_state, get_records_by_state, LifecycleState, get_assets, add_asset

app = typer.Typer(help="B.L.A.S.T Interactive Engine")
console = Console()

def get_enum_choice(prompt_text, enum_class):
    choices = [e.value for e in enum_class if e.name != "SKIP"]
    choice = inquirer.select(
        message=prompt_text,
        choices=choices,
    ).execute()
    for e in enum_class:
        if e.value == choice:
            return e
    return None

def get_optional_enum_choice(prompt_text, enum_class):
    choices = [{"name": e.value, "value": e} for e in enum_class if e.name != "SKIP"]
    choices.append({"name": "Skip", "value": None})
    return inquirer.select(
        message=f"{prompt_text} (Optional)",
        choices=choices,
    ).execute()

def get_optional_multi_enum_choice(prompt_text, enum_class):
    choices = [{"name": e.value, "value": e} for e in enum_class if e.name != "SKIP"]
    choices.append({"name": "Skip", "value": None})
    selected = inquirer.checkbox(
        message=f"{prompt_text} (Optional - Select multiple or none)",
        choices=choices,
    ).execute()
    
    if None in selected:
        return []
    return selected

def get_optional_int(prompt_text, min_val=None, max_val=None):
    def validate_int(result):
        if not result:
            return True
        if not result.lstrip('-').isdigit():
            return False
        if min_val is not None and int(result) < min_val:
            return False
        if max_val is not None and int(result) > max_val:
            return False
        return True
        
    range_str = f"{min_val}-{max_val}, " if min_val is not None and max_val is not None else ""
    val = inquirer.text(
        message=f"{prompt_text} ({range_str}Optional, press Enter to skip):",
        validate=validate_int,
        invalid_message="Must be a valid integer or blank"
    ).execute()
    return int(val) if val else None

def get_optional_float(prompt_text, min_val=None, max_val=None):
    def validate_float(result):
        if not result:
            return True
        is_float = result.replace('.', '', 1).isdigit() or (result.startswith('-') and result[1:].replace('.', '', 1).isdigit())
        if not is_float:
            return False
        if min_val is not None and float(result) < min_val:
            return False
        if max_val is not None and float(result) > max_val:
            return False
        return True

    val = inquirer.text(
        message=f"{prompt_text} (Optional, press Enter to skip):",
        validate=validate_float,
        invalid_message=f"Must be a valid float between {min_val} and {max_val} or blank" if min_val is not None else "Must be a valid float or blank"
    ).execute()
    return float(val) if val else None

def get_optional_text(prompt_text, multiline=False):
    message = f"{prompt_text} (Optional, press Enter to skip):"
    if multiline:
        message += " (Presiona Esc + Enter para guardar)"
    val = inquirer.text(
        message=message,
        multiline=multiline
    ).execute()
    return val if val else None



@app.callback()
def main():
    pass

@app.command()
def start():
    """Main Menu"""
    init_db()
    while True:
        console.print()
        
        # Check pending queue
        all_records = []
        for state in LifecycleState:
            if state not in (LifecycleState.COMPLETED, LifecycleState.SYNCED):
                records = get_records_by_state(state)
                all_records.extend(records)
                
        choices = []
        if all_records:
            pending_tactics = [r for r in all_records if r["state"] == LifecycleState.PENDING_TACTICS.value]
            pending_audits = [r for r in all_records if r["state"] == LifecycleState.PENDING_EFFICIENCY_AUDIT.value]
            
            if pending_tactics:
                choices.append({"name": f"Resume Pending Tactics ({len(pending_tactics)} found)", "value": "resume_tactics"})
            if pending_audits:
                choices.append({"name": f"Resume Pending Audits ({len(pending_audits)} found)", "value": "resume_audits"})
                
            choices.append({"name": "View All Pending Trades", "value": "resume_all"})
            
        choices.extend([
            {"name": "Start New Trade", "value": "new"},
            {"name": "Run Notion Sync", "value": "sync"},
            {"name": "Configuration", "value": "config"},
            {"name": "Exit", "value": "exit"}
        ])
        
        console.print(Panel("Welcome to B.L.A.S.T.", title="Main Menu", border_style="cyan"))
        choice = inquirer.select(
            message="Select action:",
            choices=choices
        ).execute()
        
        if choice == "new":
            flow_new_analysis()
        elif choice == "resume_tactics":
            flow_pending_audits([r for r in all_records if r["state"] == LifecycleState.PENDING_TACTICS.value])
        elif choice == "resume_audits":
            flow_pending_audits([r for r in all_records if r["state"] == LifecycleState.PENDING_EFFICIENCY_AUDIT.value])
        elif choice == "resume_all":
            flow_pending_audits(all_records)
        elif choice == "sync":
            console.print("[cyan]Starting background Notion Sync daemon...[/cyan]")
            subprocess.Popen(["conda", "run", "-n", "blast_master", "env", "PYTHONPATH=.", "python", "tools/notion_sync.py"])
            console.print("[green]Daemon started successfully![/green]")
        elif choice == "config":
            config_choice = inquirer.select(
                message="Configuration Menu:",
                choices=[
                    {"name": "Add default Assets", "value": "add_asset"},
                    {"name": "Back", "value": "back"}
                ]
            ).execute()
            if config_choice == "add_asset":
                new_asset = inquirer.text(message="Enter asset name (e.g., BTC/USDT):").execute()
                if new_asset.strip():
                    add_asset(new_asset.strip())
                    console.print(f"[green]Asset '{new_asset}' added to configuration.[/green]")
        elif choice == "exit":
            console.print("[yellow]Exiting B.L.A.S.T...[/yellow]")
            break

def flow_new_analysis():
    trade_id = str(uuid.uuid4())
    console.print(f"\n[dim]Initialized new trade context: {trade_id}[/dim]")
    
    assets = get_assets()
    if assets:
        choices = [{"name": a, "value": a} for a in assets]
        choices.append({"name": "Custom (Type it)", "value": "custom"})
        asset = inquirer.select(message="Select Asset:", choices=choices).execute()
        if asset == "custom":
            asset = inquirer.text(message="Enter Asset (e.g., BTC/USDT):").execute()
            if asset.strip():
                add_asset(asset.strip())
    else:
        asset = inquirer.text(message="Enter Asset (e.g., BTC/USDT):").execute()
        if asset.strip():
            add_asset(asset.strip())

    # Phase A: Efficiency
    console.print(Panel("Phase A: Pre-Trade Efficiency Analysis", style="bold blue"))
    
    p0_dir = get_enum_choice("P0 Direction", Direction)
    p0_str = get_enum_choice("P0 Strength", Strength)
    p0_thesis = get_optional_text("P0 Thesis", multiline=True)
    
    p2_dir = get_enum_choice("P2 Direction", Direction)
    p2_str = get_enum_choice("P2 Strength", Strength)
    p2_thesis = get_optional_text("P2 Thesis", multiline=True)
    
    p3_dir = get_enum_choice("P3 Direction", Direction)
    p3_str = get_enum_choice("P3 Strength", Strength)
    p3_thesis = get_optional_text("P3 Thesis", multiline=True)

    edge_desc = get_optional_text("Efficiency Edge Description")

    efficiency = EfficiencyAnalysis(
        p0_direction=p0_dir, p0_strength=p0_str, p0_thesis=p0_thesis,
        p2_direction=p2_dir, p2_strength=p2_str, p2_thesis=p2_thesis,
        p3_direction=p3_dir, p3_strength=p3_str, p3_thesis=p3_thesis,
        edge_description=edge_desc
    )

    console.print(f"Market Bias: [bold]{efficiency.Market_Bias}[/bold]")
    console.print(f"Calc Edge: {efficiency.Calc_edge:.2f}")

    create_record(trade_id, asset, efficiency)
    
    update_record_state(trade_id, LifecycleState.PENDING_TACTICS)
    console.print("[cyan]Saved Pre-Trade Analysis. Trade state updated to PENDING_TACTICS. Exiting Phase A.[/cyan]")

def flow_pending_audits(records):
    if not records:
        console.print("[yellow]No records available to resume.[/yellow]")
        return
        
    table = Table(title="Pending Trades")
    table.add_column("ID", style="dim")
    table.add_column("State")
    table.add_column("Asset")
    table.add_column("Date")
    
    for r in records:
        short_id = r["id"][:8]
        state = r["state"]
        asset = r["payload"].get("asset", "Unknown")
        date_str = r["created_at"].strftime("%Y-%m-%d %H:%M")
        table.add_row(short_id, state, asset, date_str)
        
    console.print(table)
    
    short_id_choice = inquirer.text(message="Enter short ID to act on (or 'c' to cancel):").execute()
    if short_id_choice.lower() == 'c':
        return
        
    target_record = next((r for r in records if r["id"].startswith(short_id_choice)), None)
    if not target_record:
        console.print("[red]Record not found.[/red]")
        return
        
    current_state = target_record["state"]
    if current_state == LifecycleState.PENDING_TACTICS.value:
        execute_tactical_department(target_record)
    elif current_state == LifecycleState.PENDING_EFFICIENCY_AUDIT.value:
        execute_phase_b(target_record)
    else:
        console.print(f"[yellow]Trade is in {current_state} state.[/yellow]")
        proceed = inquirer.confirm(message="Proceed anyway?").execute()
        if proceed:
            execute_phase_b(target_record)

def execute_tactical_department(target_record):
    trade_id = target_record["id"]
    console.print(Panel("Phase A: Pre-Trade Tactical Analysis", style="bold blue"))
    
    p4_dir = get_enum_choice("P4 Direction", Direction)
    p4_str = get_enum_choice("P4 Strength", Strength)
    p4_hier = get_enum_choice("P4 Hierarchy", Hierarchy)
    p4_thesis = get_optional_text("P4 Thesis", multiline=True)

    p1_dir = get_enum_choice("P1 Direction", Direction)
    p1_str = get_enum_choice("P1 Strength", Strength)
    p1_thesis = get_optional_text("P1 Thesis", multiline=True)

    p1_tf = get_enum_choice("P1 Timeframe", Timeframe)
    p1_type = get_enum_choice("P1 Fractal Type", FractalType)
    
    nodes_l1_str = inquirer.text(message="Nodes L1", validate=lambda r: r.isdigit()).execute()
    nodes_l2_str = inquirer.text(message="Nodes L2", validate=lambda r: r.isdigit()).execute()

    tact_class = get_enum_choice("Tactical Classification", TacticalClassification)

    tactical = TacticalAnalysis(
        p4_direction=p4_dir, p4_strength=p4_str, p4_thesis=p4_thesis,
        p1_direction=p1_dir, p1_strength=p1_str, p1_thesis=p1_thesis,
        p4_hierarchy=p4_hier,
        p1_timeframe=p1_tf,
        p1_type=p1_type,
        nodes_l1=int(nodes_l1_str),
        nodes_l2=int(nodes_l2_str),
        tactical_classification=tact_class
    )
    
    # Calculate Bias for Divergence Notice
    efficiency_payload = target_record["payload"].get("efficiency", {})
    market_bias = efficiency_payload.get("Market_Bias", "Choppy / Neutral")
    
    tactical_bias = "Bullish" if tactical.calc_edge > 0 else "Bearish" if tactical.calc_edge < 0 else "Choppy / Neutral"
    
    if tactical_bias != market_bias:
        console.print(Panel("DIVERGENCE DETECTED. AUTHORIZED RISK MAX: 5%", style="bold yellow"))
    else:
        console.print(Panel("DIRECTIONAL CONFLUENCE. AUTHORIZED RISK MAX: 10%", style="bold green"))

    console.print(Panel("Structural Bias (Bias A)", style="bold blue"))
    if market_bias == "Choppy / Neutral":
        bias_a = StructuralBias.NO_BIAS_CHOPPY
        console.print("[dim]Auto-injected Bias A: No_Bias(Choppy)[/dim]")
    else:
        bias_a = get_enum_choice("Select Initial Structural Bias (Bias A)", StructuralBias)

    update_record_state(trade_id, LifecycleState.PENDING_EFFICIENCY_AUDIT, tactical=tactical, bias_a=bias_a)
    console.print("[cyan]Saved Tactical Analysis. Trade state updated to PENDING_EFFICIENCY_AUDIT. Exiting Phase A.[/cyan]")

def execute_phase_b(target_record):
    trade_id = target_record["id"]
    console.print(Panel("Phase B: Post-Trade Audit", style="bold magenta"))
    
    # Audit Efficiency
    console.print("[cyan]--- Efficiency Audit ---[/cyan]")
    bias_a_val = target_record["payload"].get("audit_efficiency", {}).get("bias_a")
    if bias_a_val:
        # Match enum
        bias_a = next((e for e in StructuralBias if e.value == bias_a_val), StructuralBias.NO_BIAS_CHOPPY)
    else:
        bias_a = get_enum_choice("Select Initial Structural Bias (Bias A) [Missing]", StructuralBias)
    
    res_type = get_enum_choice("Resolution Type", ResolutionType)
    real_bias_b = get_enum_choice("Real Bias B", StructuralBias)
    struct_res = get_enum_choice("Structural Resolution", StructuralResolution)
    fail_reason = get_enum_choice("Failure Reason", FailureReason)
    
    lesson_eff = get_optional_text("Efficiency Lesson Learned")

    audit_eff = EfficiencyAudit(
        efficiency_id=trade_id,
        bias_a=bias_a,
        resolution_type=res_type,
        real_bias_b=real_bias_b,
        structural_resolution=struct_res,
        failure_reason=fail_reason,
        resolution_time=datetime.datetime.now(),
        lesson_learned=lesson_eff
    )
    
    # Audit Tactical
    console.print("[cyan]--- Tactical Audit ---[/cyan]")
    t_status = get_enum_choice("Trade Status", TradeStatus)
    
    if t_status == TradeStatus.NO_TAKEN:
        console.print("[yellow]Trade No Taken. Setting TacticalAudit fields to empty and Completing trade.[/yellow]")
        # Send an empty TacticalAudit. The model will initialize with None for optionals.
        audit_tactical = TacticalAudit(
            tactical_id=trade_id,
            compliance=ComplianceState.NO_EDGE # Default to something for compliance since it's required
        )
        
        # We must update trade_status!
        from sqlalchemy.orm import Session as DbSession
        from tools.database import engine_default, TacticalDepartment
        with DbSession(engine_default) as db_session:
            t_dept = db_session.get(TacticalDepartment, trade_id)
            if t_dept:
                t_dept.trade_status = t_status.value
            db_session.commit()
            
        update_record_state(trade_id, LifecycleState.COMPLETED, audit_efficiency=audit_eff, audit_tactical=audit_tactical)
        console.print("[green]Audits saved. State updated to COMPLETED.[/green]")
        return
        
    # We must update trade_status!
    from sqlalchemy.orm import Session as DbSession
    from tools.database import engine_default, TacticalDepartment
    with DbSession(engine_default) as db_session:
        t_dept = db_session.get(TacticalDepartment, trade_id)
        if t_dept:
            t_dept.trade_status = t_status.value
        db_session.commit()
    
    t_comp = get_enum_choice("Compliance State", ComplianceState)
    
    # Categoricals
    tier_setup = get_optional_enum_choice("Tier Setup", TierSetup)
    market_state = get_optional_enum_choice("Market State", MarketState)
    trading_session = get_optional_enum_choice("Session", Session)
    exit_type = get_optional_enum_choice("Exit Type", ExitType)
    trade_dec = get_optional_enum_choice("Trade Decision", TradeDecision)
    f_plan = get_optional_enum_choice("Followed Plan", FollowedPlan)
    p_emotion = get_optional_enum_choice("Primary Emotion", PrimaryEmotion)
    setup_t = get_optional_enum_choice("Setup Type", SetupType)
    htf_trend = get_optional_enum_choice("HTF Trend Context", HTFTrendContext)
    conf_status = get_optional_enum_choice("Confirmation Status", ConfirmationStatus)
    
    # Numerics
    anxiety = get_optional_int("Anxiety Level", 1, 10)
    impatience = get_optional_int("Impatience Level", 1, 10)
    mental_clarity = get_optional_int("Mental Clarity Level", 1, 10)
    
    # JSON arrays
    emotions = get_optional_multi_enum_choice("Emotions", Emotions)
    behav_errors = get_optional_multi_enum_choice("Behavioral Errors", BehavioralErrors)
    cog_patterns = get_optional_multi_enum_choice("Cognitive Patterns", CognitivePatterns)
    
    # Financial metrics
    risk_usd = get_optional_float("Risk USD")
    size = get_optional_float("Size")
    r_r = get_optional_float("Risk:Reward (R:R)")
    pnl = get_optional_float("PnL & Cost")
    entry_p = get_optional_float("Entry Price")
    close_p = get_optional_float("Closing Price")
    tp = get_optional_float("Take Profit")
    sl = get_optional_float("Stop Loss")
    cap_mae = get_optional_float("Captured MAE (0 <= MAE <= 10)", min_val=0, max_val=10)
    cap_mfe = get_optional_float("Captured MFE (0 <= MFE <= 10)", min_val=0, max_val=10)
    
    # Text blocks
    lesson_tact = get_optional_text("Tactical Lesson Learned")
    
    audit_tactical = TacticalAudit(
        tactical_id=trade_id,
        compliance=t_comp,
        tier_setup=tier_setup,
        market_state=market_state,
        session=trading_session,
        exit_type=exit_type,
        trade_decision=trade_dec,
        followed_plan=f_plan,
        primary_emotion=p_emotion,
        setup_type=setup_t,
        htf_trend_context=htf_trend,
        confirmation_status=conf_status,
        anxiety_level=anxiety,
        impatience_level=impatience,
        mental_clarity_level=mental_clarity,
        emotions=emotions if emotions else None,
        behavioral_errors=behav_errors if behav_errors else None,
        cognitive_patterns=cog_patterns if cog_patterns else None,
        risk_usd=risk_usd,
        size=size,
        r_r=r_r,
        pnl_and_cost=pnl,
        entry_price=entry_p,
        closing_price=close_p,
        take_profit=tp,
        stop_loss=sl,
        captured_mae=cap_mae,
        captured_mfe=cap_mfe,
        lesson_learned=lesson_tact
    )
    
    update_record_state(trade_id, LifecycleState.READY_FOR_NOTION, audit_efficiency=audit_eff, audit_tactical=audit_tactical)
    console.print("[green]Audits saved. State updated to READY_FOR_NOTION.[/green]")

if __name__ == "__main__":
    app()
