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
            render_wizard_layout(step_num, step_title, self, key)
            
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
    "asset", "p0_thesis", "p0_dir", "p0_str",
    "p1_thesis", "p1_dir", "p1_str", "p1_tf", "p1_type", "nodes_l1", "nodes_l2",
    "p2_thesis", "p2_dir", "p2_str"
]

STEP2_KEYS = [
    "p3_thesis", "p3_dir", "p3_str",
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

def get_mandatory_text(prompt_text, multiline=False):
    while True:
        message = f"{prompt_text} >"
        if multiline:
            message += " (Presiona Esc + Enter para guardar)"
        val = bind_pause(inquirer.text(message=message, multiline=multiline, keybindings={"skip": []})).execute()
        if val and val.strip():
            return val.strip()

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

def build_persistent_layout(active_session_name=None, pending_count=0, sync_status="OFFLINE", footer_type="menu"):
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", minimum_size=10),
        Layout(name="footer", size=3)
    )
    
    # Header
    if active_session_name:
        session_str = f"[bold warning]TEST FLIGHT: {active_session_name}[/bold warning]"
    else:
        session_str = f"[bold primary]B.L.A.S.T. ENGINE v1.1[/bold primary]"
        
    status_str = f"Audits: [bold warning]{pending_count}[/bold warning] | Notion: {sync_status}"
    header_content = Panel(
        Align.center(Text.from_markup(f"{session_str} | {status_str}")),
        border_style="primary",
        box=box.ROUNDED
    )
    layout["header"].update(header_content)
    
    # Footer
    if footer_type == "wizard":
        footer_text = Text.from_markup("[muted]Atajos: [bold primary][↑/↓][/bold primary] Navegar | [bold primary][Enter][/bold primary] Seleccionar | [bold primary][C-x][/bold primary] Guardar/Pausar | [bold primary][Esc][/bold primary] Cancelar | [bold primary][C-s][/bold primary] Notion Sync[/muted]")
    else:
        footer_text = Text.from_markup("[muted]Atajos: [bold primary][↑/↓][/bold primary] Navegar | [bold primary][1-5][/bold primary] Selección Rápida | [bold primary][Enter][/bold primary] Confirmar | [bold primary][X][/bold primary] Salir[/muted]")
        
    footer_content = Panel(Align.center(footer_text), border_style="muted", box=box.ROUNDED)
    layout["footer"].update(footer_content)
    
    return layout

def get_total_records_count():
    from tools.database import engine_default, UnifiedDepartment
    from sqlalchemy.orm import Session
    from sqlalchemy import func
    try:
        with Session(engine_default) as db_session:
            return db_session.query(func.count(UnifiedDepartment.id)).scalar()
    except Exception:
        return 0

def get_welcome_options():
    options = [
        ("1", "New Unified Analysis", "primary", "core"),
        ("2", "Execute Audits", "primary", "core"),
        ("3", "Review History (Last 5)", "primary", "core"),
        ("4", "Force Notion Sync", "primary", "system"),
        ("5", "Configuration", "primary", "system"),
    ]
    if ACTIVE_SESSION:
        options.append(("t", f"Test Drive ({ACTIVE_SESSION['name']})", "warning", "test_drive"))
        options.append(("e", f"Exit Test Flight - {ACTIVE_SESSION['name']}", "warning", "exit_session"))
    else:
        options.append(("t", "Test Drive (Q3-BTC-SCALPING)", "primary", "system"))
    options.append(("x", "Exit", "danger", "exit"))
    return options

def build_welcome_body(pending_records, active_idx=0):
    # Create outer layout
    outer_layout = Layout()
    outer_layout.split_row(
        Layout(name="menu_col", ratio=1),
        Layout(name="telemetry_col", ratio=1)
    )
    
    options = get_welcome_options()
    
    # Left panel: Main Menu Actions
    menu_text = Text()
    menu_text.append("\n")
    
    current_category = None
    for idx, (key, label, style_name, category) in enumerate(options):
        if category != current_category:
            if category == "core":
                menu_text.append("   -- CORE PIPELINE --\n", style="muted")
            elif category == "system":
                menu_text.append("\n   -- SYSTEM --\n", style="muted")
            elif category in ["test_drive", "exit_session", "exit"]:
                # Print the spacer '--' once before exit or test options
                if current_category not in ["test_drive", "exit_session", "exit"]:
                    menu_text.append("\n   --\n", style="muted")
            current_category = category
            
        is_active = (idx == active_idx)
        item_text = f"[{key.upper()}] {label}"
        
        if is_active:
            menu_text.append("   ➔ ", style="primary")
            menu_text.append(f" {item_text} \n", style="highlight")
        else:
            menu_text.append("     ")
            menu_text.append(f"{item_text}\n", style=style_name)
            
    now_str = datetime.datetime.now().strftime("%H:%M %d/%m")
    menu_panel = Panel(
        Align.left(menu_text),
        title=f"[bold secondary]Main Menu Actions (Last Session: {now_str})[/bold secondary]",
        border_style="secondary",
        box=box.ROUNDED
    )
    outer_layout["menu_col"].update(menu_panel)
    
    # Right column splits vertically into Engine State and Environment Config
    right_layout = Layout()
    right_layout.split_column(
        Layout(name="engine_state", ratio=1),
        Layout(name="env_config", ratio=1)
    )
    
    # Get total records
    total_records = get_total_records_count()
    
    # 1. Engine State
    state_table = Table(box=box.SIMPLE, border_style="muted", expand=True)
    state_table.add_column("Metric", style="cyan")
    state_table.add_column("Value", style="bold white")
    
    db_name = f"{ACTIVE_SESSION['id'][:8]}.db" if ACTIVE_SESSION else "journal.db"
    state_table.add_row("Active DB", f"{db_name} ({'Test Drive' if ACTIVE_SESSION else 'Production'})")
    state_table.add_row("Total records", f"{total_records:,}")
    state_table.add_row("Pending Audits", f"{len(pending_records)} [bold warning]")
    
    daemon_status = check_daemon_status()
    daemon_on = "ONLINE" in daemon_status
    state_table.add_row("Notion Sync", f"{'ACTIVE [success]' if daemon_on else 'INACTIVE [danger]'}")
    
    state_panel = Panel(
        state_table,
        title="[bold cyan]Engine State[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED
    )
    right_layout["engine_state"].update(state_panel)
    
    # 2. Environment Configuration
    env_table = Table(box=box.SIMPLE, border_style="muted", expand=True)
    env_table.add_column("Metric", style="magenta")
    env_table.add_column("Value", style="bold white")
    
    assets = get_assets()
    assets_str = ", ".join(assets[:4])
    env_table.add_row("Tracked Assets", assets_str)
    env_table.add_row("Notion Target", "EFFICIENCY_DB")
    env_table.add_row("Unified DB", "TACTICAL_DB")
    env_table.add_row("Calculated Edge", "|I_CD| >= 0.26")
    
    env_panel = Panel(
        env_table,
        title="[bold secondary]Environment Configuration[/bold secondary]",
        border_style="secondary",
        box=box.ROUNDED
    )
    right_layout["env_config"].update(env_panel)
    
    # Wrap right column in a System Telemetry layout container
    telemetry_container = Panel(
        right_layout,
        title="[muted]System Telemetry[/muted]",
        border_style="muted",
        box=box.ROUNDED
    )
    outer_layout["telemetry_col"].update(telemetry_container)
    
    return Panel(
        outer_layout,
        title="[white]Command Center Dashboard[/white]",
        border_style="white",
        box=box.ROUNDED
    )

def render_pending_audits_table(records) -> Panel:
    table = Table(box=box.SIMPLE, border_style="muted", expand=True)
    table.add_column("ID", justify="center", style="dim")
    table.add_column("Asset", justify="center", style="bold white")
    table.add_column("Status Indicators", justify="center")
    table.add_column("Created Date", justify="center", style="dim")
    
    for r in records:
        short_id = r["id"][:8]
        asset = r["payload"].get("asset", "Unknown")
        has_eff = r["payload"].get("audit_efficiency", {}).get("real_bias_b") is not None
        has_tac = "audit_tactical" in r["payload"]
        is_eff_paused = has_paused_state(r["id"], "eff")
        is_tac_paused = has_paused_state(r["id"], "tac")
        
        if has_eff: eff_status = "[success][✓] Eff[/success]"
        elif is_eff_paused: eff_status = "[warning][✗] Eff[/warning]"
        else: eff_status = "[danger][ ] Eff[/danger]"
        
        if has_tac: tac_status = "[success][✓] Tac[/success]"
        elif is_tac_paused: tac_status = "[warning][✗] Tac[/warning]"
        else: tac_status = "[danger][ ] Tac[/danger]"
        
        status_str = f"{eff_status} | {tac_status}"
        date_str = r["created_at"].strftime("%Y-%m-%d %H:%M")
        table.add_row(short_id, asset, status_str, date_str)
        
    return Panel(table, title="[bold secondary]Pending Audits Queue[/bold secondary]", border_style="secondary", box=box.ROUNDED)

def get_mock_market_context(asset):
    asset_upper = asset.upper() if asset else "UNKNOWN"
    if "BTC" in asset_upper:
        price = "$68,421.50"
        change = "+2.45%"
        volume = "$31.8B"
        rsi = "62.4 (Neutral-Bullish)"
        support = "$67,200 / $66,500"
        resistance = "$69,500 / $70,200"
    elif "ETH" in asset_upper:
        price = "$3,512.20"
        change = "+1.89%"
        volume = "$14.2B"
        rsi = "58.1 (Neutral)"
        support = "$3,420 / $3,350"
        resistance = "$3,600 / $3,720"
    elif "SOL" in asset_upper:
        price = "$142.85"
        change = "-0.78%"
        volume = "$4.5B"
        rsi = "45.2 (Neutral-Bearish)"
        support = "$138.00 / $132.50"
        resistance = "$148.00 / $155.00"
    else:
        price = "$124.50"
        change = "+0.45%"
        volume = "$1.2B"
        rsi = "52.3 (Neutral)"
        support = "$121.20 / $119.50"
        resistance = "$126.80 / $128.00"
        
    return {
        "price": price,
        "change": change,
        "volume": volume,
        "rsi": rsi,
        "support": support,
        "resistance": resistance
    }

def get_latest_analysis_record():
    from tools.database import engine_default, UnifiedDepartment
    from sqlalchemy.orm import Session
    from sqlalchemy import select, desc
    try:
        with Session(engine_default) as db_session:
            stmt = select(UnifiedDepartment).order_by(desc(UnifiedDepartment.created_at)).limit(1)
            record = db_session.scalars(stmt).first()
            if record:
                layers_list = []
                for layer in record.analysis_layers:
                    layers_list.append({
                        "department": layer.department,
                        "layer_name": layer.layer_name,
                        "direction": layer.direction,
                        "strength": layer.strength,
                        "thesis": layer.thesis
                    })
                
                return {
                    "id": record.id,
                    "asset": record.asset,
                    "market_bias": record.market_bias,
                    "calc_edge": record.calc_edge,
                    "edge_description": record.edge_description,
                    "p4_hierarchy": record.p4_hierarchy,
                    "p1_timeframe": record.p1_timeframe,
                    "p1_type": record.p1_type,
                    "nodes_l1": record.nodes_l1,
                    "nodes_l2": record.nodes_l2,
                    "tactical_classification": record.tactical_classification,
                    "created_at": record.created_at,
                    "layers": layers_list
                }
    except Exception:
        pass
    return None

def render_wizard_layout(step_num, step_title, session, active_key):
    try:
        audit_records = get_records_by_state(LifecycleState.PENDING_AUDITS)
    except Exception:
        audit_records = []
    pending_audits = len(audit_records)
    
    try:
        sync_status = check_daemon_status()
    except Exception:
        sync_status = "OFFLINE"
        
    layout = build_persistent_layout(
        ACTIVE_SESSION["name"] if ACTIVE_SESSION else None,
        pending_audits,
        sync_status,
        footer_type="wizard"
    )
    
    outer_layout = Layout()
    outer_layout.split_row(
        Layout(name="left_col", ratio=1),
        Layout(name="right_col", ratio=1)
    )
    
    left_layout = Layout()
    left_layout.split_column(
        Layout(name="form_wizard", ratio=3),
        Layout(name="pending_queue", ratio=1)
    )
    
    form_text = Text()
    form_text.append("\n")
    
    step_keys = STEP1_KEYS if step_num == 1 else STEP2_KEYS
    for k in step_keys:
        label = KEY_LABELS.get(k, k)
        if k == active_key:
            form_text.append(" ➔ ", style="highlight")
            form_text.append(f"[ ] {label}: ", style="highlight")
            form_text.append("<Active Input>\n", style="bold white")
        elif k in session.state:
            val = session.state[k]
            if isinstance(val, Enum):
                display_val = val.value
            elif isinstance(val, str):
                display_val = val[:22] + "..." if len(val) > 25 else val
            else:
                display_val = str(val)
            form_text.append("    [✓] ", style="success")
            form_text.append(f"{label}: ", style="success")
            form_text.append(f"{display_val}\n", style="success")
        else:
            form_text.append(f"    [ ] {label}: ...\n", style="muted")
            
    form_text.append("\n  -- Progress --\n", style="muted")
    if step_num == 1:
        form_text.append("  ➔ [bold primary][Structure][/bold primary] ➔ [dim]Tactic[/dim] ➔ [dim]Review[/dim]")
    else:
        form_text.append("  [success][Structure][/success] ➔ [bold primary][Tactic][/bold primary] ➔ [dim]Review[/dim]")
    form_text.append("\n")
    
    form_panel = Panel(
        Align.left(form_text),
        title=f"[bold primary]{step_title}[/bold primary]",
        border_style="primary",
        box=box.ROUNDED
    )
    left_layout["form_wizard"].update(form_panel)
    
    queue_table = Table(box=box.SIMPLE, border_style="muted", expand=True)
    queue_table.add_column("ID", justify="center", style="dim")
    queue_table.add_column("Asset", justify="center", style="bold white")
    queue_table.add_column("Status Indicators", justify="center")
    
    for r in audit_records[:2]:
        short_id = r["id"][:8]
        asset = r["payload"].get("asset", "Unknown")
        has_eff = r["payload"].get("audit_efficiency", {}).get("real_bias_b") is not None
        has_tac = "audit_tactical" in r["payload"]
        is_eff_paused = has_paused_state(r["id"], "eff")
        is_tac_paused = has_paused_state(r["id"], "tac")
        
        if has_eff: eff_status = "[success][✓] Eff[/success]"
        elif is_eff_paused: eff_status = "[warning][✗] Eff[/warning]"
        else: eff_status = "[danger][ ] Eff[/danger]"
        
        if has_tac: tac_status = "[success][✓] Tac[/success]"
        elif is_tac_paused: tac_status = "[warning][✗] Tac[/warning]"
        else: tac_status = "[danger][ ] Tac[/danger]"
        
        status_str = f"{eff_status} | {tac_status}"
        queue_table.add_row(short_id, asset, status_str)
        
    queue_panel = Panel(
        queue_table,
        title="[bold warning]Pending Audits Queue[/bold warning]",
        border_style="warning",
        box=box.ROUNDED
    )
    left_layout["pending_queue"].update(queue_panel)
    outer_layout["left_col"].update(left_layout)
    
    prev = get_latest_analysis_record()
    if not prev:
        empty_text = Text()
        empty_text.append("\n\n   No previous analyses found in database.\n", style="muted italic")
        empty_text.append("   Complete your first New Unified Analysis to see\n", style="muted")
        empty_text.append("   historical context here.\n", style="muted")
        
        context_panel = Panel(
            Align.center(empty_text),
            title="[bold secondary]Previous Analysis Context[/bold secondary]",
            border_style="secondary",
            box=box.ROUNDED
        )
    else:
        short_id = prev["id"][:8]
        asset = prev["asset"]
        bias = prev["market_bias"]
        edge = prev["calc_edge"]
        created = prev["created_at"].strftime("%m/%d %H:%M")
        
        bias_style = "bold green" if bias == "Bullish" else "bold red" if bias == "Bearish" else "bold yellow"
        edge_style = "bold green" if edge >= 0.26 else "bold red" if edge <= -0.26 else "bold yellow"
        
        body_text = Text()
        body_text.append(f"Asset: [bold white]{asset}[/bold white] | ID: [bold cyan]{short_id}[/bold cyan] | {created}\n", style="muted")
        body_text.append("Bias: ")
        body_text.append(f"{bias}", style=bias_style)
        body_text.append(" | Edge (I_CD): ")
        body_text.append(f"{edge:.4f}\n", style=edge_style)
        body_text.append(f"Classification: [bold cyan]{prev['tactical_classification']}[/bold cyan]\n", style="muted")
        body_text.append("─" * 40 + "\n", style="muted")
        
        layers = prev["layers"]
        p0 = next((l for l in layers if l["department"] == 'EFFICIENCY' and l["layer_name"] == 'P0'), None)
        p1 = next((l for l in layers if l["department"] == 'TACTICAL' and l["layer_name"] == 'P1'), None)
        p2 = next((l for l in layers if l["department"] == 'EFFICIENCY' and l["layer_name"] == 'P2'), None)
        p3 = next((l for l in layers if l["department"] == 'EFFICIENCY' and l["layer_name"] == 'P3'), None)
        p4 = next((l for l in layers if l["department"] == 'TACTICAL' and l["layer_name"] == 'P4'), None)
        
        def fmt_layer(label, layer_obj, color_prefix):
            res = Text()
            res.append(f"{label}: ", style=f"bold {color_prefix}")
            if layer_obj:
                d = layer_obj["direction"]
                s = layer_obj["strength"]
                t = layer_obj["thesis"]
                d_style = "green" if d == "Long" else "red" if d == "Short" else "yellow"
                res.append(f"{d.upper()}", style=d_style)
                res.append(" | ", style="dim")
                s_style = "bold" if s == "Strong" else ""
                res.append(f"{s.upper()}", style=s_style)
                if t:
                    trunc_t = t[:35] + "..." if len(t) > 38 else t
                    res.append(f"\n   Thesis: {trunc_t}", style="dim italic")
            else:
                res.append("N/A", style="dim")
            res.append("\n")
            return res
            
        body_text.append(fmt_layer("P0 (Macro)", p0, "cyan"))
        body_text.append(fmt_layer("P2 (Struc)", p2, "cyan"))
        body_text.append(fmt_layer("P3 (Trend)", p3, "cyan"))
        body_text.append(fmt_layer("P4 (Hier )", p4, "magenta"))
        body_text.append(fmt_layer("P1 (Timef)", p1, "magenta"))
        
        body_text.append("─" * 40 + "\n", style="muted")
        body_text.append("Tactical Metrics:\n", style="bold white")
        body_text.append(f"  Timeframe: [bold]{prev['p1_timeframe']}[/bold] | Hierarchy: [bold]{prev['p4_hierarchy']}[/bold]\n", style="muted")
        body_text.append(f"  Fractal Type: [bold]{prev['p1_type']}[/bold]\n", style="muted")
        body_text.append(f"  Nodes L1/L2: [bold]{prev['nodes_l1']}[/bold] / [bold]{prev['nodes_l2']}[/bold]\n", style="muted")
        
        context_panel = Panel(
            body_text,
            title=f"[bold secondary]Previous Analysis Context [{asset}][/bold secondary]",
            border_style="secondary",
            box=box.ROUNDED
        )
    outer_layout["right_col"].update(context_panel)
    
    layout["body"].update(outer_layout)
    
    try:
        console.clear(home=True)
    except TypeError:
        console.clear()
        
    console.print(layout)

def render_ui(active_idx=0):
    audit_records = get_records_by_state(LifecycleState.PENDING_AUDITS)
    pending_audits = len(audit_records)
    sync_status = check_daemon_status()
    
    try:
        console.clear(home=True)
    except TypeError:
        console.clear()
        
    layout = build_persistent_layout(
        ACTIVE_SESSION["name"] if ACTIVE_SESSION else None,
        pending_audits,
        sync_status
    )
    
    layout["body"].update(build_welcome_body(audit_records, active_idx))
    console.print(layout)

@click.group()
def cli():
    """B.L.A.S.T Interactive Engine"""
    init_db()

@cli.command()
def start():
    """Main Menu"""
    global ACTIVE_SESSION
    active_idx = 0
    while True:
        options = get_welcome_options()
        if active_idx >= len(options):
            active_idx = 0
            
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        render_ui(active_idx)
        
        kp = get_keypress()
        if not kp:
            continue
            
        selected_choice = None
        if kp == 'up':
            active_idx = (active_idx - 1) % len(options)
        elif kp == 'down':
            active_idx = (active_idx + 1) % len(options)
        elif kp == 'enter':
            selected_choice = options[active_idx][0]
        elif kp in [opt[0] for opt in options]:
            for i, opt in enumerate(options):
                if opt[0] == kp:
                    active_idx = i
                    break
            selected_choice = kp
            
        if selected_choice:
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
            elif selected_choice == "t":
                flow_test_drive()
            elif selected_choice == "e":
                ACTIVE_SESSION = None
                from tools.database import init_db
                init_db("sqlite:///.data/journal.db")
                console.print("[yellow]Exiting Test Flight and restoring main database connection...[/yellow]")
                import time
                time.sleep(1)
            elif selected_choice == "x":
                console.print("[yellow]Exiting B.L.A.S.T...[/yellow]")
                break

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
                        struct_text.append(f"   Thesis: {p_layer.thesis}\n", style="dim italic")
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
                        tact_text.append(f"   Thesis: {p_layer.thesis}\n", style="dim italic")
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
def flow_new_analysis():
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
            
            p0_thesis = session.prompt("p0_thesis", get_mandatory_text, "P0 Thesis", multiline=True)
            p0_dir = session.prompt("p0_dir", get_enum_choice, "P0 Direction", Direction)
            p0_str = session.prompt("p0_str", get_enum_choice, "P0 Strength", Strength)
            
            p1_thesis = session.prompt("p1_thesis", get_mandatory_text, "P1 Thesis", multiline=True)
            p1_dir = session.prompt("p1_dir", get_enum_choice, "P1 Direction", Direction)
            p1_str = session.prompt("p1_str", get_enum_choice, "P1 Strength", Strength)
            p1_tf = session.prompt("p1_tf", get_enum_choice, "P1 Timeframe", Timeframe)
            p1_type = session.prompt("p1_type", get_enum_choice, "P1 Fractal Type", FractalType)
            nodes_l1 = session.prompt("nodes_l1", get_mandatory_int, "Nodes L1")
            nodes_l2 = session.prompt("nodes_l2", get_mandatory_int, "Nodes L2")
            
            p2_thesis = session.prompt("p2_thesis", get_mandatory_text, "P2 Thesis", multiline=True)
            p2_dir = session.prompt("p2_dir", get_enum_choice, "P2 Direction", Direction)
            p2_str = session.prompt("p2_str", get_enum_choice, "P2 Strength", Strength)

            # --- STEP 2: Tactical & Meta Parameters ---
            p3_thesis = session.prompt("p3_thesis", get_mandatory_text, "P3 Thesis", multiline=True)
            p3_dir = session.prompt("p3_dir", get_enum_choice, "P3 Direction", Direction)
            p3_str = session.prompt("p3_str", get_enum_choice, "P3 Strength", Strength)
            
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
                            struct_text.append(f"   Thesis: {t}\n", style="dim italic")
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
                            tact_text.append(f"   Thesis: {t}\n", style="dim italic")
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
                meta_text.append(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n", style="white")
                meta_text.append("Lifecycle State:\n", style="dim")
                meta_text.append(f"  {LifecycleState.PENDING_AUDITS.value}\n", style="bold warning")
                if edge_desc:
                    meta_text.append(f"\nEdge Description:\n", style="dim")
                    meta_text.append(f"  {edge_desc}\n", style="italic white")
                    
                p_struct = Panel(struct_text, title="[Structural Vector (Eff)]", border_style="primary", box=box.ROUNDED)
                p_tact = Panel(tact_text, title="[Tactical Vector (Exec)]", border_style="secondary", box=box.ROUNDED)
                p_quant = Panel(quant_text, title="[Quantitative Profile]", border_style="success", box=box.ROUNDED)
                p_meta = Panel(meta_text, title="[State & Meta]", border_style="warning", box=box.ROUNDED)
                
                grid = Table.grid(expand=True)
                grid.add_column(ratio=1)
                grid.add_column(ratio=1)
                grid.add_row(p_struct, p_tact)
                grid.add_row(p_quant, p_meta)
                
                dashboard = Panel(
                    grid,
                    title=f"[white]Step 3/3: Review - Unified Analysis Dashboard: {asset}[/white]",
                    border_style="bold primary",
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
                            db_session.add(new_record)

                            new_ea = ModelEfficiencyAudit(id=trade_id, bias_a=bias_a.value)
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
        ACTIVE_SESSION["name"] if ACTIVE_SESSION else None,
        len(records),
        sync_status
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
        
        try:
            bias_a = StructuralBias(bias_a_val)
        except ValueError:
            bias_a = StructuralBias.NO_BIAS_CHOPPY

        session = AuditSession(trade_id, "eff")
        while True:
            try:
                real_bias_b = session.prompt("real_bias_b", get_enum_choice, "Real Bias B", StructuralBias)
                res_type = session.prompt("res_type", get_enum_choice, "Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN])
                struct_res = session.prompt("struct_res", get_enum_choice, "Structural Resolution", StructuralResolution)
                fail_reason = session.prompt("fail_reason", get_enum_choice, "Failure Reason", FailureReason)
                lesson_eff = session.prompt("lesson_eff", get_optional_text, "Efficiency Lesson Learned")

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
                break
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
                            qmark="",
                            keybindings={"skip": []}
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

if __name__ == "__main__":
    cli()
