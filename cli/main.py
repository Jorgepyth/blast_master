import click
import uuid
import datetime
import subprocess
import os
import signal

try:
    signal.signal(signal.SIGTSTP, signal.SIG_IGN)
except Exception:
    pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, IntPrompt
from rich.layout import Layout
from rich.columns import Columns
from rich.align import Align
from rich.text import Text
from rich import box
from rich.theme import Theme
from rich.live import Live

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

from cli.ui_manager import (
    CLIState,
    build_persistent_layout,
    get_welcome_options,
    build_welcome_body,
    render_pending_audits_table,
    render_wizard_layout,
    check_daemon_status,
    get_total_records_count,
    console
)

CACHE_FILE = ".data/paused_audits.json"

class AuditSession:
    def __init__(self, trade_id, audit_type):
        self.trade_id = trade_id
        self.audit_type = audit_type
        self.state = {}
        self.load_state()
        self.history = []

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
            if key not in self.history:
                self.history.append(key)
            return val
            
        if key not in self.history:
            self.history.append(key)
            
        try:
            val = func(*args, **kwargs)
            self.state[key] = val
            return val
        except PauseAuditException:
            self.save_state()
            raise
        except GoBackException:
            if key in self.history:
                self.history.remove(key)
            if not self.history:
                console.print("[yellow]Cannot go back further, you are at the first parameter.[/yellow]")
                raise RestartFlowException()
            else:
                prev_key = self.history.pop()
                if prev_key in self.state:
                    del self.state[prev_key]
                raise RestartFlowException()

class AnalysisSession:
    def __init__(self, trade_id):
        self.trade_id = trade_id
        self.state = {}
        self.history = []

    def prompt(self, key, func, *args, **kwargs):
        if key in self.state:
            val = self.state[key]
            display_val = val.value if isinstance(val, Enum) else [v.value if isinstance(v, Enum) else v for v in val] if isinstance(val, list) else val
            console.print(f"[dim]Loaded {key}: {display_val}[/dim]")
            if key not in self.history:
                self.history.append(key)
            return val
            
        if key not in self.history:
            self.history.append(key)
            
        if key in STEP1_KEYS:
            step_num = 1
            step_title = "Step 1/3: Structural Parameters"
        elif key in STEP2_KEYS:
            step_num = 2
            step_title = "Step 2/3: Tactical & Meta Parameters"
        else:
            step_num = None

        if step_num is not None:
            state.active_session = ACTIVE_SESSION
            state.refresh_metrics()
            render_wizard_layout(step_num, step_title, self, key, state)
            
        try:
            val = func(*args, **kwargs)
            self.state[key] = val
            return val
        except GoBackException:
            if key in self.history:
                self.history.remove(key)
            if not self.history:
                console.print("[yellow]Cannot go back further, you are at the first parameter.[/yellow]")
                raise RestartFlowException()
            else:
                prev_key = self.history.pop()
                if prev_key in self.state:
                    del self.state[prev_key]
                raise RestartFlowException()

ACTIVE_SESSION = None
state = CLIState()
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

blast_theme = Theme({
    "primary": "bold cyan",
    "secondary": "bold magenta",
    "success": "bold green",
    "warning": "bold yellow",
    "danger": "bold red",
    "muted": "dim white",
    "highlight": "bold reverse cyan",
})

console = Console(theme=blast_theme)

KEY_LABELS = {
    "asset": "Asset Selector",
    "p0_thesis": "P0 Thesis",
    "p0_dir": "P0 Direction",
    "p0_str": "P0 Strength",
    "p1_thesis": "P1 Thesis",
    "p1_dir": "P1 Direction",
    "p1_str": "P1 Strength",
    "p1_tf": "P1 Timeframe",
    "p1_type": "P1 Fractal Type",
    "nodes_l1": "Nodes L1",
    "nodes_l2": "Nodes L2",
    "p2_thesis": "P2 Thesis",
    "p2_dir": "P2 Direction",
    "p2_str": "P2 Strength",
    "p3_thesis": "P3 Thesis",
    "p3_dir": "P3 Direction",
    "p3_str": "P3 Strength",
    "p4_thesis": "P4 Thesis",
    "p4_dir": "P4 Direction",
    "p4_str": "P4 Strength",
    "p4_hier": "P4 Hierarchy",
    "edge_desc": "Edge Description",
    "bias_a": "Structural Bias (Bias A)",
    "tact_class": "Tactical Classification"
}
STEP1_KEYS = [
    "asset", 
    "p0_thesis", "p0_dir", "p0_str",
    "p2_thesis", "p2_dir", "p2_str",
    "p3_thesis", "p3_dir", "p3_str"
]

STEP2_KEYS = [
    "p1_thesis", "p1_dir", "p1_str", "p1_tf", "p1_type", "nodes_l1", "nodes_l2",
    "p4_thesis", "p4_dir", "p4_str", "p4_hier",
    "edge_desc", "bias_a", "tact_class"
]

class PauseAuditException(Exception):
    pass

class GoBackException(Exception):
    pass

class RestartFlowException(Exception):
    pass

def bind_pause(prompt):
    @prompt.register_kb("c-x")
    @prompt.register_kb("c-s")
    def _(event):
        event.app.exit(exception=PauseAuditException("Pause requested"))
        
    @prompt.register_kb("c-z")
    def _back(event):
        event.app.exit(exception=GoBackException("Go back requested"))
        
    return prompt

def get_keypress():
    import sys
    import tty
    import termios
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
        if ch == '\x03':
            raise KeyboardInterrupt()
        if ch == '\x1b':
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                if ch3 == 'A':
                    return 'up'
                elif ch3 == 'B':
                    return 'down'
                elif ch3 == 'C':
                    return 'right'
                elif ch3 == 'D':
                    return 'left'
        elif ch in ['\r', '\n']:
            return 'enter'
        elif ch.lower() in ['1', '2', '3', '4', '5', 't', 'e', 'x']:
            return ch.lower()
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return None

def get_enum_choice(prompt_text, enum_class, exclude=None):
    if exclude is None:
        exclude = []
    choices = [e for e in enum_class if e.name != "SKIP" and e not in exclude]
    inq_choices = [Choice(e, name=f"[{i+1}] {e.value}") for i, e in enumerate(choices)]
    
    result = bind_pause(inquirer.select(
        message=f"{prompt_text} >",
        choices=inq_choices,
        pointer=">",
        qmark="",
        keybindings={"skip": []}
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
            qmark="",
            keybindings={"skip": []}
        )).execute()
        if result:
            return result

def get_mandatory_text(prompt_text, multiline=False, default=""):
    while True:
        message = f"{prompt_text} >"
        if multiline:
            message += " (Presiona Esc + Enter para guardar)"
        val = bind_pause(inquirer.text(
            message=message, 
            multiline=multiline, 
            default=default, 
            keybindings={"skip": []}
        )).execute()
        if val and val.strip():
            return val.strip()
def format_indented_block(text_value, indent_spaces=11, first_line_flush=True, wrap_width=None):
    if not text_value:
        return ""
    prefix = " " * indent_spaces
    
    if wrap_width:
        import textwrap
        wrapped_lines = []
        for line in str(text_value).splitlines():
            if line.strip():
                wrapped_lines.extend(textwrap.wrap(line, width=wrap_width))
            else:
                wrapped_lines.append("")
        lines = wrapped_lines
    else:
        lines = str(text_value).splitlines()
        
    if not lines:
        return ""
    if first_line_flush:
        return lines[0] + "".join(f"\n{prefix}{line}" for line in lines[1:])
    else:
        return prefix + lines[0] + "".join(f"\n{prefix}{line}" for line in lines[1:])

def get_optional_text(prompt_text, multiline=False):
    message = f"{prompt_text} (Optional, press Enter to skip) >"
    if multiline:
        message += " (Presiona Esc + Enter para guardar)"
    val = bind_pause(inquirer.text(message=message, multiline=multiline, keybindings={"skip": []})).execute()
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
        invalid_message="Must be a valid integer in range",
        keybindings={"skip": []}
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
        invalid_message="Must be a valid float",
        keybindings={"skip": []}
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
        invalid_message="Must be in format YYYY-MM-DD HH:MM",
        keybindings={"skip": []}
    )).execute()
    return datetime.datetime.strptime(val, "%Y-%m-%d %H:%M")

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

@click.group()
def cli():
    """B.L.A.S.T Interactive Engine"""
    init_db()

@cli.command()
def start():
    """Main Menu"""
    global ACTIVE_SESSION
    
    state.active_session = ACTIVE_SESSION
    state.refresh_metrics()
    state.active_idx = 0
    
    layout = build_persistent_layout(state)
    layout["body"].update(build_welcome_body(state))
    
    with Live(layout, console=console, screen=True, auto_refresh=False) as live:
        try:
            while True:
                options = get_welcome_options(state)
                if state.active_idx >= len(options):
                    state.active_idx = 0
                    
                kp = get_keypress()
                if not kp:
                    continue
                    
                selected_choice = None
                if kp == 'up':
                    state.active_idx = (state.active_idx - 1) % len(options)
                    layout["body"].update(build_welcome_body(state))
                    live.refresh()
                elif kp == 'down':
                    state.active_idx = (state.active_idx + 1) % len(options)
                    layout["body"].update(build_welcome_body(state))
                    live.refresh()
                elif kp == 'enter':
                    selected_choice = options[state.active_idx][0]
                elif kp in [opt[0] for opt in options]:
                    for i, opt in enumerate(options):
                        if opt[0] == kp:
                            state.active_idx = i
                            layout["body"].update(build_welcome_body(state))
                            live.refresh()
                            break
                    selected_choice = kp
                    
                if selected_choice:
                    live.stop()
                    
                    try:
                        if selected_choice == "1":
                            flow_new_analysis()
                        elif selected_choice == "2":
                            flow_pending_audits()
                        elif selected_choice == "3":
                            flow_review_analysis()
                        elif selected_choice == "4":
                            console.print("[cyan]Starting background Notion Sync daemon...[/cyan]")
                            subprocess.Popen(["conda", "run", "-n", "blast_master", "env", "PYTHONPATH=.", "python", "tools/notion_sync.py"])
                            console.print("[green]Daemon started successfully![/green]")
                            input("Press Enter to continue...")
                        elif selected_choice == "5":
                            config_choice = inquirer.select(
                                message="Configuration >",
                                choices=[
                                    Choice("add_asset", name="[1] Add New Asset"),
                                    Choice("add_backdated_analysis", name="[2] Add Backdated Analysis"),
                                    Choice("executed_trades_repair", name="[3] Repair Executed Trades"),
                                    Choice("back", name="[4] Back to Main Menu")
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
                            elif config_choice == "add_backdated_analysis":
                                try:
                                    backdated_ts = get_mandatory_datetime("Enter Target Timestamp")
                                    flow_new_analysis(backdated_timestamp=backdated_ts)
                                except GoBackException:
                                    console.print("[warning]Operation cancelled.[/warning]")
                                except PauseAuditException:
                                    console.print("[warning]Operation paused/cancelled.[/warning]")
                                except Exception as e:
                                    console.print(f"[bold red]Error adding backdated analysis: {e}[/bold red]")
                                input("Press Enter to continue...")
                            elif config_choice == "executed_trades_repair":
                                flow_executed_trades_repair()
                        elif selected_choice == "t":
                            flow_test_drive()
                        elif selected_choice == "e":
                            ACTIVE_SESSION = None
                            state.active_session = None
                            from tools.database import init_db
                            init_db("sqlite:///.data/journal.db")
                            console.print("[yellow]Exiting Test Flight and restoring main database connection...[/yellow]")
                            import time
                            time.sleep(1)
                        elif selected_choice == "x":
                            console.print("[yellow]Exiting B.L.A.S.T...[/yellow]")
                            break
                    finally:
                        state.active_session = ACTIVE_SESSION
                        state.refresh_metrics()
                        
                        layout = build_persistent_layout(state)
                        layout["body"].update(build_welcome_body(state))
                        live.update(layout)
                        
                        if selected_choice != "x":
                            live.start()
                            live.refresh()
                        else:
                            break
        except Exception:
            raise
        finally:
            live.stop()

def format_percentage(value):
    return f"{value * 100:.3f}%"

def flow_review_analysis():
    from tools.database import engine_default, UnifiedDepartment, AnalysisLayer
    from sqlalchemy.orm import Session
    from sqlalchemy import select, desc
    
    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        console.rule("[bold cyan]Recent Analyses Index (Last 5)[/bold cyan]")
        console.print()
        
        with Session(engine_default) as db_session:
            stmt = select(UnifiedDepartment).order_by(desc(UnifiedDepartment.created_at)).limit(5)
            records = db_session.scalars(stmt).all()
            
            if not records:
                console.print("[yellow]No analyses found in the database. Please perform a new analysis first![/yellow]\n")
                input("Press Enter to return to main menu...")
                break
                
            table = Table(box=box.ROUNDED, border_style="magenta", expand=True)
            table.add_column("#", justify="center", style="dim")
            table.add_column("Short ID", justify="center", style="cyan")
            table.add_column("Asset", justify="center", style="bold white")
            table.add_column("Market Bias", justify="center")
            table.add_column("Calc Edge", justify="right")
            table.add_column("Created At", justify="center", style="dim")
            
            for idx, r in enumerate(records):
                bias_style = "bold green" if r.market_bias == "Bullish" else "bold red" if r.market_bias == "Bearish" else "bold yellow"
                bias_text = f"[{bias_style}]{r.market_bias}[/{bias_style}]"
                
                edge_style = "bold green" if r.calc_edge >= 0.26 else "bold red" if r.calc_edge <= -0.26 else "bold yellow"
                edge_text = f"[{edge_style}]{r.calc_edge:.4f}[/{edge_style}]"
                
                table.add_row(
                    str(idx + 1),
                    r.id[:8],
                    r.asset,
                    bias_text,
                    edge_text,
                    r.created_at.strftime("%Y-%m-%d %H:%M")
                )
                
            console.print(table)
            console.print()
            
            choices = [
                Choice(r.id, name=f"[{idx+1}] {r.created_at.strftime('%m/%d %H:%M')} | {r.asset} (Bias: {r.market_bias}, Edge: {r.calc_edge:.4f})")
                for idx, r in enumerate(records)
            ]
            choices.append(Choice("back", name="[<] Back to Main Menu"))
            
            selected_id = inquirer.select(
                message="Select analysis to inspect in detail >",
                choices=choices,
                pointer=">",
                qmark="",
                keybindings={"skip": []}
            ).execute()
            
            if selected_id == "back":
                break
                
            # Render Detail View
            stmt_sel = select(UnifiedDepartment).where(UnifiedDepartment.id == selected_id)
            record = db_session.scalars(stmt_sel).first()
            if not record:
                console.print("[bold red]Analysis record not found![/bold red]")
                input("Press Enter to continue...")
                continue
                
            layers = record.analysis_layers
            p0 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P0'), None)
            p2 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P2'), None)
            p3 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P3'), None)
            
            p4 = next((l for l in layers if l.department == 'TACTICAL' and l.layer_name == 'P4'), None)
            p1 = next((l for l in layers if l.department == 'TACTICAL' and l.layer_name == 'P1'), None)
            
            # Structural Vector panel
            struct_text = Text()
            struct_text.append("Market Bias: ", style="dim")
            bias_style = "bold green" if record.market_bias == "Bullish" else "bold red" if record.market_bias == "Bearish" else "bold yellow"
            struct_text.append(f"{record.market_bias}\n\n", style=bias_style)
            
            for label, p_layer in [("P0", p0), ("P2", p2), ("P3", p3)]:
                struct_text.append(f"{label}: ", style="bold cyan")
                if p_layer:
                    d_style = "green" if p_layer.direction == "Long" else "red" if p_layer.direction == "Short" else "yellow"
                    struct_text.append(f"{p_layer.direction.upper()}", style=d_style)
                    struct_text.append(" | ", style="dim")
                    s_style = "bold" if p_layer.strength == "Strong" else ""
                    struct_text.append(f"{p_layer.strength.upper()}\n", style=s_style)
                    if p_layer.thesis:
                        indented_thesis = format_indented_block(p_layer.thesis, indent_spaces=11, wrap_width=38)
                        struct_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
                else:
                    struct_text.append("N/A\n", style="dim")
                    
            # Tactical Vector panel
            tact_text = Text()
            tact_text.append("Hierarchy: ", style="dim")
            tact_text.append(f"{record.p4_hierarchy}\n", style="bold white")
            tact_text.append("Timeframe: ", style="dim")
            tact_text.append(f"{record.p1_timeframe}\n", style="bold white")
            tact_text.append("Fractal Type: ", style="dim")
            tact_text.append(f"{record.p1_type}\n", style="bold white")
            tact_text.append("Nodes L1/L2: ", style="dim")
            tact_text.append(f"{record.nodes_l1} / {record.nodes_l2}\n\n", style="bold white")
            
            for label, p_layer in [("P4", p4), ("P1", p1)]:
                tact_text.append(f"{label}: ", style="bold magenta")
                if p_layer:
                    d_style = "green" if p_layer.direction == "Long" else "red" if p_layer.direction == "Short" else "yellow"
                    tact_text.append(f"{p_layer.direction.upper()}", style=d_style)
                    tact_text.append(" | ", style="dim")
                    s_style = "bold" if p_layer.strength == "Strong" else ""
                    tact_text.append(f"{p_layer.strength.upper()}\n", style=s_style)
                    if p_layer.thesis:
                        indented_thesis = format_indented_block(p_layer.thesis, indent_spaces=11, wrap_width=38)
                        tact_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
                else:
                    tact_text.append("N/A\n", style="dim")
                    
            # Quantitative panel
            quant_text = Text()
            quant_text.append("I_CD (Edge): ", style="dim")
            edge_style = "bold green" if record.calc_edge >= 0.26 else "bold red" if record.calc_edge <= -0.26 else "bold yellow"
            quant_text.append(f"{record.calc_edge:.4f}\n\n", style=edge_style)
            
            quant_text.append("Long Prob: ", style="dim")
            quant_text.append(f"{record.long_prob * 100:.1f}%\n", style="green")
            quant_text.append("Short Prob: ", style="dim")
            quant_text.append(f"{record.short_prob * 100:.1f}%\n", style="red")
            quant_text.append("No-Trade Prob: ", style="dim")
            quant_text.append(f"{record.no_trade_prob * 100:.1f}%\n\n", style="yellow")
            
            quant_text.append("Tactical Classification:\n", style="dim")
            quant_text.append(f"  {record.tactical_classification}\n", style="bold cyan")
            
            # State & Meta panel
            meta_text = Text()
            meta_text.append("Full ID: ", style="dim")
            meta_text.append(f"{record.id}\n", style="cyan")
            meta_text.append("Short ID: ", style="dim")
            meta_text.append(f"{record.id[:8]}\n", style="bold cyan")
            meta_text.append("Created: ", style="dim")
            meta_text.append(f"{record.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n", style="white")
            meta_text.append("Updated: ", style="dim")
            meta_text.append(f"{record.updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n", style="white")
            meta_text.append("Lifecycle State:\n", style="dim")
            state_style = "bold green" if record.state in ["COMPLETED", "SYNCED"] else "bold yellow"
            meta_text.append(f"  {record.state}\n", style=state_style)
            if record.edge_description:
                meta_text.append(f"\nEdge Description:\n", style="dim")
                meta_text.append(f"  {record.edge_description}\n", style="italic white")
                
            p_struct = Panel(struct_text, title="[Structural Vector (Eff)]", border_style="cyan", box=box.ROUNDED)
            p_tact = Panel(tact_text, title="[Tactical Vector (Exec)]", border_style="magenta", box=box.ROUNDED)
            p_quant = Panel(quant_text, title="[Quantitative Profile]", border_style="green", box=box.ROUNDED)
            p_meta = Panel(meta_text, title="[State & Meta]", border_style="yellow", box=box.ROUNDED)
            
            grid = Table.grid(expand=True)
            grid.add_column(ratio=1)
            grid.add_column(ratio=1)
            grid.add_row(p_struct, p_tact)
            grid.add_row(p_quant, p_meta)
            
            dashboard = Panel(
                grid,
                title=f"[white]Unified Analysis Dashboard: {record.asset} - {record.created_at.strftime('%m/%d %H:%M')}[/white]",
                border_style="bold blue",
                box=box.DOUBLE
            )
            
            try:
                console.clear(home=True)
            except TypeError:
                console.clear()
                
            console.print(dashboard)
            console.print("\n[dim]Press Enter to return to Recent Analyses Index...[/dim]")
def flow_new_analysis(backdated_timestamp=None):
    trade_id = str(uuid.uuid4())
    console.print(f"\n[muted]Initialized new unified trade context: {trade_id}[/muted]")
    
    session = AnalysisSession(trade_id)
    while True:
        try:
            # --- STEP 1: Structure Parameters ---
            def prompt_asset():
                available_assets = get_assets()
                if available_assets:
                    choices = [Choice(a, name=a) for a in available_assets]
                    choices.append(Choice("CUSTOM", name="[Add Custom Asset]"))
                    
                    asset_choice = bind_pause(inquirer.select(
                        message="Select Asset >",
                        choices=choices,
                        pointer=">",
                        qmark="",
                        keybindings={"skip": []}
                    )).execute()
                    
                    if asset_choice == "CUSTOM":
                        asset = get_mandatory_text("Enter Asset (e.g., BTC/USDT)")
                        add_asset(asset, category="Crypto")
                        return asset
                    else:
                        return asset_choice
                else:
                    asset = get_mandatory_text("Enter Asset (e.g., BTC/USDT)")
                    add_asset(asset, category="Crypto")
                    return asset

            asset = session.prompt("asset", prompt_asset)
            
            # Formatted String Template for Macro Vector Analysis
            p0_template = (
                "1DT-B\n"
                "12HT-B\n"
                "4HT-B\n"
                "1HT-B"
            )
            
            p0_thesis = session.prompt("p0_thesis", get_mandatory_text, "P0 Thesis", multiline=True, default=p0_template)
            p0_dir = session.prompt("p0_dir", get_enum_choice, "P0 Direction", Direction)
            p0_str = session.prompt("p0_str", get_enum_choice, "P0 Strength", Strength)
            
            p2_template = (
                "20EMA: \n"
                "200EMA: \n"
                "EMA: \n"
                "ADX: \n"
                "ATR: \n"
                "RSI: \n"
                "DIVERGENCE + TIMEFRAME:"
            )
            
            p2_thesis = session.prompt("p2_thesis", get_mandatory_text, "P2 Thesis", multiline=True, default=p2_template)
            p2_dir = session.prompt("p2_dir", get_enum_choice, "P2 Direction", Direction)
            p2_str = session.prompt("p2_str", get_enum_choice, "P2 Strength", Strength)
            
            p3_thesis = session.prompt("p3_thesis", get_mandatory_text, "P3 Thesis", multiline=True)
            p3_dir = session.prompt("p3_dir", get_enum_choice, "P3 Direction", Direction)
            p3_str = session.prompt("p3_str", get_enum_choice, "P3 Strength", Strength)

            # --- STEP 2: Tactical & Meta Parameters ---
            p1_thesis = session.prompt("p1_thesis", get_mandatory_text, "P1 Thesis", multiline=True)
            p1_dir = session.prompt("p1_dir", get_enum_choice, "P1 Direction", Direction)
            p1_str = session.prompt("p1_str", get_enum_choice, "P1 Strength", Strength)
            p1_tf = session.prompt("p1_tf", get_enum_choice, "P1 Timeframe", Timeframe)
            p1_type = session.prompt("p1_type", get_enum_choice, "P1 Fractal Type", FractalType)
            nodes_l1 = session.prompt("nodes_l1", get_mandatory_int, "Nodes L1")
            nodes_l2 = session.prompt("nodes_l2", get_mandatory_int, "Nodes L2")
            
            p4_thesis = session.prompt("p4_thesis", get_mandatory_text, "P4 Thesis", multiline=True)
            p4_dir = session.prompt("p4_dir", get_enum_choice, "P4 Direction", Direction)
            p4_str = session.prompt("p4_str", get_enum_choice, "P4 Strength", Strength)
            p4_hier = session.prompt("p4_hier", get_enum_choice, "P4 Hierarchy", Hierarchy)

            edge_desc = session.prompt("edge_desc", get_mandatory_text, "Efficiency Edge Description", multiline=True)
            bias_a = session.prompt("bias_a", get_enum_choice, "Initial Structural Bias (Bias A)", StructuralBias)
            tact_class = session.prompt("tact_class", get_enum_choice, "Tactical Classification", TacticalClassification)

            # --- STEP 3: Calculations, Review & Post-Input Editing ---
            while True:
                # Retrieve current state values (handles mutations from inline editor)
                asset = session.state["asset"]
                p0_thesis = session.state["p0_thesis"]
                p0_dir = session.state["p0_dir"]
                p0_str = session.state["p0_str"]
                
                p1_thesis = session.state["p1_thesis"]
                p1_dir = session.state["p1_dir"]
                p1_str = session.state["p1_str"]
                p1_tf = session.state["p1_tf"]
                p1_type = session.state["p1_type"]
                nodes_l1 = session.state["nodes_l1"]
                nodes_l2 = session.state["nodes_l2"]
                
                p2_thesis = session.state["p2_thesis"]
                p2_dir = session.state["p2_dir"]
                p2_str = session.state["p2_str"]
                
                p3_thesis = session.state["p3_thesis"]
                p3_dir = session.state["p3_dir"]
                p3_str = session.state["p3_str"]
                
                p4_thesis = session.state["p4_thesis"]
                p4_dir = session.state["p4_dir"]
                p4_str = session.state["p4_str"]
                p4_hier = session.state["p4_hier"]
                
                edge_desc = session.state["edge_desc"]
                bias_a = session.state["bias_a"]
                tact_class = session.state["tact_class"]

                # Perform calculations
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
                elif i_cd >= 0.26:
                    market_bias = "Bullish"
                else:
                    market_bias = "Bearish"

                # Validation & Instantiation
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
                    console.print(f"[danger]Validation Error: {e}[/danger]")
                    input("Press Enter to continue to edit or discard...")
                    # Fall back to editing directly
                    field_to_edit = inquirer.select(
                        message="Please select invalid field to fix >",
                        choices=[Choice("tact_class", name="Tactical Classification"), Choice("discard", name="Discard")],
                        pointer=">",
                        qmark=""
                    ).execute()
                    if field_to_edit == "discard":
                        raise PauseAuditException("Discard requested")
                    else:
                        new_val = get_enum_choice("Edit Tactical Classification", TacticalClassification)
                        session.state["tact_class"] = new_val
                        continue

                # Build Review dashboard
                struct_text = Text()
                struct_text.append("Market Bias: ", style="dim")
                bias_style = "success" if market_bias == "Bullish" else "danger" if market_bias == "Bearish" else "warning"
                struct_text.append(f"{market_bias}\n\n", style=bias_style)
                
                for label, d, s, t in [("P0", p0_dir, p0_str, p0_thesis), ("P2", p2_dir, p2_str, p2_thesis), ("P3", p3_dir, p3_str, p3_thesis)]:
                    struct_text.append(f"{label}: ", style="bold primary")
                    if d and s:
                        d_style = "success" if d.value == "Long" else "danger" if d.value == "Short" else "warning"
                        struct_text.append(f"{d.value.upper()}", style=d_style)
                        struct_text.append(" | ", style="dim")
                        s_style = "bold" if s.value == "Strong" else ""
                        struct_text.append(f"{s.value.upper()}\n", style=s_style)
                        if t:
                            indented_thesis = format_indented_block(t, indent_spaces=11, wrap_width=38)
                            struct_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
                    else:
                        struct_text.append("N/A\n", style="dim")
                        
                # Tactical Vector panel
                tact_text = Text()
                tact_text.append("Hierarchy: ", style="dim")
                tact_text.append(f"{p4_hier.value}\n", style="bold white")
                tact_text.append("Timeframe: ", style="dim")
                tact_text.append(f"{p1_tf.value}\n", style="bold white")
                tact_text.append("Fractal Type: ", style="dim")
                tact_text.append(f"{p1_type.value}\n", style="bold white")
                tact_text.append("Nodes L1/L2: ", style="dim")
                tact_text.append(f"{nodes_l1} / {nodes_l2}\n\n", style="bold white")
                
                for label, d, s, t in [("P4", p4_dir, p4_str, p4_thesis), ("P1", p1_dir, p1_str, p1_thesis)]:
                    tact_text.append(f"{label}: ", style="bold secondary")
                    if d and s:
                        d_style = "success" if d.value == "Long" else "danger" if d.value == "Short" else "warning"
                        tact_text.append(f"{d.value.upper()}", style=d_style)
                        tact_text.append(" | ", style="dim")
                        s_style = "bold" if s.value == "Strong" else ""
                        tact_text.append(f"{s.value.upper()}\n", style=s_style)
                        if t:
                            indented_thesis = format_indented_block(t, indent_spaces=11, wrap_width=38)
                            tact_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
                    else:
                        tact_text.append("N/A\n", style="dim")
                        
                # Quantitative panel
                quant_text = Text()
                quant_text.append("I_CD (Edge): ", style="dim")
                edge_style = "success" if i_cd >= 0.26 else "danger" if i_cd <= -0.26 else "warning"
                quant_text.append(f"{i_cd:.4f}\n\n", style=edge_style)
                
                quant_text.append("Long Prob: ", style="dim")
                quant_text.append(f"{tactical.long_prob * 100:.1f}%\n", style="success")
                quant_text.append("Short Prob: ", style="dim")
                quant_text.append(f"{tactical.short_prob * 100:.1f}%\n", style="danger")
                quant_text.append("No-Trade Prob: ", style="dim")
                quant_text.append(f"{tactical.no_trade_prob * 100:.1f}%\n\n", style="warning")
                
                quant_text.append("Tactical Classification:\n", style="dim")
                quant_text.append(f"  {tactical.tactical_classification.value}\n", style="bold primary")
                
                # State & Meta panel
                meta_text = Text()
                meta_text.append("Full ID: ", style="dim")
                meta_text.append(f"{trade_id}\n", style="primary")
                meta_text.append("Short ID: ", style="dim")
                meta_text.append(f"{trade_id[:8]}\n", style="bold primary")
                meta_text.append("Created: ", style="dim")
                display_ts = backdated_timestamp if backdated_timestamp else datetime.datetime.now()
                meta_text.append(f"{display_ts.strftime('%Y-%m-%d %H:%M:%S')}\n", style="white")
                meta_text.append("Lifecycle State:\n", style="dim")
                meta_text.append(f"  {LifecycleState.PENDING_AUDITS.value}\n", style="bold warning")
                if edge_desc:
                    meta_text.append(f"\nEdge Description:\n", style="dim")
                    meta_text.append(f"  {edge_desc}\n", style="italic white")
                    
                p_struct = Panel(struct_text, title="[Structural Vector (Eff)]", border_style="cyan", box=box.ROUNDED)
                p_tact = Panel(tact_text, title="[Tactical Vector (Exec)]", border_style="magenta", box=box.ROUNDED)
                p_quant = Panel(quant_text, title="[Quantitative Profile]", border_style="green", box=box.ROUNDED)
                p_meta = Panel(meta_text, title="[State & Meta]", border_style="yellow", box=box.ROUNDED)
                
                grid = Table.grid(expand=True)
                grid.add_column(ratio=1)
                grid.add_column(ratio=1)
                grid.add_row(p_struct, p_tact)
                grid.add_row(p_quant, p_meta)
                
                dashboard = Panel(
                    grid,
                    title=f"[white]Step 3/3: Review - Unified Analysis Dashboard: {asset}[/white]",
                    border_style="bold cyan",
                    box=box.DOUBLE
                )
                
                try:
                    console.clear(home=True)
                except TypeError:
                    console.clear()
                    
                console.print(dashboard)
                console.print()

                action_choice = inquirer.select(
                    message="Review Action >",
                    choices=[
                        Choice("save", name="[1] Confirm & Save"),
                        Choice("edit", name="[2] Edit a Field"),
                        Choice("discard", name="[3] Discard")
                    ],
                    pointer=">",
                    qmark=""
                ).execute()

                if action_choice == "save":
                    from tools.database import engine_default, UnifiedDepartment, AnalysisLayer, EfficiencyAudit as ModelEfficiencyAudit
                    from sqlalchemy.orm import Session
                    
                    with Session(engine_default) as db_session:
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
                            if backdated_timestamp:
                                new_record.created_at = backdated_timestamp
                                new_record.updated_at = backdated_timestamp
                            db_session.add(new_record)

                            new_ea = ModelEfficiencyAudit(id=trade_id, bias_a=bias_a.value)
                            if backdated_timestamp:
                                new_ea.created_at = backdated_timestamp
                                new_ea.updated_at = backdated_timestamp
                            db_session.add(new_ea)

                            for layer_dict in efficiency.to_db_layers():
                                al = AnalysisLayer(
                                    trade_id=trade_id, department='EFFICIENCY', **layer_dict.model_dump()
                                )
                                db_session.add(al)

                            for layer_dict in tactical.to_db_layers():
                                al = AnalysisLayer(
                                    trade_id=trade_id, department='TACTICAL', **layer_dict.model_dump()
                                )
                                db_session.add(al)

                            db_session.commit()
                            console.print("[success]Unified Analysis Saved. Transitioned directly to PENDING_AUDITS.[/success]")
                        except Exception as e:
                            db_session.rollback()
                            console.print(f"[danger]Transaction rolled back due to error: {e}[/danger]")
                            input("Press Enter to continue...")
                            return
                    
                    input("Press Enter to continue...")
                    return

                elif action_choice == "discard":
                    raise PauseAuditException("Discard requested")

                elif action_choice == "edit":
                    edit_choices = [
                        Choice("asset", name=f"Asset: {asset}"),
                        Choice("p0_thesis", name=f"P0 Thesis: {p0_thesis[:30]}..."),
                        Choice("p0_dir", name=f"P0 Direction: {p0_dir.value}"),
                        Choice("p0_str", name=f"P0 Strength: {p0_str.value}"),
                        Choice("p1_thesis", name=f"P1 Thesis: {p1_thesis[:30]}..."),
                        Choice("p1_dir", name=f"P1 Direction: {p1_dir.value}"),
                        Choice("p1_str", name=f"P1 Strength: {p1_str.value}"),
                        Choice("p1_tf", name=f"P1 Timeframe: {p1_tf.value}"),
                        Choice("p1_type", name=f"P1 Fractal Type: {p1_type.value}"),
                        Choice("nodes_l1", name=f"Nodes L1: {nodes_l1}"),
                        Choice("nodes_l2", name=f"Nodes L2: {nodes_l2}"),
                        Choice("p2_thesis", name=f"P2 Thesis: {p2_thesis[:30]}..."),
                        Choice("p2_dir", name=f"P2 Direction: {p2_dir.value}"),
                        Choice("p2_str", name=f"P2 Strength: {p2_str.value}"),
                        Choice("p3_thesis", name=f"P3 Thesis: {p3_thesis[:30]}..."),
                        Choice("p3_dir", name=f"P3 Direction: {p3_dir.value}"),
                        Choice("p3_str", name=f"P3 Strength: {p3_str.value}"),
                        Choice("p4_thesis", name=f"P4 Thesis: {p4_thesis[:30]}..."),
                        Choice("p4_dir", name=f"P4 Direction: {p4_dir.value}"),
                        Choice("p4_str", name=f"P4 Strength: {p4_str.value}"),
                        Choice("p4_hier", name=f"P4 Hierarchy: {p4_hier.value}"),
                        Choice("edge_desc", name=f"Edge Description: {edge_desc[:30]}..."),
                        Choice("bias_a", name=f"Bias A: {bias_a.value}"),
                        Choice("tact_class", name=f"Tactical Classification: {tact_class.value}"),
                        Choice("back", name="[<] Back to Review")
                    ]
                    
                    field_to_edit = inquirer.select(
                        message="Select Field to Edit >",
                        choices=edit_choices,
                        pointer=">",
                        qmark=""
                    ).execute()
                    
                    if field_to_edit == "back":
                        continue
                    
                    try:
                        if field_to_edit == "asset":
                            available_assets = get_assets()
                            choices = [Choice(a, name=a) for a in available_assets]
                            choices.append(Choice("CUSTOM", name="[Add Custom Asset]"))
                            asset_choice = inquirer.select(
                                message="Select Asset >",
                                choices=choices,
                                pointer=">",
                                qmark=""
                            ).execute()
                            if asset_choice == "CUSTOM":
                                new_val = get_mandatory_text("Enter Asset (e.g., BTC/USDT)")
                                add_asset(new_val, category="Crypto")
                            else:
                                new_val = asset_choice
                        elif field_to_edit in ["p0_thesis", "p1_thesis", "p2_thesis", "p3_thesis", "p4_thesis"]:
                            new_val = get_mandatory_text(f"Edit {field_to_edit.replace('_', ' ').title()}", multiline=True)
                        elif field_to_edit in ["p0_dir", "p1_dir", "p2_dir", "p3_dir", "p4_dir"]:
                            new_val = get_enum_choice(f"Edit {field_to_edit.replace('_', ' ').title()}", Direction)
                        elif field_to_edit in ["p0_str", "p1_str", "p2_str", "p3_str", "p4_str"]:
                            new_val = get_enum_choice(f"Edit {field_to_edit.replace('_', ' ').title()}", Strength)
                        elif field_to_edit == "p1_tf":
                            new_val = get_enum_choice("Edit P1 Timeframe", Timeframe)
                        elif field_to_edit == "p1_type":
                            new_val = get_enum_choice("Edit P1 Fractal Type", FractalType)
                        elif field_to_edit in ["nodes_l1", "nodes_l2"]:
                            new_val = get_mandatory_int(f"Edit {field_to_edit.upper()}")
                        elif field_to_edit == "p4_hier":
                            new_val = get_enum_choice("Edit P4 Hierarchy", Hierarchy)
                        elif field_to_edit == "edge_desc":
                            new_val = get_mandatory_text("Edit Edge Description", multiline=True)
                        elif field_to_edit == "bias_a":
                            new_val = get_enum_choice("Edit Bias A", StructuralBias)
                        elif field_to_edit == "tact_class":
                            new_val = get_enum_choice("Edit Tactical Classification", TacticalClassification)
                        
                        session.state[field_to_edit] = new_val
                    except GoBackException:
                        console.print("[warning]Edit cancelled.[/warning]")
                        continue
                    except PauseAuditException:
                        console.print("[warning]Edit paused.[/warning]")
                        continue
        except RestartFlowException:
            continue
        except PauseAuditException:
            console.print("\n[warning]Analysis Cancelled / Paused.[/warning]")
            input("Press Enter to continue...")
            return

def flow_pending_audits():
    records = get_records_by_state(LifecycleState.PENDING_AUDITS)
    if not records:
        console.print("[warning]No pending audits.[/warning]")
        input("Press Enter to continue...")
        return
        
    sync_status = check_daemon_status()
    layout = build_persistent_layout(
        active_session_name=ACTIVE_SESSION["name"] if ACTIVE_SESSION else None,
        pending_count=len(records),
        sync_status=sync_status
    )
    layout["body"].update(render_pending_audits_table(records))
    
    try:
        console.clear(home=True)
    except TypeError:
        console.clear()
        
    console.print(layout)
    console.print()
    
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
        edge_desc_val = payload.get("edge_description")
        if edge_desc_val:
            console.print(f"[bold cyan]Edge Description:[/bold cyan] {edge_desc_val}")
        
        try:
            bias_a = StructuralBias(bias_a_val)
        except ValueError:
            bias_a = StructuralBias.NO_BIAS_CHOPPY

        session = AuditSession(trade_id, "eff")
        while True:
            try:
                real_bias_b = session.state.get("real_bias_b") or session.prompt("real_bias_b", get_enum_choice, "Real Bias B", StructuralBias)
                res_type = session.state.get("res_type") or session.prompt("res_type", get_enum_choice, "Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN])
                struct_res = session.state.get("struct_res") or session.prompt("struct_res", get_enum_choice, "Structural Resolution", StructuralResolution)
                fail_reason = session.state.get("fail_reason") or session.prompt("fail_reason", get_enum_choice, "Failure Reason", FailureReason)
                lesson_eff = session.state.get("lesson_eff") if "lesson_eff" in session.state else session.prompt("lesson_eff", get_optional_text, "Efficiency Lesson Learned")

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
                
                # Review Panel
                rev_text = Text()
                rev_text.append(f"Original Bias (Bias A): {bias_a.value}\n")
                rev_text.append(f"Real Bias B: {real_bias_b.value}\n")
                rev_text.append(f"Resolution Type: {res_type.value}\n")
                rev_text.append(f"Structural Resolution: {struct_res.value}\n")
                rev_text.append(f"Failure Reason: {fail_reason.value}\n")
                rev_text.append(f"Lesson Learned: {lesson_eff or ''}\n")
                
                try:
                    console.clear(home=True)
                except TypeError:
                    console.clear()
                console.print(Panel(rev_text, title="Review: Efficiency Audit Staging Payload", border_style="cyan"))
                
                action_choice = inquirer.select(
                    message="Review Action >",
                    choices=[
                        Choice("save", name="[1] Confirm & Save"),
                        Choice("edit", name="[2] Edit a Field"),
                        Choice("discard", name="[3] Discard")
                    ],
                    pointer=">",
                    qmark=""
                ).execute()

                if action_choice == "save":
                    new_payload["audit_efficiency"] = audit_eff.model_dump()
                    console.print("[green]Efficiency Audit saved.[/green]")
                    session.clear_state()
                    break
                elif action_choice == "discard":
                    raise PauseAuditException("Discard requested")
                elif action_choice == "edit":
                    edit_choices = [
                        Choice("real_bias_b", name=f"Real Bias B: {real_bias_b.value}"),
                        Choice("res_type", name=f"Resolution Type: {res_type.value}"),
                        Choice("struct_res", name=f"Structural Resolution: {struct_res.value}"),
                        Choice("fail_reason", name=f"Failure Reason: {fail_reason.value}"),
                        Choice("lesson_eff", name=f"Lesson Learned: {lesson_eff or ''}"),
                        Choice("back", name="[<] Back to Review")
                    ]
                    field_to_edit = inquirer.select(
                        message="Select Field to Edit >",
                        choices=edit_choices,
                        pointer=">",
                        qmark=""
                    ).execute()
                    if field_to_edit == "back":
                        continue
                    if field_to_edit == "real_bias_b":
                        session.state["real_bias_b"] = get_enum_choice("Edit Real Bias B", StructuralBias)
                    elif field_to_edit == "res_type":
                        session.state["res_type"] = get_enum_choice("Edit Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN])
                    elif field_to_edit == "struct_res":
                        session.state["struct_res"] = get_enum_choice("Edit Structural Resolution", StructuralResolution)
                    elif field_to_edit == "fail_reason":
                        session.state["fail_reason"] = get_enum_choice("Edit Failure Reason", FailureReason)
                    elif field_to_edit == "lesson_eff":
                        session.state["lesson_eff"] = get_optional_text("Edit Efficiency Lesson Learned")
            except RestartFlowException:
                continue
            except PauseAuditException:
                console.print("\n[bold yellow]Audit Paused. Progress saved.[/bold yellow]")
                input("Press Enter to continue...")
                return

    elif audit_choice == "tac":
        console.print(Panel("Tactical Audit", style="bold magenta"))
        session = AuditSession(trade_id, "tac")
        while True:
            try:
                t_status = session.prompt("t_status", get_enum_choice, "Trade Status", TradeStatus)
                
                if t_status == TradeStatus.NO_TAKEN:
                    while True:
                        t_comp = session.state.get("t_comp") or session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                        audit_tactical = TacticalAudit(
                            tactical_id=trade_id,
                            trade_status=t_status,
                            compliance=t_comp
                        )
                        
                        rev_text = Text()
                        rev_text.append(f"Trade Status: {t_status.value}\n")
                        rev_text.append(f"Compliance State: {t_comp.value}\n")
                        
                        try:
                            console.clear(home=True)
                        except TypeError:
                            console.clear()
                        console.print(Panel(rev_text, title="Review: Tactical Audit (No Trade Taken)", border_style="cyan"))
                        
                        action_choice = inquirer.select(
                            message="Review Action >",
                            choices=[
                                Choice("save", name="[1] Confirm & Save"),
                                Choice("edit", name="[2] Edit a Field"),
                                Choice("discard", name="[3] Discard")
                            ],
                            pointer=">",
                            qmark=""
                        ).execute()

                        if action_choice == "save":
                            new_payload["trade_status"] = t_status.value
                            new_payload["audit_tactical"] = audit_tactical.model_dump()
                            console.print("[green]Tactical Audit saved (No Trade Taken).[/green]")
                            session.clear_state()
                            break
                        elif action_choice == "discard":
                            raise PauseAuditException("Discard requested")
                        elif action_choice == "edit":
                            session.state["t_comp"] = get_enum_choice("Edit Compliance State", ComplianceState)
                    break
                else:
                    while True:
                        htf_trend = session.state.get("htf_trend") or session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                        ltf_trend = session.state.get("ltf_trend") or session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)
                        sl = session.state.get("sl") if "sl" in session.state else session.prompt("sl", get_mandatory_float, "Stop Loss")
                        entry_p = session.state.get("entry_p") if "entry_p" in session.state else session.prompt("entry_p", get_mandatory_float, "Entry Price")
                        conf_params = session.state.get("conf_params") or session.prompt("conf_params", get_multi_enum_choice, "Confirmation Params", ConfirmationParams)
                        size = session.state.get("size") if "size" in session.state else session.prompt("size", get_mandatory_float, "Size")
                        tp = session.state.get("tp") if "tp" in session.state else session.prompt("tp", get_mandatory_float, "Take Profit")
                        entry_time = session.state.get("entry_time") or session.prompt("entry_time", get_mandatory_datetime, "Entry Time")
                        emotions = session.state.get("emotions") or session.prompt("emotions", get_multi_enum_choice, "Emotions", Emotions)
                        pre_trade_emotions = session.state.get("pre_trade_emotions") if "pre_trade_emotions" in session.state else session.prompt("pre_trade_emotions", get_mandatory_text, "Pre Trade Emotions")
                        p_emotion = session.state.get("p_emotion") or session.prompt("p_emotion", get_enum_choice, "Primary Emotion", PrimaryEmotion)
                        mental_clarity = session.state.get("mental_clarity") if "mental_clarity" in session.state else session.prompt("mental_clarity", get_mandatory_int, "Mental Clarity Level", 1, 5)
                        impatience = session.state.get("impatience") if "impatience" in session.state else session.prompt("impatience", get_mandatory_int, "Impatience Level", 1, 5)
                        anxiety = session.state.get("anxiety") if "anxiety" in session.state else session.prompt("anxiety", get_mandatory_int, "Anxiety Level", 1, 5)
                        mid_trade_emotions = session.state.get("mid_trade_emotions") if "mid_trade_emotions" in session.state else session.prompt("mid_trade_emotions", get_mandatory_text, "Mid Trade Emotions")
                        post_trade_emotions = session.state.get("post_trade_emotions") if "post_trade_emotions" in session.state else session.prompt("post_trade_emotions", get_mandatory_text, "Post Trade Emotions")
                        exit_time = session.state.get("exit_time") or session.prompt("exit_time", get_mandatory_datetime, "Exit Time")
                        exit_type = session.state.get("exit_type") or session.prompt("exit_type", get_enum_choice, "Exit Type", ExitType)
                        conf_status = session.state.get("conf_status") or session.prompt("conf_status", get_enum_choice, "Confirmation Status", ConfirmationStatus)
                        close_p = session.state.get("close_p") if "close_p" in session.state else session.prompt("close_p", get_mandatory_float, "Closing Price")
                        
                        def ask_could_hit_tp():
                            return bind_pause(inquirer.select(
                                message="Could hit TP? >",
                                choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                pointer=">",
                                qmark="",
                                keybindings={"skip": []}
                            )).execute()
                            
                        could_hit_tp = session.state.get("could_hit_tp") or session.prompt("could_hit_tp", ask_could_hit_tp)
                        t_comp = session.state.get("t_comp") or session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                        tier_setup = session.state.get("tier_setup") or session.prompt("tier_setup", get_enum_choice, "Tier Setup", TierSetup)
                        market_state = session.state.get("market_state") or session.prompt("market_state", get_enum_choice, "Market State", MarketState)
                        f_plan = session.state.get("f_plan") or session.prompt("f_plan", get_enum_choice, "Followed Plan", FollowedPlan)
                        setup_t = session.state.get("setup_t") or session.prompt("setup_t", get_enum_choice, "Setup Type", SetupType)
                        behav_errors = session.state.get("behav_errors") or session.prompt("behav_errors", get_multi_enum_choice, "Behavioral Errors", BehavioralErrors)
                        cog_patterns = session.state.get("cog_patterns") or session.prompt("cog_patterns", get_multi_enum_choice, "Cognitive Patterns", CognitivePatterns)
                        mae = session.state.get("mae") if "mae" in session.state else session.prompt("mae", get_mandatory_float, "MAE (0 <= MAE <= 10)", min_val=0, max_val=10)
                        mfe = session.state.get("mfe") if "mfe" in session.state else session.prompt("mfe", get_mandatory_float, "MFE (0 <= MFE <= 10)", min_val=0, max_val=10)
                        lesson_tact = session.state.get("lesson_tact") if "lesson_tact" in session.state else session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)

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
                            cost=0.0,
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
                        if audit_tactical.notional_size is not None:
                            rev_text.append(f"Notional Size: ${audit_tactical.notional_size:.2f}\n")
                        if audit_tactical.capital_at_risk is not None:
                            rev_text.append(f"Capital At Risk: ${audit_tactical.capital_at_risk:.2f}\n")
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
                        
                        try:
                            console.clear(home=True)
                        except TypeError:
                            console.clear()
                        console.print(Panel(rev_text, title="Review: Tactical Audit Calculation", border_style="cyan"))
                        
                        action_choice = inquirer.select(
                            message="Review Action >",
                            choices=[
                                Choice("save", name="[1] Confirm & Save"),
                                Choice("edit", name="[2] Edit a Field"),
                                Choice("discard", name="[3] Discard")
                            ],
                            pointer=">",
                            qmark=""
                        ).execute()

                        if action_choice == "save":
                            new_payload["trade_status"] = t_status.value
                            new_payload["audit_tactical"] = audit_tactical.model_dump()
                            console.print("[green]Tactical Audit saved.[/green]")
                            session.clear_state()
                            break
                        elif action_choice == "discard":
                            raise PauseAuditException("Discard requested")
                        elif action_choice == "edit":
                            edit_choices = [
                                Choice("htf_trend", name=f"HTF Trend: {htf_trend.value}"),
                                Choice("ltf_trend", name=f"LTF Trend: {ltf_trend.value}"),
                                Choice("sl", name=f"Stop Loss: {sl}"),
                                Choice("entry_p", name=f"Entry Price: {entry_p}"),
                                Choice("size", name=f"Size: {size}"),
                                Choice("tp", name=f"Take Profit: {tp}"),
                                Choice("entry_time", name=f"Entry Time: {entry_time}"),
                                Choice("exit_time", name=f"Exit Time: {exit_time}"),
                                Choice("exit_type", name=f"Exit Type: {exit_type.value}"),
                                Choice("conf_status", name=f"Confirmation Status: {conf_status.value}"),
                                Choice("close_p", name=f"Closing Price: {close_p}"),
                                Choice("could_hit_tp", name=f"Could hit TP: {could_hit_tp}"),
                                Choice("t_comp", name=f"Compliance State: {t_comp.value}"),
                                Choice("tier_setup", name=f"Tier Setup: {tier_setup.value}"),
                                Choice("market_state", name=f"Market State: {market_state.value}"),
                                Choice("f_plan", name=f"Followed Plan: {f_plan.value}"),
                                Choice("setup_t", name=f"Setup Type: {setup_t.value}"),
                                Choice("mae", name=f"MAE: {mae}"),
                                Choice("mfe", name=f"MFE: {mfe}"),
                                Choice("lesson_tact", name=f"Lesson: {lesson_tact[:30]}..."),
                                Choice("back", name="[<] Back to Review")
                            ]
                            field_to_edit = inquirer.select(
                                message="Select Field to Edit >",
                                choices=edit_choices,
                                pointer=">",
                                qmark=""
                            ).execute()
                            if field_to_edit == "back":
                                continue
                            if field_to_edit == "htf_trend":
                                session.state["htf_trend"] = get_enum_choice("Edit HTF Trend Context", HTFTrendContext)
                            elif field_to_edit == "ltf_trend":
                                session.state["ltf_trend"] = get_enum_choice("Edit LTF Trend Context", TrendContext)
                            elif field_to_edit == "sl":
                                session.state["sl"] = get_mandatory_float("Edit Stop Loss")
                            elif field_to_edit == "entry_p":
                                session.state["entry_p"] = get_mandatory_float("Edit Entry Price")
                            elif field_to_edit == "size":
                                session.state["size"] = get_mandatory_float("Edit Size")
                            elif field_to_edit == "tp":
                                session.state["tp"] = get_mandatory_float("Edit Take Profit")
                            elif field_to_edit == "entry_time":
                                session.state["entry_time"] = get_mandatory_datetime("Edit Entry Time")
                            elif field_to_edit == "exit_time":
                                session.state["exit_time"] = get_mandatory_datetime("Edit Exit Time")
                            elif field_to_edit == "exit_type":
                                session.state["exit_type"] = get_enum_choice("Edit Exit Type", ExitType)
                            elif field_to_edit == "conf_status":
                                session.state["conf_status"] = get_enum_choice("Edit Confirmation Status", ConfirmationStatus)
                            elif field_to_edit == "close_p":
                                session.state["close_p"] = get_mandatory_float("Edit Closing Price")
                            elif field_to_edit == "could_hit_tp":
                                session.state["could_hit_tp"] = ask_could_hit_tp()
                            elif field_to_edit == "t_comp":
                                session.state["t_comp"] = get_enum_choice("Edit Compliance State", ComplianceState)
                            elif field_to_edit == "tier_setup":
                                session.state["tier_setup"] = get_enum_choice("Edit Tier Setup", TierSetup)
                            elif field_to_edit == "market_state":
                                session.state["market_state"] = get_enum_choice("Edit Market State", MarketState)
                            elif field_to_edit == "f_plan":
                                session.state["f_plan"] = get_enum_choice("Edit Followed Plan", FollowedPlan)
                            elif field_to_edit == "setup_t":
                                session.state["setup_t"] = get_enum_choice("Edit Setup Type", SetupType)
                            elif field_to_edit == "mae":
                                session.state["mae"] = get_mandatory_float("Edit MAE (0 <= MAE <= 10)", min_val=0, max_val=10)
                            elif field_to_edit == "mfe":
                                session.state["mfe"] = get_mandatory_float("Edit MFE (0 <= MFE <= 10)", min_val=0, max_val=10)
                            elif field_to_edit == "lesson_tact":
                                session.state["lesson_tact"] = get_mandatory_text("Edit Tactical Lesson Learned", multiline=True)
                    break
            except RestartFlowException:
                continue
            except PauseAuditException:
                console.print("\n[bold yellow]Audit Paused. Progress saved.[/bold yellow]")
                input("Press Enter to continue...")
                return

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

def render_final_review_layout(record, workspace=None, pyd_ta=None):
    # Retrieve P-layers
    layers = record.analysis_layers
    p0 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P0'), None)
    p2 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P2'), None)
    p3 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P3'), None)
    p4 = next((l for l in layers if l.department == 'TACTICAL' and l.layer_name == 'P4'), None)
    p1 = next((l for l in layers if l.department == 'TACTICAL' and l.layer_name == 'P1'), None)
    
    # 1. Structural Vector Panel (Efficiency + P0, P2, P3)
    struct_text = Text()
    struct_text.append("Market Bias: ", style="dim")
    bias_val = workspace.get("market_bias") if workspace else record.market_bias
    bias_style = "bold green" if bias_val == "Bullish" else "bold red" if bias_val == "Bearish" else "bold yellow"
    struct_text.append(f"{bias_val}\n\n", style=bias_style)
    
    for label, p_layer in [("P0", p0), ("P2", p2), ("P3", p3)]:
        struct_text.append(f"{label}: ", style="bold cyan")
        if p_layer:
            d_style = "green" if p_layer.direction == "Long" else "red" if p_layer.direction == "Short" else "yellow"
            struct_text.append(f"{p_layer.direction.upper()}", style=d_style)
            struct_text.append(" | ", style="dim")
            s_style = "bold" if p_layer.strength == "Strong" else ""
            struct_text.append(f"{p_layer.strength.upper()}\n", style=s_style)
            if p_layer.thesis:
                indented_thesis = format_indented_block(p_layer.thesis, indent_spaces=11, wrap_width=38)
                struct_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
        else:
            struct_text.append("N/A\n", style="dim")
            
    # Efficiency Audit Table Metrics
    struct_text.append("\n--- Efficiency Audit Metrics ---\n", style="bold cyan")
    ea = record.efficiency_audit
    if ea:
        real_bias = workspace.get("real_bias_b") if (workspace and "real_bias_b" in workspace) else ea.real_bias_b
        res_type = workspace.get("resolution_type") if (workspace and "resolution_type" in workspace) else ea.resolution_type
        struct_res = workspace.get("structural_resolution") if (workspace and "structural_resolution" in workspace) else ea.structural_resolution
        fail_reason = workspace.get("failure_reason") if (workspace and "failure_reason" in workspace) else ea.failure_reason
        lesson_eff = workspace.get("lesson_eff") if (workspace and "lesson_eff" in workspace) else ea.lesson_learned
        
        struct_text.append("Bias A (Original): ", style="dim")
        struct_text.append(f"{ea.bias_a}\n", style="white")
        struct_text.append("Real Bias B: ", style="dim")
        struct_text.append(f"{real_bias}\n", style="white")
        struct_text.append("Resolution Type: ", style="dim")
        struct_text.append(f"{res_type}\n", style="white")
        struct_text.append("Structural Resolution: ", style="dim")
        struct_text.append(f"{struct_res}\n", style="white")
        struct_text.append("Failure Reason: ", style="dim")
        struct_text.append(f"{fail_reason}\n", style="white")
        if lesson_eff:
            indented_lesson = format_indented_block(lesson_eff, indent_spaces=11, wrap_width=38)
            struct_text.append(f"Lesson Learned:\n  {indented_lesson}\n", style="dim italic")
            
    # 2. Tactical Vector Panel (Tactical + P1, P4)
    tact_text = Text()
    tact_text.append("Hierarchy: ", style="dim")
    tact_text.append(f"{record.p4_hierarchy}\n", style="bold white")
    tact_text.append("Timeframe: ", style="dim")
    tact_text.append(f"{record.p1_timeframe}\n", style="bold white")
    tact_text.append("Fractal Type: ", style="dim")
    tact_text.append(f"{record.p1_type}\n", style="bold white")
    tact_text.append("Nodes L1/L2: ", style="dim")
    tact_text.append(f"{record.nodes_l1} / {record.nodes_l2}\n\n", style="bold white")
    
    for label, p_layer in [("P4", p4), ("P1", p1)]:
        tact_text.append(f"{label}: ", style="bold magenta")
        if p_layer:
            d_style = "green" if p_layer.direction == "Long" else "red" if p_layer.direction == "Short" else "yellow"
            tact_text.append(f"{p_layer.direction.upper()}", style=d_style)
            tact_text.append(" | ", style="dim")
            s_style = "bold" if p_layer.strength == "Strong" else ""
            tact_text.append(f"{p_layer.strength.upper()}\n", style=s_style)
            if p_layer.thesis:
                indented_thesis = format_indented_block(p_layer.thesis, indent_spaces=11, wrap_width=38)
                tact_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
        else:
            tact_text.append("N/A\n", style="dim")
            
    # Tactical Audit Table Metrics
    tact_text.append("\n--- Tactical Audit Metrics ---\n", style="bold magenta")
    ta = record.tactical_audit
    if ta:
        t_comp = workspace.get("compliance") if (workspace and "compliance" in workspace) else ta.compliance
        could_hit = workspace.get("could_hit_tp") if (workspace and "could_hit_tp" in workspace) else ta.could_hit_tp
        entry_p = workspace.get("entry_price") if (workspace and "entry_price" in workspace) else ta.entry_price
        close_p = workspace.get("closing_price") if (workspace and "closing_price" in workspace) else ta.closing_price
        size = workspace.get("size") if (workspace and "size" in workspace) else ta.size
        sl = workspace.get("stop_loss") if (workspace and "stop_loss" in workspace) else ta.stop_loss
        tp = workspace.get("take_profit") if (workspace and "take_profit" in workspace) else ta.take_profit
        mae = workspace.get("mae") if (workspace and "mae" in workspace) else ta.mae_adverse
        mfe = workspace.get("mfe") if (workspace and "mfe" in workspace) else ta.mfe_favorable
        lesson_tact = workspace.get("lesson_tact") if (workspace and "lesson_tact" in workspace) else ta.lesson_learned
        
        tact_text.append("Compliance: ", style="dim")
        tact_text.append(f"{t_comp}\n", style="white")
        tact_text.append("Could Hit TP: ", style="dim")
        tact_text.append(f"{could_hit}\n", style="white")
        tact_text.append("Entry Price: ", style="dim")
        tact_text.append(f"{entry_p}\n", style="white")
        tact_text.append("Closing Price: ", style="dim")
        tact_text.append(f"{close_p}\n", style="white")
        tact_text.append("Size: ", style="dim")
        tact_text.append(f"{size}\n", style="white")
        tact_text.append("Stop Loss: ", style="dim")
        tact_text.append(f"{sl}\n", style="white")
        tact_text.append("Take Profit: ", style="dim")
        tact_text.append(f"{tp}\n", style="white")
        tact_text.append("MAE Adverse: ", style="dim")
        tact_text.append(f"{mae}\n", style="white")
        tact_text.append("MFE Favorable: ", style="dim")
        tact_text.append(f"{mfe}\n", style="white")
        if lesson_tact:
            indented_lesson = format_indented_block(lesson_tact, indent_spaces=11, wrap_width=38)
            tact_text.append(f"Lesson Learned:\n  {indented_lesson}\n", style="dim italic")
            
    # 3. Quantitative Profile Panel (automated exposure metrics)
    quant_text = Text()
    quant_text.append("I_CD (Edge): ", style="dim")
    edge_style = "bold green" if record.calc_edge >= 0.26 else "bold red" if record.calc_edge <= -0.26 else "bold yellow"
    quant_text.append(f"{record.calc_edge:.4f}\n\n", style=edge_style)
    
    quant_text.append("Long Prob: ", style="dim")
    quant_text.append(f"{record.long_prob * 100:.1f}%\n", style="green")
    quant_text.append("Short Prob: ", style="dim")
    quant_text.append(f"{record.short_prob * 100:.1f}%\n", style="red")
    quant_text.append("No-Trade Prob: ", style="dim")
    quant_text.append(f"{record.no_trade_prob * 100:.1f}%\n\n", style="yellow")
    
    quant_text.append("Tactical Classification:\n", style="dim")
    quant_text.append(f"  {record.tactical_classification}\n", style="bold cyan")
    
    # Recalculated metrics if PydanticTacticalAudit is available
    if pyd_ta:
        quant_text.append("\n--- Recalculated Exposure ---\n", style="bold yellow")
        if pyd_ta.trade_decision:
            quant_text.append(f"Decision: {pyd_ta.trade_decision}\n")
        if pyd_ta.notional_size is not None:
            quant_text.append(f"Notional Size: ${pyd_ta.notional_size:.2f}\n")
        if pyd_ta.capital_at_risk is not None:
            quant_text.append(f"Capital At Risk: ${pyd_ta.capital_at_risk:.2f}\n")
        if pyd_ta.risk_usd is not None:
            quant_text.append(f"Risk USD: ${pyd_ta.risk_usd:.2f}\n")
        if pyd_ta.r_r is not None:
            quant_text.append(f"R:R: {pyd_ta.r_r:.2f}\n")
        if pyd_ta.pnl is not None:
            quant_text.append(f"PnL: ${pyd_ta.pnl:.2f}\n")
    elif ta:
        quant_text.append("\n--- Saved Exposure ---\n", style="bold yellow")
        if ta.trade_decision:
            quant_text.append(f"Decision: {ta.trade_decision}\n")
        if ta.notional_size is not None:
            quant_text.append(f"Notional Size: ${ta.notional_size:.2f}\n")
        if ta.capital_at_risk is not None:
            quant_text.append(f"Capital At Risk: ${ta.capital_at_risk:.2f}\n")
        if ta.risk_usd is not None:
            quant_text.append(f"Risk USD: ${ta.risk_usd:.2f}\n")
        if ta.r_r is not None:
            quant_text.append(f"R:R: {ta.r_r:.2f}\n")
        if ta.pnl_and_cost is not None:
            quant_text.append(f"PnL & Cost: ${ta.pnl_and_cost:.2f}\n")
            
    # 4. State & Meta Panel
    meta_text = Text()
    meta_text.append("Full ID: ", style="dim")
    meta_text.append(f"{record.id}\n", style="cyan")
    meta_text.append("Short ID: ", style="dim")
    meta_text.append(f"{record.id[:8]}\n", style="bold cyan")
    meta_text.append("Created: ", style="dim")
    meta_text.append(f"{record.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n", style="white")
    meta_text.append("Updated: ", style="dim")
    meta_text.append(f"{record.updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n", style="white")
    meta_text.append("Lifecycle State:\n", style="dim")
    state_style = "bold green" if record.state in ["COMPLETED", "SYNCED", "READY_FOR_NOTION"] else "bold yellow"
    meta_text.append(f"  {record.state}\n", style=state_style)
    if record.edge_description:
        meta_text.append(f"\nEdge Description:\n", style="dim")
        meta_text.append(f"  {record.edge_description}\n", style="italic white")
        
    p_struct = Panel(struct_text, title="[Structural Vector (Eff)]", border_style="cyan", box=box.ROUNDED)
    p_tact = Panel(tact_text, title="[Tactical Vector (Exec)]", border_style="magenta", box=box.ROUNDED)
    p_quant = Panel(quant_text, title="[Quantitative Profile]", border_style="green", box=box.ROUNDED)
    p_meta = Panel(meta_text, title="[State & Meta]", border_style="yellow", box=box.ROUNDED)
    
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(p_struct, p_tact)
    grid.add_row(p_quant, p_meta)
    
    asset_val = workspace.get("asset") if workspace else record.asset
    dashboard = Panel(
        grid,
        title=f"[white]Unified Repair Dashboard: {asset_val} - {record.id[:8]}[/white]",
        border_style="bold blue",
        box=box.DOUBLE
    )
    return dashboard

def flow_executed_trades_repair():
    from tools.database import engine_default, UnifiedDepartment, EfficiencyAudit as DbEfficiencyAudit, TacticalAudit as DbTacticalAudit
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from cli.schemas.audit_tactical import TacticalAudit as PydanticTacticalAudit
    
    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        console.rule("[bold cyan]Repair Executed Trades (READY_FOR_NOTION)[/bold cyan]")
        console.print()
        
        with Session(engine_default) as db_session:
            stmt = select(UnifiedDepartment).where(UnifiedDepartment.state == LifecycleState.READY_FOR_NOTION.value)
            records = db_session.scalars(stmt).all()
            
            if not records:
                console.print("[yellow]No trades in READY_FOR_NOTION state found.[/yellow]\n")
                input("Press Enter to continue...")
                return
                
            choices = []
            for r in records:
                choices.append(Choice(r.id, name=f"[{r.id[:8]}] {r.asset} | {r.market_bias} | {r.tactical_classification}"))
            choices.append(Choice("back", name="[Back to Configuration]"))
            
            selected_id = inquirer.select(
                message="Select Trade to Inspect and Repair >",
                choices=choices,
                pointer=">",
                qmark=""
            ).execute()
            
            if selected_id == "back":
                return
                
            record = db_session.get(UnifiedDepartment, selected_id)
            if not record or not record.efficiency_audit or not record.tactical_audit:
                console.print("[red]Error: Required audit records not found for this trade.[/red]")
                input("Press Enter to continue...")
                continue
                
            ea = record.efficiency_audit
            ta = record.tactical_audit
            
            # Populate mutable workspace context
            workspace = {
                # Efficiency Audit Fields
                "real_bias_b": ea.real_bias_b,
                "resolution_type": ea.resolution_type,
                "structural_resolution": ea.structural_resolution,
                "failure_reason": ea.failure_reason,
                "lesson_eff": ea.lesson_learned,
                
                # Tactical Audit Fields
                "compliance": ta.compliance,
                "could_hit_tp": ta.could_hit_tp,
                "entry_price": ta.entry_price or 0.0,
                "closing_price": ta.closing_price or 0.0,
                "size": ta.size or 0.0,
                "stop_loss": ta.stop_loss or 0.0,
                "take_profit": ta.take_profit or 0.0,
                "mae": ta.mae_adverse or 0.0,
                "mfe": ta.mfe_favorable or 0.0,
                "lesson_tact": ta.lesson_learned
            }
            
            # Repair Hook Loop
            while True:
                # Recalculate metrics for display
                pyd_ta = PydanticTacticalAudit(
                    tactical_id=selected_id,
                    compliance=workspace["compliance"],
                    entry_price=workspace["entry_price"],
                    closing_price=workspace["closing_price"],
                    size=workspace["size"],
                    stop_loss=workspace["stop_loss"],
                    take_profit=workspace["take_profit"],
                    mae=workspace["mae"],
                    mfe=workspace["mfe"],
                    cost=0.0
                )
                
                # Render comprehensive three-column dashboard layout
                dashboard = render_final_review_layout(record, workspace=workspace, pyd_ta=pyd_ta)
                
                try:
                    console.clear(home=True)
                except TypeError:
                    console.clear()
                console.print(dashboard)
                
                action = inquirer.select(
                    message="Repair Action >",
                    choices=[
                        Choice("save", name="[1] Confirm & Save Repair"),
                        Choice("edit", name="[2] Edit an Audit Field"),
                        Choice("discard", name="[3] Discard Changes")
                    ],
                    pointer=">",
                    qmark=""
                ).execute()
                
                if action == "save":
                    try:
                        # Update Efficiency fields
                        ea.real_bias_b = workspace["real_bias_b"]
                        ea.resolution_type = workspace["resolution_type"]
                        ea.structural_resolution = workspace["structural_resolution"]
                        ea.failure_reason = workspace["failure_reason"]
                        ea.lesson_learned = workspace["lesson_eff"]
                        ea.updated_at = datetime.datetime.now()
                        
                        # Update Tactical fields
                        ta.compliance = workspace["compliance"]
                        ta.could_hit_tp = workspace["could_hit_tp"]
                        ta.entry_price = workspace["entry_price"]
                        ta.closing_price = workspace["closing_price"]
                        ta.size = workspace["size"]
                        ta.stop_loss = workspace["stop_loss"]
                        ta.take_profit = workspace["take_profit"]
                        ta.mae_adverse = workspace["mae"]
                        ta.mfe_favorable = workspace["mfe"]
                        ta.lesson_learned = workspace["lesson_tact"]
                        
                        # Recalculated fields
                        ta.notional_size = pyd_ta.notional_size
                        ta.capital_at_risk = pyd_ta.capital_at_risk
                        ta.risk_usd = pyd_ta.risk_usd
                        ta.r_r = pyd_ta.r_r
                        ta.pnl_and_cost = pyd_ta.pnl_and_cost
                        ta.trade_decision = pyd_ta.trade_decision
                        
                        db_session.commit()
                        console.print("[success]Repair successfully committed to the database.[/success]")
                    except Exception as e:
                        db_session.rollback()
                        console.print(f"[bold red]Failed to save repair: {e}[/bold red]")
                    input("Press Enter to continue...")
                    break
                    
                elif action == "discard":
                    break
                    
                elif action == "edit":
                    edit_choices = [
                        # Efficiency Audit Fields
                        Choice("real_bias_b", name=f"[Efficiency] Real Bias B: {workspace['real_bias_b']}"),
                        Choice("resolution_type", name=f"[Efficiency] Resolution Type: {workspace['resolution_type']}"),
                        Choice("structural_resolution", name=f"[Efficiency] Structural Resolution: {workspace['structural_resolution']}"),
                        Choice("failure_reason", name=f"[Efficiency] Failure Reason: {workspace['failure_reason']}"),
                        Choice("lesson_eff", name=f"[Efficiency] Lesson Learned: {workspace['lesson_eff'] or ''}"),
                        
                        # Tactical Audit Fields
                        Choice("compliance", name=f"[Tactical] Compliance State: {workspace['compliance']}"),
                        Choice("could_hit_tp", name=f"[Tactical] Could Hit TP: {workspace['could_hit_tp']}"),
                        Choice("entry_price", name=f"[Tactical] Entry Price: {workspace['entry_price']}"),
                        Choice("closing_price", name=f"[Tactical] Closing Price: {workspace['closing_price']}"),
                        Choice("size", name=f"[Tactical] Size: {workspace['size']}"),
                        Choice("stop_loss", name=f"[Tactical] Stop Loss: {workspace['stop_loss']}"),
                        Choice("take_profit", name=f"[Tactical] Take Profit: {workspace['take_profit']}"),
                        Choice("mae", name=f"[Tactical] MAE (Adverse): {workspace['mae']}"),
                        Choice("mfe", name=f"[Tactical] MFE (Favorable): {workspace['mfe']}"),
                        Choice("lesson_tact", name=f"[Tactical] Lesson Learned: {workspace['lesson_tact'] or ''}"),
                        
                        Choice("back", name="[<] Back to Review")
                    ]
                    
                    field = inquirer.select(
                        message="Select Field to Edit >",
                        choices=edit_choices,
                        pointer=">",
                        qmark=""
                    ).execute()
                    
                    if field == "back":
                        continue
                        
                    if field == "real_bias_b":
                        workspace["real_bias_b"] = get_enum_choice("Edit Real Bias B", StructuralBias).value
                    elif field == "resolution_type":
                        workspace["resolution_type"] = get_enum_choice("Edit Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN]).value
                    elif field == "structural_resolution":
                        workspace["structural_resolution"] = get_enum_choice("Edit Structural Resolution", StructuralResolution).value
                    elif field == "failure_reason":
                        workspace["failure_reason"] = get_enum_choice("Edit Failure Reason", FailureReason).value
                    elif field == "lesson_eff":
                        workspace["lesson_eff"] = get_optional_text("Edit Efficiency Lesson Learned")
                    elif field == "compliance":
                        workspace["compliance"] = get_enum_choice("Edit Compliance State", ComplianceState).value
                    elif field == "could_hit_tp":
                        workspace["could_hit_tp"] = inquirer.select(
                            message="Could hit TP? >",
                            choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                            pointer=">",
                            qmark="",
                            keybindings={"skip": []}
                        ).execute()
                    elif field in ["entry_price", "closing_price", "size", "stop_loss", "take_profit"]:
                        workspace[field] = get_mandatory_float(f"Enter {field.replace('_', ' ').title()}")
                    elif field in ["mae", "mfe"]:
                        workspace[field] = get_mandatory_float(f"Enter {field.upper()} (0 <= val <= 10)", min_val=0, max_val=10)
                    elif field == "lesson_tact":
                        workspace["lesson_tact"] = get_mandatory_text("Edit Tactical Lesson Learned", multiline=True)

if __name__ == "__main__":
    cli()
