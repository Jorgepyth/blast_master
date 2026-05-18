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
from InquirerPy.separator import Separator

from cli.schemas.efficiency import EfficiencyAnalysis, Direction, Strength
from cli.schemas.audit_efficiency import EfficiencyAudit, StructuralBias, ResolutionType, StructuralResolution, FailureReason
from cli.schemas.tactical import TacticalAnalysis, Hierarchy, Timeframe, FractalType, TacticalClassification, TradeStatus
from cli.schemas.audit_tactical import TacticalAudit, ComplianceState, TierSetup, MarketState, Session, ExitType, TradeDecision, FollowedPlan, PrimaryEmotion, SetupType, HTFTrendContext, TrendContext, ConfirmationStatus, ConfirmationParams, Emotions, BehavioralErrors, CognitivePatterns
from tools.database import init_db, update_record_state, get_records_by_state, LifecycleState, add_asset, get_assets
import json
import os
from enum import Enum

CACHE_FILE = ".data/paused_audits.json"

def has_paused_state(trade_id, audit_type):
    if not os.path.exists(CACHE_FILE): return False
    try:
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
            return bool(cache.get(trade_id, {}).get(audit_type))
    except Exception:
        return False

class AuditSession:
    def __init__(self, trade_id, audit_type):
        self.trade_id = trade_id
        self.audit_type = audit_type
        self.state = {}
        self.load_state()

    def load_state(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    cache = json.load(f)
                    self.state = cache.get(self.trade_id, {}).get(self.audit_type, {})
            except Exception:
                pass

    def save_state(self):
        cache = {}
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    cache = json.load(f)
            except Exception:
                pass
        
        if self.trade_id not in cache:
            cache[self.trade_id] = {}
            
        serialized_state = {}
        for k, v in self.state.items():
            if isinstance(v, Enum):
                serialized_state[k] = v.value
            elif isinstance(v, datetime.datetime):
                serialized_state[k] = v.strftime("%Y-%m-%d %H:%M")
            elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], Enum):
                serialized_state[k] = [item.value for item in v]
            else:
                serialized_state[k] = v
                
        cache[self.trade_id][self.audit_type] = serialized_state
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)

    def clear_state(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    cache = json.load(f)
                if self.trade_id in cache and self.audit_type in cache[self.trade_id]:
                    del cache[self.trade_id][self.audit_type]
                    if not cache[self.trade_id]:
                        del cache[self.trade_id]
                    with open(CACHE_FILE, "w") as f:
                        json.dump(cache, f)
            except Exception:
                pass

    def prompt(self, key, func, *args, **kwargs):
        if key in self.state:
            val = self.state[key]
            if func.__name__ == 'get_enum_choice':
                enum_class = args[1] if len(args) > 1 else kwargs.get('enum_class')
                if enum_class:
                    try: val = enum_class(val)
                    except ValueError: pass
            elif func.__name__ == 'get_multi_enum_choice':
                enum_class = args[1] if len(args) > 1 else kwargs.get('enum_class')
                if enum_class and isinstance(val, list):
                    try: val = [enum_class(i) for i in val]
                    except ValueError: pass
            elif func.__name__ == 'get_mandatory_datetime' and isinstance(val, str):
                try: val = datetime.datetime.strptime(val, "%Y-%m-%d %H:%M")
                except ValueError: pass
                
            display_val = val.value if isinstance(val, Enum) else [v.value if isinstance(v, Enum) else v for v in val] if isinstance(val, list) else val
            console.print(f"[dim]Loaded {key}: {display_val}[/dim]")
            return val
            
        try:
            val = func(*args, **kwargs)
            self.state[key] = val
            return val
        except PauseAuditException:
            self.save_state()
            raise

ACTIVE_SESSION = None
TEST_SESSIONS_FILE = ".data/test_sessions.json"

class TestSessionManager:
    @staticmethod
    def load_sessions():
        if not os.path.exists(TEST_SESSIONS_FILE):
            return {}
        try:
            with open(TEST_SESSIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def save_sessions(sessions):
        os.makedirs(os.path.dirname(TEST_SESSIONS_FILE), exist_ok=True)
        with open(TEST_SESSIONS_FILE, "w") as f:
            json.dump(sessions, f, indent=4)

    @staticmethod
    def create_session(name):
        import uuid
        session_id = str(uuid.uuid4())
        sessions = TestSessionManager.load_sessions()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        sessions[session_id] = {
            "name": name,
            "created_at": now,
            "last_played": now
        }
        TestSessionManager.save_sessions(sessions)
        return session_id, sessions[session_id]

    @staticmethod
    def update_last_played(session_id):
        sessions = TestSessionManager.load_sessions()
        if session_id in sessions:
            sessions[session_id]["last_played"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            TestSessionManager.save_sessions(sessions)

    @staticmethod
    def delete_session(session_id):
        sessions = TestSessionManager.load_sessions()
        if session_id in sessions:
            del sessions[session_id]
            TestSessionManager.save_sessions(sessions)
            # Optionally delete the db file
            db_path = f".data/{session_id}.db"
            if os.path.exists(db_path):
                try: os.remove(db_path)
                except Exception: pass

console = Console()

class PauseAuditException(Exception):
    pass

def bind_pause(prompt):
    @prompt.register_kb("c-x")
    @prompt.register_kb("c-s")
    def _(event):
        event.app.exit(exception=PauseAuditException("Pause requested"))
    return prompt

def get_enum_choice(prompt_text, enum_class, exclude=None):
    if exclude is None:
        exclude = []
    choices = [e for e in enum_class if e.name != "SKIP" and e not in exclude]
    inq_choices = [Choice(e, name=f"[{i+1}] {e.value}") for i, e in enumerate(choices)]
    
    result = bind_pause(inquirer.select(
        message=f"{prompt_text} >",
        choices=inq_choices,
        pointer=">",
        qmark=""
    )).execute()
    return result

def get_multi_enum_choice(prompt_text, enum_class):
    choices = [e for e in enum_class if e.name != "SKIP"]
    inq_choices = [Choice(e, name=f"[{i+1}] {e.value}") for i, e in enumerate(choices)]
    
    while True:
        result = bind_pause(inquirer.checkbox(
            message=f"{prompt_text} (Select at least one) >",
            choices=inq_choices,
            pointer=">",
            qmark=""
        )).execute()
        if result:
            return result

def get_mandatory_text(prompt_text, multiline=False):
    while True:
        message = f"{prompt_text} >"
        if multiline:
            message += " (Presiona Esc + Enter para guardar)"
        val = bind_pause(inquirer.text(message=message, multiline=multiline)).execute()
        if val and val.strip():
            return val.strip()

def get_optional_text(prompt_text, multiline=False):
    message = f"{prompt_text} (Optional, press Enter to skip) >"
    if multiline:
        message += " (Presiona Esc + Enter para guardar)"
    val = bind_pause(inquirer.text(message=message, multiline=multiline)).execute()
    return val.strip() if val else None

def get_mandatory_int(prompt_text, min_val=None, max_val=None):
    def validate_int(result):
        if not result or not result.lstrip('-').isdigit(): return False
        v = int(result)
        if min_val is not None and v < min_val: return False
        if max_val is not None and v > max_val: return False
        return True
        
    range_str = f" [{min_val}-{max_val}]" if min_val is not None and max_val is not None else ""
    val = bind_pause(inquirer.text(
        message=f"{prompt_text}{range_str} >",
        validate=validate_int,
        invalid_message="Must be a valid integer in range"
    )).execute()
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

    val = bind_pause(inquirer.text(
        message=f"{prompt_text} >",
        validate=validate_float,
        invalid_message="Must be a valid float"
    )).execute()
    return float(val)

def get_mandatory_datetime(prompt_text):
    def validate_datetime(result):
        if not result: return False
        try:
            datetime.datetime.strptime(result, "%Y-%m-%d %H:%M")
            return True
        except ValueError:
            return False

    val = bind_pause(inquirer.text(
        message=f"{prompt_text} (YYYY-MM-DD HH:MM) >",
        validate=validate_datetime,
        invalid_message="Must be in format YYYY-MM-DD HH:MM"
    )).execute()
    return datetime.datetime.strptime(val, "%Y-%m-%d %H:%M")

def check_daemon_status():
    try:
        result = subprocess.run(["pgrep", "-f", "notion_sync.py"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return "[bold green]ONLINE[/bold green]"
    except Exception:
        pass
    return "[bold red]OFFLINE[/bold red]"

def flow_test_drive():
    global ACTIVE_SESSION
    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        sessions = TestSessionManager.load_sessions()
        
        console.rule("[bold cyan]Test Drive Sessions[/bold cyan]")
        console.print()
        
        choices = [
            Choice("create", name="[+] Create New Session"),
            Choice("delete", name="[-] Delete Session"),
            Choice("back", name="[<] Back to Main Menu"),
            Separator()
        ]
        
        for s_id, s_data in sessions.items():
            name = s_data.get("name", "Unknown")
            last_p = s_data.get("last_played", "Unknown")
            choices.append(Choice(s_id, name=f"► {name} (Last Played: {last_p})"))
            
        choice = inquirer.select(
            message="Select Action >",
            choices=choices,
            pointer=">",
            qmark=""
        ).execute()
        
        if choice == "back":
            return
        elif choice == "create":
            name = get_mandatory_text("Enter session name")
            session_id, _ = TestSessionManager.create_session(name)
            ACTIVE_SESSION = {"id": session_id, "name": name}
            
            from tools.database import copy_assets_to_current_db, init_db
            init_db(f"sqlite:///.data/{session_id}.db")
            copy_assets_to_current_db()
            
            console.print(f"[green]Session '{name}' created and loaded![/green]")
            input("Press Enter to continue...")
            return
        elif choice == "delete":
            if not sessions:
                console.print("[red]No sessions to delete.[/red]")
                input("Press Enter to continue...")
                continue
                
            del_choices = [Choice("cancel", name="Cancel")] + [Choice(s_id, name=f"{s_data['name']}") for s_id, s_data in sessions.items()]
            del_choice = inquirer.select(
                message="Select session to delete >",
                choices=del_choices,
                pointer=">",
                qmark=""
            ).execute()
            
            if del_choice != "cancel":
                confirm = inquirer.confirm(message="Are you sure you want to delete this session?").execute()
                if confirm:
                    TestSessionManager.delete_session(del_choice)
                    if ACTIVE_SESSION and ACTIVE_SESSION["id"] == del_choice:
                        ACTIVE_SESSION = None
                        from tools.database import init_db
                        init_db("sqlite:///.data/journal.db")
                    console.print("[green]Session deleted.[/green]")
                    input("Press Enter to continue...")
        else:
            s_id = choice
            ACTIVE_SESSION = {"id": s_id, "name": sessions[s_id]["name"]}
            TestSessionManager.update_last_played(s_id)
            from tools.database import init_db
            init_db(f"sqlite:///.data/{s_id}.db")
            console.print(f"[green]Session '{ACTIVE_SESSION['name']}' loaded![/green]")
            input("Press Enter to continue...")
            return

def render_ui():
    audit_records = get_records_by_state(LifecycleState.PENDING_AUDITS)
    pending_audits = len(audit_records)
    
    sync_status = check_daemon_status()
    
    if ACTIVE_SESSION:
        console.rule(f"[bold magenta]TEST FLIGHT --- {ACTIVE_SESSION['name']}[/bold magenta]")
    else:
        console.rule("[bold cyan]B.L.A.S.T. ENGINE v1.0 [STATUS: OPERATIONAL][/bold cyan]")
    
    status_text = Text.from_markup(f"Pending Audits: [bold yellow]{pending_audits}[/bold yellow]  |  Notion Sync: {sync_status}", justify="center")
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
    global ACTIVE_SESSION
    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        render_ui()
        
        menu_choices = [
            Choice("1", name="[1] New Unified Analysis"),
            Choice("2", name="[2] Pending Audits"),
            Choice("3", name="[3] Run Notion Sync"),
            Choice("config", name="[4] Configuration")
        ]
        
        if ACTIVE_SESSION:
            menu_choices.append(Choice("exit", name=f"[5] Exit Test Flight - {ACTIVE_SESSION['name']}"))
        else:
            menu_choices.append(Choice("test_drive", name="[T] Test Drive"))
            menu_choices.append(Choice("exit", name="[5] Exit"))
            
        choice = inquirer.select(
            message="BLAST >",
            choices=menu_choices,
            pointer=">",
            qmark=""
        ).execute()
        
        if choice == "1":
            flow_new_analysis()
        elif choice == "2":
            flow_pending_audits()
        elif choice == "3":
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
        elif choice == "test_drive":
            flow_test_drive()
        elif choice == "exit":
            if ACTIVE_SESSION:
                ACTIVE_SESSION = None
                from tools.database import init_db
                init_db("sqlite:///.data/journal.db")
                console.print("[yellow]Exiting Test Flight and restoring main database connection...[/yellow]")
                import time
                time.sleep(1)
            else:
                console.print("[yellow]Exiting B.L.A.S.T...[/yellow]")
                break

def format_percentage(value):
    return f"{value * 100:.3f}%"

def flow_new_analysis():
    trade_id = str(uuid.uuid4())
    console.print(f"\n[dim]Initialized new unified trade context: {trade_id}[/dim]")
    
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

    console.print(Panel("Unified Market Analysis (P0-P4)", style="bold blue"))
    
    # P0
    p0_thesis = get_mandatory_text("P0 Thesis", multiline=True)
    p0_dir = get_enum_choice("P0 Direction", Direction)
    p0_str = get_enum_choice("P0 Strength", Strength)
    
    # P1
    p1_thesis = get_mandatory_text("P1 Thesis", multiline=True)
    p1_dir = get_enum_choice("P1 Direction", Direction)
    p1_str = get_enum_choice("P1 Strength", Strength)
    p1_tf = get_enum_choice("P1 Timeframe", Timeframe)
    p1_type = get_enum_choice("P1 Fractal Type", FractalType)
    nodes_l1 = get_mandatory_int("Nodes L1")
    nodes_l2 = get_mandatory_int("Nodes L2")
    
    # P2
    p2_thesis = get_mandatory_text("P2 Thesis", multiline=True)
    p2_dir = get_enum_choice("P2 Direction", Direction)
    p2_str = get_enum_choice("P2 Strength", Strength)
    
    # P3
    p3_thesis = get_mandatory_text("P3 Thesis", multiline=True)
    p3_dir = get_enum_choice("P3 Direction", Direction)
    p3_str = get_enum_choice("P3 Strength", Strength)
    
    # P4
    p4_thesis = get_mandatory_text("P4 Thesis", multiline=True)
    p4_dir = get_enum_choice("P4 Direction", Direction)
    p4_str = get_enum_choice("P4 Strength", Strength)
    p4_hier = get_enum_choice("P4 Hierarchy", Hierarchy)

    # Meta
    edge_desc = get_mandatory_text("Efficiency Edge Description", multiline=True)

    # Calculations
    def get_dir_val(d): return 1 if d == Direction.LONG else -1 if d == Direction.SHORT else 0
    def get_str_val(s): return 2 if s == Strength.STRONG else 1 if s == Strength.MID else 0

    x0 = get_dir_val(p0_dir) * get_str_val(p0_str)
    x1 = get_dir_val(p1_dir) * get_str_val(p1_str)
    x2 = get_dir_val(p2_dir) * get_str_val(p2_str)
    x3 = get_dir_val(p3_dir) * get_str_val(p3_str)
    x4 = get_dir_val(p4_dir) * get_str_val(p4_str)

    i_cd = (0.30 * x0 + 0.25 * x1 + 0.15 * x2 + 0.10 * x3 + 0.20 * x4) / 2.0

    if abs(i_cd) < 0.26:
        market_bias = "Choppy / Neutral"
        action = "NO_TRADE"
        alloc_scale = 0.0
    elif i_cd >= 0.26:
        market_bias = "Bullish"
        action = "EXECUTE LONG"
        alloc_scale = abs(i_cd)
    else:
        market_bias = "Bearish"
        action = "EXECUTE SHORT"
        alloc_scale = abs(i_cd)

    console.print(f"\n[bold magenta]Resulting Market Bias:[/bold magenta] [bold cyan]{market_bias}[/bold cyan] (I_CD: {i_cd:.4f})\n")

    bias_a = get_enum_choice("Initial Structural Bias (Bias A)", StructuralBias)
    tact_class = get_enum_choice("Tactical Classification", TacticalClassification)

    # Instantiation
    try:
        efficiency = EfficiencyAnalysis(
            p0_direction=p0_dir, p0_strength=p0_str, p0_thesis=p0_thesis,
            p2_direction=p2_dir, p2_strength=p2_str, p2_thesis=p2_thesis,
            p3_direction=p3_dir, p3_strength=p3_str, p3_thesis=p3_thesis,
            Calc_edge=i_cd, Market_Bias=market_bias, edge_description=edge_desc
        )
        tactical = TacticalAnalysis(
            p4_direction=p4_dir, p4_strength=p4_str, p4_thesis=p4_thesis,
            p1_direction=p1_dir, p1_strength=p1_str, p1_thesis=p1_thesis,
            p4_hierarchy=p4_hier, p1_timeframe=p1_tf, p1_type=p1_type,
            nodes_l1=nodes_l1, nodes_l2=nodes_l2, tactical_classification=tact_class,
            calc_edge=i_cd
        )
    except Exception as e:
        console.print(f"[bold red]Validation Error: {e}[/bold red]")
        input("Press Enter to continue...")
        return

    # Review Panel
    rev_text = Text()
    rev_text.append(f"Resulting Market Bias: {market_bias}\n", style="bold green" if action != "NO_TRADE" else "bold yellow")
    rev_text.append(f"Raw Index Value (I_CD): {i_cd:.4f}\n")
    rev_text.append(f"Action: {action}\n")
    rev_text.append(f"Target Risk Allocation Scale: {alloc_scale:.4f}")

    console.print(Panel(rev_text, title="Review: Unified Analysis", border_style="cyan"))
    if not inquirer.confirm(message="Confirm and Save?").execute():
        console.print("[red]Transaction cancelled.[/red]")
        input("Press Enter to continue...")
        return

    from tools.database import engine_default, UnifiedDepartment, AnalysisLayer, EfficiencyAudit as ModelEfficiencyAudit
    from sqlalchemy.orm import Session
    
    with Session(engine_default) as session:
        try:
            new_record = UnifiedDepartment(
                id=trade_id,
                state=LifecycleState.PENDING_AUDITS.value,
                asset=asset,
                market_bias=market_bias,
                calc_edge=i_cd,
                edge_description=edge_desc,
                trade_status=None,
                p4_hierarchy=tactical.p4_hierarchy.value,
                p1_timeframe=tactical.p1_timeframe.value,
                p1_type=tactical.p1_type.value,
                nodes_l1=tactical.nodes_l1,
                nodes_l2=tactical.nodes_l2,
                tactical_classification=tactical.tactical_classification.value,
                long_prob=tactical.long_prob,
                short_prob=tactical.short_prob,
                no_trade_prob=tactical.no_trade_prob
            )
            session.add(new_record)

            new_ea = ModelEfficiencyAudit(id=trade_id, bias_a=bias_a.value)
            session.add(new_ea)

            for layer_dict in efficiency.to_db_layers():
                al = AnalysisLayer(
                    trade_id=trade_id, department='EFFICIENCY', **layer_dict.model_dump()
                )
                session.add(al)

            for layer_dict in tactical.to_db_layers():
                al = AnalysisLayer(
                    trade_id=trade_id, department='TACTICAL', **layer_dict.model_dump()
                )
                session.add(al)

            session.commit()
            console.print("[green]Unified Analysis Saved. Transitioned directly to PENDING_AUDITS.[/green]")
        except Exception as e:
            session.rollback()
            console.print(f"[bold red]Transaction rolled back due to error: {e}[/bold red]")
            input("Press Enter to continue...")
            return

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
        has_eff = r["payload"].get("audit_efficiency", {}).get("real_bias_b") is not None
        has_tac = "audit_tactical" in r["payload"]
        is_eff_paused = has_paused_state(r["id"], "eff")
        is_tac_paused = has_paused_state(r["id"], "tac")
        
        if has_eff: eff_status = "[green][✓] Eff[/green]"
        elif is_eff_paused: eff_status = "[yellow][✗] Eff[/yellow]"
        else: eff_status = "[red][ ] Eff[/red]"
        
        if has_tac: tac_status = "[green][✓] Tac[/green]"
        elif is_tac_paused: tac_status = "[yellow][✗] Tac[/yellow]"
        else: tac_status = "[red][ ] Tac[/red]"
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
        market_bias_val = payload.get("efficiency", {}).get("Market_Bias", "Unknown")
        
        if not bias_a_val:
            bias_a_val = market_bias_val
            
        console.print(f"[bold cyan]Original Market Bias (Bias A):[/bold cyan] {market_bias_val} / {bias_a_val}")
        
        try:
            bias_a = StructuralBias(bias_a_val)
        except ValueError:
            bias_a = StructuralBias.NO_BIAS_CHOPPY

        session = AuditSession(trade_id, "eff")
        try:
            real_bias_b = session.prompt("real_bias_b", get_enum_choice, "Real Bias B", StructuralBias)
            res_type = session.prompt("res_type", get_enum_choice, "Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN])
            struct_res = session.prompt("struct_res", get_enum_choice, "Structural Resolution", StructuralResolution)
            fail_reason = session.prompt("fail_reason", get_enum_choice, "Failure Reason", FailureReason)
            lesson_eff = session.prompt("lesson_eff", get_optional_text, "Efficiency Lesson Learned")
        except PauseAuditException:
            console.print("\n[bold yellow]Audit Paused. Progress saved.[/bold yellow]")
            input("Press Enter to continue...")
            return

        session.clear_state()

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
        
        new_payload["audit_efficiency"] = audit_eff.model_dump()
        console.print("[green]Efficiency Audit saved.[/green]")

    elif audit_choice == "tac":
        console.print(Panel("Tactical Audit", style="bold magenta"))
        session = AuditSession(trade_id, "tac")
        try:
            t_status = session.prompt("t_status", get_enum_choice, "Trade Status", TradeStatus)
            
            if t_status == TradeStatus.NO_TAKEN:
                t_comp = session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                audit_tactical = TacticalAudit(
                    tactical_id=trade_id,
                    trade_status=t_status,
                    compliance=t_comp
                )
                new_payload["trade_status"] = t_status.value
                new_payload["audit_tactical"] = audit_tactical.model_dump()
                console.print("[green]Tactical Audit saved (No Trade Taken).[/green]")
                session.clear_state()
            else:
                htf_trend = session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                ltf_trend = session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)
                sl = session.prompt("sl", get_mandatory_float, "Stop Loss")
                entry_p = session.prompt("entry_p", get_mandatory_float, "Entry Price")
                conf_params = session.prompt("conf_params", get_multi_enum_choice, "Confirmation Params", ConfirmationParams)
                size = session.prompt("size", get_mandatory_float, "Size")
                tp = session.prompt("tp", get_mandatory_float, "Take Profit")
                entry_time = session.prompt("entry_time", get_mandatory_datetime, "Entry Time")
                emotions = session.prompt("emotions", get_multi_enum_choice, "Emotions", Emotions)
                pre_trade_emotions = session.prompt("pre_trade_emotions", get_mandatory_text, "Pre Trade Emotions")
                p_emotion = session.prompt("p_emotion", get_enum_choice, "Primary Emotion", PrimaryEmotion)
                mental_clarity = session.prompt("mental_clarity", get_mandatory_int, "Mental Clarity Level", 1, 5)
                impatience = session.prompt("impatience", get_mandatory_int, "Impatience Level", 1, 5)
                anxiety = session.prompt("anxiety", get_mandatory_int, "Anxiety Level", 1, 5)
                mid_trade_emotions = session.prompt("mid_trade_emotions", get_mandatory_text, "Mid Trade Emotions")
                post_trade_emotions = session.prompt("post_trade_emotions", get_mandatory_text, "Post Trade Emotions")
                exit_time = session.prompt("exit_time", get_mandatory_datetime, "Exit Time")
                exit_type = session.prompt("exit_type", get_enum_choice, "Exit Type", ExitType)
                conf_status = session.prompt("conf_status", get_enum_choice, "Confirmation Status", ConfirmationStatus)
                close_p = session.prompt("close_p", get_mandatory_float, "Closing Price")
                
                def ask_could_hit_tp():
                    return bind_pause(inquirer.select(
                        message="Could hit TP? >",
                        choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                        pointer=">",
                        qmark=""
                    )).execute()
                    
                could_hit_tp = session.prompt("could_hit_tp", ask_could_hit_tp)
                t_comp = session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                tier_setup = session.prompt("tier_setup", get_enum_choice, "Tier Setup", TierSetup)
                market_state = session.prompt("market_state", get_enum_choice, "Market State", MarketState)
                f_plan = session.prompt("f_plan", get_enum_choice, "Followed Plan", FollowedPlan)
                setup_t = session.prompt("setup_t", get_enum_choice, "Setup Type", SetupType)
                behav_errors = session.prompt("behav_errors", get_multi_enum_choice, "Behavioral Errors", BehavioralErrors)
                cog_patterns = session.prompt("cog_patterns", get_multi_enum_choice, "Cognitive Patterns", CognitivePatterns)
                cost = session.prompt("cost", get_mandatory_float, "Cost")
                mae = session.prompt("mae", get_mandatory_float, "MAE (0 <= MAE <= 10)", min_val=0, max_val=10)
                mfe = session.prompt("mfe", get_mandatory_float, "MFE (0 <= MFE <= 10)", min_val=0, max_val=10)
                lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
        except PauseAuditException:
            console.print("\n[bold yellow]Audit Paused. Progress saved.[/bold yellow]")
            input("Press Enter to continue...")
            return
            
        session.clear_state()
        
        audit_tactical = TacticalAudit(
            tactical_id=trade_id,
            trade_status=t_status,
            htf_trend_context=htf_trend,
            ltf_trend_context=ltf_trend,
            stop_loss=sl,
            entry_price=entry_p,
            confirmation_params=conf_params,
            size=size,
            take_profit=tp,
            entry_time=entry_time,
            emotions=emotions,
            pre_trade_emotions=pre_trade_emotions,
            primary_emotion=p_emotion,
            mental_clarity_level=mental_clarity,
            impatience_level=impatience,
            anxiety_level=anxiety,
            mid_trade_emotions=mid_trade_emotions,
            post_trade_emotions=post_trade_emotions,
            exit_time=exit_time,
            exit_type=exit_type,
            confirmation_status=conf_status,
            closing_price=close_p,
            could_hit_tp=could_hit_tp,
            compliance=t_comp,
            tier_setup=tier_setup,
            market_state=market_state,
            followed_plan=f_plan,
            setup_type=setup_t,
            behavioral_errors=behav_errors,
            cognitive_patterns=cog_patterns,
            cost=cost,
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
    has_eff_final = new_payload.get("audit_efficiency", {}).get("real_bias_b") is not None
    has_tac_final = "audit_tactical" in new_payload
    if has_eff_final and has_tac_final:
        final_state = LifecycleState.READY_FOR_NOTION
        console.print("[bold cyan]Both audits complete! Record transitioning to READY_FOR_NOTION.[/bold cyan]")
    else:
        final_state = LifecycleState.PENDING_AUDITS
        console.print("[yellow]Record remains in PENDING_AUDITS until both components are complete.[/yellow]")
        
    update_record_state(trade_id, final_state, append_payload=new_payload)
    input("Press Enter to continue...")

if __name__ == "__main__":
    cli()
