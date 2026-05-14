import click
import uuid
import datetime
import subprocess
import os

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, IntPrompt
from rich.layout import Layout
from rich.columns import Columns
from rich.align import Align
from rich.text import Text
from rich import box

from InquirerPy import inquirer
from InquirerPy.base.control import Choice

from cli.schemas.efficiency import EfficiencyAnalysis, Direction, Strength
from cli.schemas.audit_efficiency import EfficiencyAudit, StructuralBias, ResolutionType, StructuralResolution, FailureReason
from cli.schemas.tactical import TacticalAnalysis, Hierarchy, Timeframe, FractalType, TacticalClassification, TradeStatus
from cli.schemas.audit_tactical import TacticalAudit, ComplianceState, TierSetup, MarketState, Session, ExitType, TradeDecision, FollowedPlan, PrimaryEmotion, SetupType, HTFTrendContext, TrendContext, ConfirmationStatus, ConfirmationParams, Emotions, BehavioralErrors, CognitivePatterns
from tools.database import init_db, create_record, update_record_state, get_records_by_state, LifecycleState, add_asset, get_assets

console = Console()

def get_enum_choice(prompt_text, enum_class, exclude=None):
    if exclude is None:
        exclude = []
    choices = [e for e in enum_class if e.name != "SKIP" and e not in exclude]
    inq_choices = [Choice(e, name=f"[{i+1}] {e.value}") for i, e in enumerate(choices)]
    
    result = inquirer.select(
        message=f"{prompt_text} >",
        choices=inq_choices,
        pointer=">",
        qmark=""
    ).execute()
    return result

def get_multi_enum_choice(prompt_text, enum_class):
    choices = [e for e in enum_class if e.name != "SKIP"]
    inq_choices = [Choice(e, name=f"[{i+1}] {e.value}") for i, e in enumerate(choices)]
    
    while True:
        result = inquirer.checkbox(
            message=f"{prompt_text} (Select at least one) >",
            choices=inq_choices,
            pointer=">",
            qmark=""
        ).execute()
        if result:
            return result

def get_mandatory_text(prompt_text, multiline=False):
    while True:
        message = f"{prompt_text} >"
        if multiline:
            message += " (Presiona Esc + Enter para guardar)"
        val = inquirer.text(message=message, multiline=multiline).execute()
        if val and val.strip():
            return val.strip()

def get_optional_text(prompt_text, multiline=False):
    message = f"{prompt_text} (Optional, press Enter to skip) >"
    if multiline:
        message += " (Presiona Esc + Enter para guardar)"
    val = inquirer.text(message=message, multiline=multiline).execute()
    return val.strip() if val else None

def get_mandatory_int(prompt_text, min_val=None, max_val=None):
    def validate_int(result):
        if not result or not result.lstrip('-').isdigit(): return False
        v = int(result)
        if min_val is not None and v < min_val: return False
        if max_val is not None and v > max_val: return False
        return True
        
    range_str = f" [{min_val}-{max_val}]" if min_val is not None and max_val is not None else ""
    val = inquirer.text(
        message=f"{prompt_text}{range_str} >",
        validate=validate_int,
        invalid_message="Must be a valid integer in range"
    ).execute()
    return int(val)

def get_mandatory_float(prompt_text, min_val=None, max_val=None):
    def validate_float(result):
        if not result: return False
        is_float = result.replace('.', '', 1).isdigit() or (result.startswith('-') and result[1:].replace('.', '', 1).isdigit())
        if not is_float: return False
        v = float(result)
        if min_val is not None and v < min_val: return False
        if max_val is not None and v > max_val: return False
        return True

    val = inquirer.text(
        message=f"{prompt_text} >",
        validate=validate_float,
        invalid_message="Must be a valid float"
    ).execute()
    return float(val)

def get_mandatory_datetime(prompt_text):
    def validate_datetime(result):
        if not result: return False
        try:
            datetime.datetime.strptime(result, "%Y-%m-%d %H:%M")
            return True
        except ValueError:
            return False

    val = inquirer.text(
        message=f"{prompt_text} (YYYY-MM-DD HH:MM) >",
        validate=validate_datetime,
        invalid_message="Must be in format YYYY-MM-DD HH:MM"
    ).execute()
    return datetime.datetime.strptime(val, "%Y-%m-%d %H:%M")

def check_daemon_status():
    try:
        result = subprocess.run(["pgrep", "-f", "notion_sync.py"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return "[bold green]ONLINE[/bold green]"
    except Exception:
        pass
    return "[bold red]OFFLINE[/bold red]"

def render_ui():
    analysis_records = get_records_by_state(LifecycleState.ANALYSIS)
    pending_tactics = len(analysis_records)
    
    audit_records = get_records_by_state(LifecycleState.PENDING_AUDITS)
    pending_audits = len(audit_records)
    
    sync_status = check_daemon_status()
    
    console.rule("[bold cyan]B.L.A.S.T. ENGINE v1.0 [STATUS: OPERATIONAL][/bold cyan]")
    
    status_text = Text.from_markup(f"Pending Tactics: [bold yellow]{pending_tactics}[/bold yellow]  |  Pending Audits: [bold yellow]{pending_audits}[/bold yellow]  |  Notion Sync: {sync_status}", justify="center")
    console.print(status_text)
    console.print()

    btc_logo = """
⠀⠀⠀⠀⣿⡇⠀⢸⣿⡇⠀⠀⠀⠀
⠸⠿⣿⣿⣿⡿⠿⠿⣿⣿⣿⣶⣄⠀
⠀⠀⢸⣿⣿⡇⠀⠀⠀⠈⣿⣿⣿⠀
⠀⠀⢸⣿⣿⡇⠀⠀⢀⣠⣿⣿⠟⠀
⠀⠀⢸⣿⣿⡿⠿⠿⠿⣿⣿⣥⣄⠀
⠀⠀⢸⣿⣿⡇⠀⠀⠀⠀⢻⣿⣿⣧
⠀⠀⢸⣿⣿⡇⠀⠀⠀⠀⣼⣿⣿⣿
⢰⣶⣿⣿⣿⣷⣶⣶⣾⣿⣿⠿⠛⠁
⠀⠀⠀⠀⣿⡇⠀⢸⣿⡇⠀⠀⠀⠀
"""
    left_panel = Panel(Align.center(Text(btc_logo, style="bold yellow", justify="left")), border_style="green", box=box.ROUNDED)
    console.print(Columns([left_panel], align="left", expand=True))

@click.group()
def cli():
    """B.L.A.S.T Interactive Engine"""
    init_db()

@cli.command()
def start():
    """Main Menu"""
    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        render_ui()
        
        choice = inquirer.select(
            message="BLAST >",
            choices=[
                Choice("1", name="[1] New Trade Analysis"),
                Choice("2", name="[2] Resume Pending Tactics"),
                Choice("3", name="[3] Pending Audits"),
                Choice("4", name="[4] Run Notion Sync"),
                Choice("config", name="[5] Configuration"),
                Choice("exit", name="[6] Exit")
            ],
            pointer=">",
            qmark=""
        ).execute()
        
        if choice == "1":
            flow_new_analysis()
        elif choice == "2":
            flow_pending_tactics()
        elif choice == "3":
            flow_pending_audits()
        elif choice == "4":
            console.print("[cyan]Starting background Notion Sync daemon...[/cyan]")
            subprocess.Popen(["conda", "run", "-n", "blast_master", "env", "PYTHONPATH=.", "python", "tools/notion_sync.py"])
            console.print("[green]Daemon started successfully![/green]")
            input("Press Enter to continue...")
        elif choice == "config":
            config_choice = inquirer.select(
                message="Configuration >",
                choices=[
                    Choice("add_asset", name="[1] Add New Asset"),
                    Choice("back", name="[2] Back to Main Menu")
                ],
                pointer=">",
                qmark=""
            ).execute()
            
            if config_choice == "add_asset":
                new_asset = get_mandatory_text("Enter Asset Name (e.g., BTC/USDT)")
                category = get_optional_text("Enter category (default: Crypto)")
                if not category:
                    category = "Crypto"
                try:
                    add_asset(new_asset, category)
                    console.print(f"[green]Successfully added {new_asset} to database.[/green]")
                except Exception as e:
                    console.print(f"[bold red]Failed to add asset: {e}[/bold red]")
                input("Press Enter to continue...")
        elif choice == "exit":
            console.print("[yellow]Exiting B.L.A.S.T...[/yellow]")
            break

def format_percentage(value):
    return f"{value * 100:.3f}%"

def flow_new_analysis():
    trade_id = str(uuid.uuid4())
    console.print(f"\n[dim]Initialized new trade context: {trade_id}[/dim]")
    
    available_assets = get_assets()
    if available_assets:
        choices = [Choice(a, name=a) for a in available_assets]
        choices.append(Choice("CUSTOM", name="[Add Custom Asset]"))
        
        asset_choice = inquirer.select(
            message="Select Asset >",
            choices=choices,
            pointer=">",
            qmark=""
        ).execute()
        
        if asset_choice == "CUSTOM":
            asset = get_mandatory_text("Enter Asset (e.g., BTC/USDT)")
            add_asset(asset, category="Crypto")
        else:
            asset = asset_choice
    else:
        asset = get_mandatory_text("Enter Asset (e.g., BTC/USDT)")
        add_asset(asset, category="Crypto")

    # Step A (Efficiency)
    console.print(Panel("Step A: Efficiency Analysis", style="bold blue"))
    p0_dir = get_enum_choice("P0 Direction", Direction)
    p0_str = get_enum_choice("P0 Strength", Strength)
    p0_thesis = get_mandatory_text("P0 Thesis", multiline=True)
    p2_dir = get_enum_choice("P2 Direction", Direction)
    p2_str = get_enum_choice("P2 Strength", Strength)
    p2_thesis = get_mandatory_text("P2 Thesis", multiline=True)
    p3_dir = get_enum_choice("P3 Direction", Direction)
    p3_str = get_enum_choice("P3 Strength", Strength)
    p3_thesis = get_mandatory_text("P3 Thesis", multiline=True)
    edge_desc = get_mandatory_text("Efficiency Edge Description")

    efficiency = EfficiencyAnalysis(
        p0_direction=p0_dir, p0_strength=p0_str, p0_thesis=p0_thesis,
        p2_direction=p2_dir, p2_strength=p2_str, p2_thesis=p2_thesis,
        p3_direction=p3_dir, p3_strength=p3_str, p3_thesis=p3_thesis,
        edge_description=edge_desc
    )

    # UI Review Panel
    rev_text = Text()
    rev_text.append("[ P0 ]\n", style="bold cyan")
    rev_text.append(f"Direction: {efficiency.p0_direction.value} | Strength: {efficiency.p0_strength.value}\n")
    rev_text.append(f"Thesis: {efficiency.p0_thesis}\n\n")
    
    rev_text.append("[ P2 ]\n", style="bold cyan")
    rev_text.append(f"Direction: {efficiency.p2_direction.value} | Strength: {efficiency.p2_strength.value}\n")
    rev_text.append(f"Thesis: {efficiency.p2_thesis}\n\n")

    rev_text.append("[ P3 ]\n", style="bold cyan")
    rev_text.append(f"Direction: {efficiency.p3_direction.value} | Strength: {efficiency.p3_strength.value}\n")
    rev_text.append(f"Thesis: {efficiency.p3_thesis}\n\n")

    rev_text.append("--- Metrics ---\n", style="bold yellow")
    rev_text.append(f"Calc Edge: {efficiency.Calc_edge:.2f}\n")
    rev_text.append(f"Market Bias: {efficiency.Market_Bias}\n")
    rev_text.append(f"Edge Description: {efficiency.edge_description}\n")
    rev_text.append(f"Probabilities: Long {format_percentage(efficiency.Long_prob)} | Short {format_percentage(efficiency.Short_prob)} | No Trade {format_percentage(efficiency.No_trade_prob)}\n")

    console.print(Panel(rev_text, title="Review: Pre-Trade Analysis", border_style="cyan"))
    if not inquirer.confirm(message="Confirm and Save?").execute():
        console.print("[red]Transaction cancelled.[/red]")
        input("Press Enter to continue...")
        return

    create_record(trade_id, payload_data={
        "asset": asset,
        "efficiency": efficiency.model_dump(),
        "efficiency_layers": [l.model_dump() for l in efficiency.to_db_layers()]
    })

    # Step B (Audit Init)
    console.print(Panel("Step B: Structural Bias (Bias A)", style="bold blue"))
    if efficiency.Market_Bias == "Choppy / Neutral":
        bias_a = StructuralBias.NO_BIAS_CHOPPY
        console.print("[dim]Auto-injected Bias A: No_Bias(Choppy)[/dim]")
    else:
        bias_a = get_enum_choice("Select Initial Structural Bias (Bias A)", StructuralBias)

    update_record_state(trade_id, LifecycleState.PENDING_TACTICS, append_payload={"bias_a": bias_a.value if hasattr(bias_a, 'value') else bias_a})
    console.print("[green]Pre-Trade Efficiency Analysis completed. Proceed to Tactical Analysis.[/green]")
    input("Press Enter to continue...")
    return

def flow_pending_tactics():
    records = get_records_by_state(LifecycleState.PENDING_TACTICS)
    if not records:
        console.print("[yellow]No pending tactics in PENDING_TACTICS state.[/yellow]")
        input("Press Enter to continue...")
        return
        
    table = Table(title="Pending Tactics", border_style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Asset")
    table.add_column("Market Bias")
    table.add_column("Date")
    
    for r in records:
        short_id = r["id"][:8]
        asset = r["payload"].get("asset", "Unknown")
        market_bias = r["payload"].get("efficiency", {}).get("Market_Bias", "Unknown")
        date_str = r["created_at"].strftime("%Y-%m-%d %H:%M")
        table.add_row(short_id, asset, market_bias, date_str)
        
    console.print(table)
    
    short_id_choice = get_mandatory_text("Enter short ID to act on (or 'c' to cancel)")
    if short_id_choice.lower() == 'c':
        return
        
    target_record = next((r for r in records if r["id"].startswith(short_id_choice)), None)
    if not target_record:
        console.print("[red]Record not found.[/red]")
        input("Press Enter to continue...")
        return

    trade_id = target_record["id"]
    efficiency_bias = target_record["payload"].get("efficiency", {}).get("Market_Bias", "Unknown")
    efficiency_edge = target_record["payload"].get("efficiency", {}).get("Calc_edge", 0.0)
    eff_long = max(0.15, min(0.85, efficiency_edge * 0.2833))
    eff_short = max(0.15, min(0.85, -efficiency_edge * 0.2833))
    eff_nt = max(0.0, 1.0 - eff_long - eff_short)

    console.print(Panel("Step D: Tactical Analysis", style="bold blue"))
    p4_dir = get_enum_choice("P4 Direction", Direction)
    p4_str = get_enum_choice("P4 Strength", Strength)
    p4_hier = get_enum_choice("P4 Hierarchy", Hierarchy)
    p4_thesis = get_mandatory_text("P4 Thesis", multiline=True)

    p1_dir = get_enum_choice("P1 Direction", Direction)
    p1_str = get_enum_choice("P1 Strength", Strength)
    p1_tf = get_enum_choice("P1 Timeframe", Timeframe)
    p1_type = get_enum_choice("P1 Fractal Type", FractalType)
    p1_thesis = get_mandatory_text("P1 Thesis", multiline=True)
    
    nodes_l1 = get_mandatory_int("Nodes L1")
    nodes_l2 = get_mandatory_int("Nodes L2")

    tact_class = get_enum_choice("Tactical Classification", TacticalClassification)

    tactical = TacticalAnalysis(
        p4_direction=p4_dir, p4_strength=p4_str, p4_hierarchy=p4_hier, p4_thesis=p4_thesis,
        p1_direction=p1_dir, p1_strength=p1_str, p1_timeframe=p1_tf, p1_type=p1_type, p1_thesis=p1_thesis,
        nodes_l1=nodes_l1, nodes_l2=nodes_l2,
        tactical_classification=tact_class
    )

    trev_text = Text()
    trev_text.append("[ P4 ]\n", style="bold cyan")
    trev_text.append(f"Direction: {tactical.p4_direction.value} | Strength: {tactical.p4_strength.value} | Hierarchy: {tactical.p4_hierarchy.value}\n")
    trev_text.append(f"Thesis: {tactical.p4_thesis}\n\n")
    
    trev_text.append("[ P1 ]\n", style="bold cyan")
    trev_text.append(f"Direction: {tactical.p1_direction.value} | Strength: {tactical.p1_strength.value} | Timeframe: {tactical.p1_timeframe.value} | Type: {tactical.p1_type.value}\n")
    trev_text.append(f"Nodes L1: {tactical.nodes_l1} | Nodes L2: {tactical.nodes_l2}\n")
    trev_text.append(f"Thesis: {tactical.p1_thesis}\n\n")

    trev_text.append("--- Efficiency Review ---\n", style="bold yellow")
    trev_text.append(f"Structural Bias (Market Bias): {efficiency_bias}\n")
    trev_text.append(f"Calc Edge: {efficiency_edge:.2f}\n")
    trev_text.append(f"Probabilities: Long {format_percentage(eff_long)} | Short {format_percentage(eff_short)} | No Trade {format_percentage(eff_nt)}\n\n")

    trev_text.append("--- Tactical Review ---\n", style="bold yellow")
    trev_text.append(f"Tactical Classification: {tactical.tactical_classification.value}\n")
    trev_text.append(f"Calc Edge: {tactical.calc_edge:.2f}\n")
    trev_text.append(f"Probabilities: Long {format_percentage(tactical.long_prob)} | Short {format_percentage(tactical.short_prob)} | No Trade {format_percentage(tactical.no_trade_prob)}\n")

    console.print(Panel(trev_text, title="Review: Tactical Analysis", border_style="cyan"))
    if not inquirer.confirm(message="Confirm and Save?").execute():
        console.print("[red]Transaction cancelled.[/red]")
        input("Press Enter to continue...")
        return

    tactical_bias = "Bullish" if tactical.calc_edge > 0 else "Bearish" if tactical.calc_edge < 0 else "Choppy / Neutral"
    
    if tactical_bias != efficiency_bias:
        console.print(Panel("DIVERGENCE DETECTED. AUTHORIZED RISK MAX: 5%", style="bold red"))
    else:
        console.print(Panel("DIRECTIONAL CONFLUENCE. AUTHORIZED RISK MAX: 10%", style="bold green"))

    update_record_state(trade_id, LifecycleState.PENDING_AUDITS, append_payload={
        "tactical": tactical.model_dump(),
        "tactical_layers": [l.model_dump() for l in tactical.to_db_layers()]
    })
    console.print("[cyan]Saved Analysis. Transitioned to PENDING_AUDITS. Proceed to execute manually in broker.[/cyan]")
    input("Press Enter to continue...")

def flow_pending_audits():
    records = get_records_by_state(LifecycleState.PENDING_AUDITS)
    if not records:
        console.print("[yellow]No pending audits.[/yellow]")
        input("Press Enter to continue...")
        return
        
    table = Table(title="Pending Audits", border_style="magenta")
    table.add_column("ID", style="dim")
    table.add_column("Asset")
    table.add_column("Status")
    table.add_column("Date")
    
    for r in records:
        short_id = r["id"][:8]
        asset = r["payload"].get("asset", "Unknown")
        has_eff = "audit_efficiency" in r["payload"]
        has_tac = "audit_tactical" in r["payload"]
        eff_status = "[green][✓] Eff[/green]" if has_eff else "[red][ ] Eff[/red]"
        tac_status = "[green][✓] Tac[/green]" if has_tac else "[red][ ] Tac[/red]"
        status_str = f"{eff_status} | {tac_status}"
        date_str = r["created_at"].strftime("%Y-%m-%d %H:%M")
        table.add_row(short_id, asset, status_str, date_str)
        
    console.print(table)
    
    short_id_choice = get_mandatory_text("Enter short ID to audit (or 'c' to cancel)")
    if short_id_choice.lower() == 'c':
        return
        
    target_record = next((r for r in records if r["id"].startswith(short_id_choice)), None)
    if not target_record:
        console.print("[red]Record not found.[/red]")
        input("Press Enter to continue...")
        return
        
    trade_id = target_record["id"]
    payload = target_record["payload"]

    audit_choice = inquirer.select(
        message="Select Component to Audit >",
        choices=[
            Choice("eff", name="Efficiency Audit"),
            Choice("tac", name="Tactical Audit"),
            Choice("cancel", name="Cancel")
        ],
        pointer=">",
        qmark=""
    ).execute()

    if audit_choice == "cancel":
        return
        
    new_payload = dict(payload)

    if audit_choice == "eff":
        console.print(Panel("Efficiency Audit", style="bold magenta"))
        bias_a_val = payload.get("audit_efficiency", {}).get("bias_a")
        if not bias_a_val:
            bias_a_val = payload.get("efficiency", {}).get("Market_Bias", "Unknown")
            
        console.print(f"[bold cyan]Original Market Bias (Bias A):[/bold cyan] {bias_a_val}")
        
        try:
            bias_a = StructuralBias(bias_a_val)
        except ValueError:
            bias_a = StructuralBias.NO_BIAS_CHOPPY

        res_type = get_enum_choice("Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN])
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
            resolution_time=datetime.datetime.now(datetime.UTC),
            lesson_learned=lesson_eff
        )
        
        new_payload["audit_efficiency"] = audit_eff.model_dump()
        console.print("[green]Efficiency Audit saved.[/green]")

    elif audit_choice == "tac":
        console.print(Panel("Tactical Audit", style="bold magenta"))
        t_status = get_enum_choice("Trade Status", TradeStatus)
        t_comp = get_enum_choice("Compliance State", ComplianceState)
        
        if t_status == TradeStatus.NO_TAKEN:
            audit_tactical = TacticalAudit(
                tactical_id=trade_id,
                trade_status=t_status,
                compliance=t_comp
            )
            new_payload["trade_status"] = t_status.value
            new_payload["audit_tactical"] = audit_tactical.model_dump()
            console.print("[green]Tactical Audit saved (No Trade Taken).[/green]")
        else:
            entry_time = get_mandatory_datetime("Entry Time")
            exit_time = get_mandatory_datetime("Exit Time")

            tier_setup = get_enum_choice("Tier Setup", TierSetup)
            market_state = get_enum_choice("Market State", MarketState)
            exit_type = get_enum_choice("Exit Type", ExitType)
            f_plan = get_enum_choice("Followed Plan", FollowedPlan)
            p_emotion = get_enum_choice("Primary Emotion", PrimaryEmotion)
            setup_t = get_enum_choice("Setup Type", SetupType)
            htf_trend = get_enum_choice("HTF Trend Context", HTFTrendContext)
            ltf_trend = get_enum_choice("LTF Trend Context", TrendContext)
            conf_status = get_enum_choice("Confirmation Status", ConfirmationStatus)
            conf_params = get_multi_enum_choice("Confirmation Params", ConfirmationParams)
            
            pre_trade_emotions = get_mandatory_text("Pre Trade Emotions")
            mid_trade_emotions = get_mandatory_text("Mid Trade Emotions")
            post_trade_emotions = get_mandatory_text("Post Trade Emotions")

            anxiety = get_mandatory_int("Anxiety Level", 1, 5)
            impatience = get_mandatory_int("Impatience Level", 1, 5)
            mental_clarity = get_mandatory_int("Mental Clarity Level", 1, 5)
            
            emotions = get_multi_enum_choice("Emotions", Emotions)
            behav_errors = get_multi_enum_choice("Behavioral Errors", BehavioralErrors)
            cog_patterns = get_multi_enum_choice("Cognitive Patterns", CognitivePatterns)
            
            cost = get_mandatory_float("Cost")
            size = get_mandatory_float("Size")
            entry_p = get_mandatory_float("Entry Price")
            close_p = get_mandatory_float("Closing Price")
            tp = get_mandatory_float("Take Profit")
            sl = get_mandatory_float("Stop Loss")
            mae = get_mandatory_float("MAE (0 <= MAE <= 10)", min_val=0, max_val=10)
            mfe = get_mandatory_float("MFE (0 <= MFE <= 10)", min_val=0, max_val=10)
            
            lesson_tact = get_mandatory_text("Tactical Lesson Learned", multiline=True)
            
            audit_tactical = TacticalAudit(
                tactical_id=trade_id,
                trade_status=t_status,
                compliance=t_comp,
                entry_time=entry_time,
                exit_time=exit_time,
                tier_setup=tier_setup,
                market_state=market_state,
                exit_type=exit_type,
                followed_plan=f_plan,
                primary_emotion=p_emotion,
                setup_type=setup_t,
                htf_trend_context=htf_trend,
                ltf_trend_context=ltf_trend,
                confirmation_status=conf_status,
                confirmation_params=conf_params,
                pre_trade_emotions=pre_trade_emotions,
                mid_trade_emotions=mid_trade_emotions,
                post_trade_emotions=post_trade_emotions,
                anxiety_level=anxiety,
                impatience_level=impatience,
                mental_clarity_level=mental_clarity,
                emotions=emotions,
                behavioral_errors=behav_errors,
                cognitive_patterns=cog_patterns,
                cost=cost,
                size=size,
                entry_price=entry_p,
                closing_price=close_p,
                take_profit=tp,
                stop_loss=sl,
                mae=mae,
                mfe=mfe,
                lesson_learned=lesson_tact
            )
            
            # Post Review Panel
            rev_text = Text()
            rev_text.append("--- Automated Calculations ---\n", style="bold yellow")
            if audit_tactical.trade_decision:
                rev_text.append(f"Trade Decision: {audit_tactical.trade_decision}\n")
            if audit_tactical.trade_duration:
                rev_text.append(f"Trade Duration: {audit_tactical.trade_duration}\n")
            if audit_tactical.session:
                rev_text.append(f"Session: {audit_tactical.session}\n")
            if audit_tactical.notional_size_usd is not None:
                rev_text.append(f"Notional Size: ${audit_tactical.notional_size_usd:.2f}\n")
            if audit_tactical.risk_usd is not None:
                rev_text.append(f"Risk: ${audit_tactical.risk_usd:.2f}\n")
            if audit_tactical.r_r is not None:
                rev_text.append(f"R:R: {audit_tactical.r_r:.2f}\n")
            if audit_tactical.pnl is not None:
                rev_text.append(f"PnL: ${audit_tactical.pnl:.2f}\n")
            if audit_tactical.pnl_and_cost is not None:
                rev_text.append(f"PnL & Cost: ${audit_tactical.pnl_and_cost:.2f}\n")
            if audit_tactical.r_multiple is not None:
                rev_text.append(f"R Multiple: {audit_tactical.r_multiple:.2f}\n")
            if audit_tactical.captured_mfe is not None:
                rev_text.append(f"Captured MFE: {audit_tactical.captured_mfe:.2f}\n")
            
            console.print(Panel(rev_text, title="Review: Tactical Audit Calculation", border_style="cyan"))
            
            new_payload["trade_status"] = t_status.value
            new_payload["audit_tactical"] = audit_tactical.model_dump()
            console.print("[green]Tactical Audit saved.[/green]")

    # Promotion Rule
    if "audit_efficiency" in new_payload and "audit_tactical" in new_payload:
        final_state = LifecycleState.READY_FOR_NOTION
        console.print("[bold cyan]Both audits complete! Record transitioning to READY_FOR_NOTION.[/bold cyan]")
    else:
        final_state = LifecycleState.PENDING_AUDITS
        console.print("[yellow]Record remains in PENDING_AUDITS until both components are complete.[/yellow]")
        
    update_record_state(trade_id, final_state, append_payload=new_payload)
    input("Press Enter to continue...")

if __name__ == "__main__":
    cli()
