import click
import uuid
import datetime
import subprocess
import os
import signal
import logging
import shutil
from pathlib import Path

from typing import Optional
from decimal import Decimal, getcontext
from sqlalchemy import text
from core.math_engine import calculate_probabilities, calculate_algebraic_metrics

getcontext().prec = 18

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
from tools.database import (
    init_db, update_record_state, get_records_by_state,
    LifecycleState, add_asset, get_assets, to_local_display
)
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
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logging.warning(f"Error cargando caché de estado: {e}")

    def save_state(self):
        cache = {}
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    cache = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logging.warning(f"Error cargando caché de estado: {e}")
        
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
            except (OSError, json.JSONDecodeError) as e:
                logging.warning(f"Error limpiando caché de estado: {e}")

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
            state.refresh_metrics(get_active_engine())
            render_wizard_layout(step_num, step_title, self, key, state, get_active_engine())
            
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
ACTIVE_ENGINE = None

def get_active_engine():
    global ACTIVE_ENGINE
    if ACTIVE_SESSION is not None:
        return ACTIVE_ENGINE
        
    import tools.database
    if tools.database.engine_default is not None:
        return tools.database.engine_default
    
    from tools.database import init_db
    import os
    
    legacy_db_path = ".data/journal.db"
    new_primary_db = "flight_account_001_xauusd.db"
    new_primary_path = f".data/{new_primary_db}"
    
    if os.path.exists(legacy_db_path) and not os.path.exists(new_primary_path):
        os.rename(legacy_db_path, new_primary_path)

    sessions = FlightSessionManager.load_sessions()
    if "001" not in sessions:
        sessions["001"] = {
            "name": "Main Flight Account",
            "db_name": new_primary_db,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "last_played": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        FlightSessionManager.save_sessions(sessions)
    
    primary_db = sessions["001"].get("db_name", new_primary_db)
    ACTIVE_ENGINE = init_db(f"sqlite:///.data/{primary_db}")
    return ACTIVE_ENGINE


state = CLIState()
FLIGHT_SESSIONS_FILE = ".data/flight_sessions.json"

class FlightSessionManager:
    @staticmethod
    def load_sessions():
        if not os.path.exists(FLIGHT_SESSIONS_FILE):
            return {}
        try:
            with open(FLIGHT_SESSIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def save_sessions(sessions):
        os.makedirs(os.path.dirname(FLIGHT_SESSIONS_FILE), exist_ok=True)
        with open(FLIGHT_SESSIONS_FILE, "w") as f:
            json.dump(sessions, f, indent=4)

    @staticmethod
    def create_session(account_index: str, name: str):
        import re
        sessions = FlightSessionManager.load_sessions()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        sanitized_nickname = re.sub(r'[^a-zA-Z0-9]', '', name).lower()
        db_name = f"flight_account_{account_index}_{sanitized_nickname}.db"
        sessions[account_index] = {
            "name": name,
            "db_name": db_name,
            "created_at": now,
            "last_played": now
        }
        FlightSessionManager.save_sessions(sessions)
        return account_index, sessions[account_index]

    @staticmethod
    def update_last_played(session_id):
        sessions = FlightSessionManager.load_sessions()
        if session_id in sessions:
            sessions[session_id]["last_played"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            FlightSessionManager.save_sessions(sessions)

    @staticmethod
    def delete_session(session_id):
        sessions = FlightSessionManager.load_sessions()
        if session_id in sessions:
            db_name = sessions[session_id].get("db_name", f"flight_account_{session_id}_{sessions[session_id].get('name', 'unknown').lower().replace(' ', '')}.db")
            del sessions[session_id]
            FlightSessionManager.save_sessions(sessions)
            # Optionally delete the db file
            db_path = f".data/{db_name}"
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

class ExitToMainMenuException(Exception):
    pass

class RestartFlowException(Exception):
    pass

class ReloadChoicesException(Exception):
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
def format_indented_block(text_value, indent_spaces=11, first_line_flush=True, wrap_width=80):
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
        
    # Strip trailing whitespace to guarantee horizontal bounds
    lines = [line.rstrip() for line in lines]
    
    if first_line_flush:
        return lines[0] + "".join(f"\n{prefix}{line}" for line in lines[1:])
    else:
        return prefix + lines[0] + "".join(f"\n{prefix}{line}" for line in lines[1:])

def auto_fetch_tradingview_screenshot(asset: str) -> Optional[str]:
    """
    Escanea la carpeta Downloads, filtra por el ticker del activo,
    selecciona el archivo más reciente, lo mueve a staging y retorna el nombre.
    """
    downloads_dir = Path("/mnt/c/Users/jcifu/Downloads")
    staging_dir = Path(os.getcwd()) / ".assets" / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Normalización de Ticker (ej. XAUUSD asimila XAUUSDT.P)
    base_ticker = asset.upper().replace("/", "").replace("USDT", "USD")
    if "XAU" in base_ticker: base_ticker = "XAUUSD"
    if "BTC" in base_ticker: base_ticker = "BTC"

    # Filtro Lexical y de Extensión
    search_patterns = [f"*{base_ticker}*.png", f"*{base_ticker}*.jpg"]
    possible_files = []
    for pattern in search_patterns:
        possible_files.extend(list(downloads_dir.glob(pattern)))

    if not possible_files:
        return None

    # Filtro Temporal: El más reciente es el índice 0
    possible_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    target_file = possible_files[0]

    destination = staging_dir / target_file.name
    try:
        shutil.move(str(target_file), str(destination))
        return target_file.name
    except Exception:
        return None

def handle_visual_lesson_assignment(trade_id: str, asset: str, current_path: Optional[str] = "nan", suffix: str = "_VL") -> Optional[str]:
    import os
    import shutil
    import datetime
    from InquirerPy.base.control import Choice
    
    staging_dir = ".assets/staging"
    clean_asset = asset.replace("/", "_").replace(".", "_")
    account_prefix = ACTIVE_SESSION["name"].replace(" ", "_").lower() if ACTIVE_SESSION else "xauusdt_trading_main"
    permanent_dir = f".assets/permanent/{account_prefix}/{clean_asset}"
    
    os.makedirs(staging_dir, exist_ok=True)
    os.makedirs(permanent_dir, exist_ok=True)
    
    while True:
        valid_extensions = ('.png', '.jpg', '.jpeg')
        files = [f for f in os.listdir(staging_dir) if f.lower().endswith(valid_extensions)]
        
        choices = []
        if current_path and current_path != "nan":
            choices.append(Choice("keep", name=f"[ Keep Current File: {current_path} ]"))
            
        choices.append(Choice("auto_fetch", name=f"[ 🔄 Auto-Fetch recent {asset} screenshot from Downloads ]"))
            
        for f in files:
            choices.append(Choice(f, name=f))
            
        choices.append(Choice("skip", name="[ Skip Visual Lesson Assignment ]"))
        
        label_map = {
            "_NF": "Normal Fractal",
            "_IF": "Inverted Fractal",
            "_VL": "Visual Lesson"
        }
        prompt_label = label_map.get(suffix, "Visual Asset")

        prompt = inquirer.select(
            message=f"Select {prompt_label} image from staging folder >",
            choices=choices,
            pointer=">",
            qmark="",
            keybindings={"skip": []}
        )
        
        @prompt.register_kb("c-f")
        def _open_windows_viewer(event):
            import platform
            import os
            import subprocess
            abs_staging = os.path.abspath(staging_dir)
            if os.path.exists(abs_staging):
                try:
                    if platform.system() == "Windows":
                        os.startfile(abs_staging)
                    elif platform.system() == "Darwin":
                        subprocess.Popen(["open", abs_staging])
                    else:
                        try:
                            subprocess.Popen(["xdg-open", abs_staging], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except FileNotFoundError:
                            try:
                                subprocess.Popen(["gio", "open", abs_staging], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            except FileNotFoundError:
                                pass
                except Exception:
                    pass

        @prompt.register_kb("c-r")
        @prompt.register_kb("f5")
        def _reload_choices(event):
            event.app.exit(exception=ReloadChoicesException("Reload requested"))

        try:
            selected = bind_pause(prompt).execute()
        except ReloadChoicesException:
            try:
                from rich.console import Console
                Console().clear(home=True)
            except Exception:
                pass
            continue
        
        if selected == "keep":
            return current_path
            
        if selected == "auto_fetch":
            fetched_file = auto_fetch_tradingview_screenshot(asset)
            if fetched_file:
                print(f"\n[+] Successfully fetched and moved: {fetched_file}")
            else:
                print(f"\n[-] No recent screenshots found for {asset} in Downloads.")
            continue

        if selected == "skip":
            return "nan"
            
        ext = os.path.splitext(selected)[1]
        date_prefix = datetime.date.today().strftime("%Y%m%d")
        new_filename = f"{date_prefix}_{trade_id[:8]}{suffix}{ext}"
        
        staging_path = os.path.join(staging_dir, selected)
        perm_path = os.path.join(permanent_dir, new_filename)
        
        shutil.move(staging_path, perm_path)
        return perm_path

def determine_market_bias(i_cd: float) -> str:
    if abs(i_cd) < 0.26:
        return "Choppy / Neutral"
    elif i_cd >= 0.26:
        return "Bullish"
    else:
        return "Bearish"

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

def get_mandatory_datetime(prompt_text, allow_cancel=False):
    def validate_datetime(result):
        if not result: return False
        if allow_cancel and result.lower() == 'c': return True
        try:
            datetime.datetime.strptime(result, "%Y-%m-%d %H:%M")
            return True
        except ValueError:
            return False

    msg = f"{prompt_text} (YYYY-MM-DD HH:MM)"
    if allow_cancel:
        msg += " (or 'c' to cancel)"
    msg += " >"

    val = bind_pause(inquirer.text(
        message=msg,
        validate=validate_datetime,
        invalid_message="Must be in format YYYY-MM-DD HH:MM or 'c'",
        keybindings={"skip": []}
    )).execute()
    
    if allow_cancel and val.lower() == 'c':
        raise GoBackException("Cancelled by user")
    dt = datetime.datetime.strptime(val, "%Y-%m-%d %H:%M")
    return dt

def flow_flight_sessions():
    global ACTIVE_SESSION, ACTIVE_ENGINE
    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        sessions = FlightSessionManager.load_sessions()
        
        console.rule("[bold cyan]Flight Sessions Workspace[/bold cyan]")
        console.print()
        
        choices = [
            Choice("create", name="[+] Provision New Flight Session"),
            Choice("delete", name="[-] Delete Session"),
            Choice("back", name="[<] Back to Main Menu"),
            Separator()
        ]
        
        for s_id, s_data in sessions.items():
            name = s_data.get("name", "Unknown")
            last_p = s_data.get("last_played", "Unknown")
            choices.append(Choice(s_id, name=f"► {s_id} - {name} (Last Played: {last_p})"))
            
        choice = inquirer.select(
            message="Select Action >",
            choices=choices,
            pointer=">",
            qmark=""
        ).execute()
        
        if choice == "back":
            return
        elif choice == "create":
            account_idx = get_mandatory_text("Enter sequential account numeric index key (e.g. 002)")
            name = get_mandatory_text("Enter flight session nickname")
            session_id, session_data = FlightSessionManager.create_session(account_idx, name)
            ACTIVE_SESSION = {"id": session_id, "name": name, "db_name": session_data["db_name"]}
            
            from tools.database import copy_assets_to_current_db, init_db
            ACTIVE_ENGINE = init_db(f"sqlite:///.data/{session_data['db_name']}")
            copy_assets_to_current_db(ACTIVE_ENGINE)
            
            console.print(f"[green]Flight Session '{name}' created and loaded![/green]")
            input("Press Enter to continue...")
            return
        elif choice == "delete":
            if not sessions:
                console.print("[red]No sessions to delete.[/red]")
                input("Press Enter to continue...")
                continue
                
            del_choices = [Choice("cancel", name="Cancel")] + [Choice(s_id, name=f"{s_id} - {s_data['name']}") for s_id, s_data in sessions.items()]
            del_choice = inquirer.select(
                message="Select session to delete >",
                choices=del_choices,
                pointer=">",
                qmark=""
            ).execute()
            
            if del_choice != "cancel":
                from rich.panel import Panel
                warning_panel = Panel(
                    "WARNING: You are about to permanently erase this Flight Session database file from disk.\n"
                    "This action will completely destroy all unified analyses, efficiency audits, tactical\n"
                    "logs, and historical metadata stored within this session ledger. This cannot be undone.",
                    title="[bold red]CRITICAL WARNING[/bold red]",
                    border_style="bold red",
                    box=box.ROUNDED
                )
                console.print(warning_panel)
                
                confirm = inquirer.text(message="Type 'yes I am completely sure' to confirm session destruction:").execute()
                if confirm == "yes I am completely sure":
                    FlightSessionManager.delete_session(del_choice)
                    if ACTIVE_SESSION and ACTIVE_SESSION["id"] == del_choice:
                        ACTIVE_SESSION = None
                        ACTIVE_ENGINE = None
                    console.print("[green]Session deleted.[/green]")
                    input("Press Enter to continue...")
                else:
                    console.print("[yellow]Operation cancelled. Flight Session preserved.[/yellow]")
                    input("Press Enter to continue...")
        else:
            s_id = choice
            import re
            fallback_db = f"flight_account_{s_id}_{re.sub(r'[^a-zA-Z0-9]', '', sessions[s_id]['name']).lower()}.db"
            ACTIVE_SESSION = {"id": s_id, "name": sessions[s_id]["name"], "db_name": sessions[s_id].get("db_name", fallback_db)}
            FlightSessionManager.update_last_played(s_id)
            from tools.database import init_db
            ACTIVE_ENGINE = init_db(f"sqlite:///.data/{ACTIVE_SESSION['db_name']}")
            console.print(f"[green]Flight Session '{ACTIVE_SESSION['name']}' loaded![/green]")
            input("Press Enter to continue...")
            return

@click.group()
def cli():
    """B.L.A.S.T Interactive Engine"""
    get_active_engine()

@cli.command()
def start():
    """Main Menu"""
    global ACTIVE_SESSION
    
    state.active_session = ACTIVE_SESSION
    state.refresh_metrics(get_active_engine())
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
                            active_db_file = ACTIVE_SESSION["db_name"] if ACTIVE_SESSION else "flight_account_001_xauusd.db"
                            env_context = os.environ.copy()
                            env_context["BLAST_ACTIVE_DB"] = active_db_file
                            subprocess.Popen(["conda", "run", "-n", "blast_master", "env", "PYTHONPATH=.", "python", "tools/notion_sync.py"], env=env_context)
                            console.print("[green]Daemon started successfully![/green]")
                            input("Press Enter to continue...")
                        elif selected_choice == "5":
                            config_choice = inquirer.select(
                                message="Configuration >",
                                choices=[
                                    Choice("analysis_modification", name="[1] Analysis Modification"),
                                    Choice("assets_configuration", name="[2] Assets Configuration"),
                                    Choice("back", name="[3] Back to Main Menu")
                                ],
                                pointer=">",
                                qmark=""
                            ).execute()
                            
                            if config_choice == "analysis_modification":
                                mod_choice = inquirer.select(
                                    message="Analysis Modification >",
                                    choices=[
                                        Choice("add_backdated", name="[1] Add Backdated Analysis"),
                                        Choice("repair_analysis", name="[2] Repair executed analysis & audits"),
                                        Choice("back", name="[3] Back to Configuration Menu")
                                    ],
                                    pointer=">",
                                    qmark=""
                                ).execute()
                                
                                if mod_choice == "add_backdated":
                                    try:
                                        backdated_ts = get_mandatory_datetime("Enter Target Timestamp", allow_cancel=True)
                                        flow_new_analysis(backdated_timestamp=backdated_ts)
                                    except (GoBackException, PauseAuditException):
                                        continue
                                    except Exception as e:
                                        console.print(f"[bold red]Error adding backdated analysis: {e}[/bold red]")
                                        input("Press Enter to continue...")
                                elif mod_choice == "repair_analysis":
                                    flow_repair_analysis_audits()
                            elif config_choice == "assets_configuration":
                                try:
                                    flow_assets_configuration()
                                except Exception as e:
                                    console.print(f"[bold red]Error in assets configuration: {e}[/bold red]")
                        elif selected_choice == "t":
                            flow_flight_sessions()
                        elif selected_choice == "e":
                            ACTIVE_SESSION = None
                            state.active_session = None
                            ACTIVE_ENGINE = None
                            console.print("[yellow]Restoring Main Flight Account Connection...[/yellow]")
                            import time
                            time.sleep(1)
                        elif selected_choice == "x":
                            console.print("[yellow]Exiting B.L.A.S.T...[/yellow]")
                            break
                    finally:
                        state.active_session = ACTIVE_SESSION
                        state.refresh_metrics(get_active_engine())
                        
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
    import datetime
    import calendar
    from sqlalchemy.orm import Session
    
    # Unify Table Generation via LEFT JOINs
    # Rolling 10 items grid query
    rolling_query = """
    SELECT u.id, u.asset, u.market_bias, u.calc_edge, u.created_at, 
           e.bias_a, e.real_bias_b, e.resolution_type, 
           t.compliance, u.trade_status, u.is_backdated
    FROM unified_department u
    LEFT JOIN efficiency_audit e ON u.id = e.id
    LEFT JOIN tactical_audit t ON u.id = t.id
    ORDER BY u.created_at DESC LIMIT 10;
    """

    def format_row_value(val, is_bias=False, is_compliance=False, is_status=False, is_edge=False):
        if val is None or val == "":
            return "[yellow]Pending[/yellow]"
        val_str = str(val)
        if is_edge:
            try:
                edge_val = float(val)
                edge_style = "bold green" if edge_val >= 0.26 else "bold red" if edge_val <= -0.26 else "bold yellow"
                return f"[{edge_style}]{edge_val:.4f}[/{edge_style}]"
            except ValueError:
                return f"[yellow]{val_str}[/yellow]"
        if is_bias:
            if val_str in ["Bullish", "Long", "BOS", "CHOCH", "Validated Range Expansion", "Trend Reversal"]:
                bias_style = "bold green"
            elif val_str in ["Bearish", "Short"]:
                bias_style = "bold red"
            else:
                bias_style = "bold yellow"
            return f"[{bias_style}]{val_str}[/{bias_style}]"
        if is_compliance:
            comp_style = "bold green" if "Edge_valid" in val_str else "bold red" if val_str in ["Invalid_edge", "No_edge"] else "bold yellow"
            return f"[{comp_style}]{val_str}[/{comp_style}]"
        if is_status:
            status_style = "bold green" if "good" in val_str.lower() else "bold red" if "bad" in val_str.lower() else "bold yellow"
            return f"[{status_style}]{val_str}[/{status_style}]"
        return val_str

    def render_ledger_table(rows):
        SHORTHANDS = {
            "Choppy / Neutral": "Neutral",
            "Confirmed (A equal to B)": "Conf (A=B)",
            "Invalidated (B not equal to A)": "Inval (B!=A)",
            "Overlap Invalidated (New Bias before resolution)": "Overlap Inval",
            "Trade_taken_bad_execution": "Taken (Bad Ex)",
            "Trade_taken_good_execution": "Taken (Good Ex)",
            "Trade_not_taken_valid_edge": "Not Taken (Valid)"
        }

        # Configuración inmutable de geometría de tabla
        table = Table(
            box=box.ROUNDED, 
            border_style="magenta", 
            expand=True,
            min_width=110,  # Evita el colapso destructivo en terminales pequeñas
            pad_edge=False
        )
        
        # Asignación de anchos fijos explícitos para columnas críticas izquierdas
        table.add_column("#", justify="center", width=4, no_wrap=True)
        table.add_column("Short ID", justify="center", style="cyan", width=14, no_wrap=True)
        table.add_column("Asset", justify="center", style="bold white", width=10, no_wrap=True)
        
        # Columnas de métricas y estados con anchos proporcionales mínimos
        table.add_column("Market Bias", justify="center", width=11, no_wrap=True)
        table.add_column("Calc Edge", justify="right", width=9, no_wrap=True)
        table.add_column("Created At", justify="center", style="dim cyan", width=12, no_wrap=True)
        table.add_column("Bias A", justify="center", width=11, no_wrap=True)
        table.add_column("Real Bias B", justify="center", width=11, no_wrap=True)
        table.add_column("Resolution Type", justify="center", width=14, no_wrap=True)
        table.add_column("Compliance", justify="center", width=13, no_wrap=True)
        table.add_column("Trade Status", justify="center", width=14, no_wrap=True)
        
        for idx, row in enumerate(rows):
            r_id, asset, market_bias, calc_edge, created_at, bias_a, real_bias_b, resolution_type, compliance, trade_status, is_backdated = row
            
            market_bias = SHORTHANDS.get(market_bias, market_bias)
            bias_a = SHORTHANDS.get(bias_a, bias_a)
            real_bias_b = SHORTHANDS.get(real_bias_b, real_bias_b)
            resolution_type = SHORTHANDS.get(resolution_type, resolution_type)
            compliance = SHORTHANDS.get(compliance, compliance)
            trade_status = SHORTHANDS.get(trade_status, trade_status)
            
            if isinstance(created_at, datetime.datetime):
                created_display = to_local_display(created_at, '%m/%d %H:%M')
            else:
                created_display = str(created_at)[5:16]
            created_str = f"[dim cyan]{created_display}[/dim cyan]"
            
            badge = " [bold yellow](B)[/bold yellow]" if is_backdated else ""
            table.add_row(
                str(idx + 1),
                f"{r_id[:8]}{badge}",
                asset,
                format_row_value(market_bias, is_bias=True),
                format_row_value(calc_edge, is_edge=True),
                created_str,
                format_row_value(bias_a, is_bias=True),
                format_row_value(real_bias_b, is_bias=True),
                format_row_value(resolution_type, is_bias=True),
                format_row_value(compliance, is_compliance=True),
                format_row_value(trade_status, is_status=True)
            )
        return table

    def format_json_field(val):
        if not val:
            return "[yellow]Pending[/yellow]"
        try:
            import json
            if isinstance(val, str):
                lst = json.loads(val)
            else:
                lst = val
            if isinstance(lst, list):
                return ", ".join(str(x) for x in lst)
            return str(lst)
        except Exception:
            return str(val)

    def show_unified_detail(selected_id, raw_conn):
        # ── Visual Helper Functions ──
        def render_bar(value, max_val, width=20, fill_char="█", empty_char="░"):
            if max_val <= 0 or value is None:
                return empty_char * width
            ratio = min(max(float(value) / float(max_val), 0.0), 1.0)
            filled = int(ratio * width)
            return fill_char * filled + empty_char * (width - filled)

        def render_scale_bar(value, max_val=5, width=10):
            if value is None:
                return "░" * width + "  -/5"
            v = int(value)
            return render_bar(v, max_val, width) + f"  {v}/{max_val}"

        def render_prob_bar(probability, width=28):
            if probability is None:
                return "░" * width + "   -.-%"
            pct = float(probability) * 100
            return render_bar(probability, 1.0, width) + f"  {pct:5.1f}%"

        def render_edge_gauge(edge_val, width=44):
            e = float(edge_val)
            norm = (e + 1.0) / 2.0
            pos = int(norm * (width - 1))
            pos = max(0, min(pos, width - 1))
            mark_low = int((-0.26 + 1.0) / 2.0 * (width - 1))
            mark_high = int((0.26 + 1.0) / 2.0 * (width - 1))
            gauge = list("─" * width)
            gauge[mark_low] = "┊"
            gauge[mark_high] = "┊"
            gauge[width // 2] = "┃"
            gauge[pos] = "●"
            label = f"  [{'+' if e >= 0 else ''}{e:.2f}]"
            return "".join(gauge) + label

        GATE_LABELS = {
            "g1": "15m Trend Alignment",
            "g2": "Fractal Trend Confirmed",
            "g3": "Limit Order Placed",
            "g4": "Breathing Protocol Executed",
            "g5": "Manual Cooldown (5-min wait)",
            "g6": "Stop-Loss Price Validated",
            "g7": "Take-Profit Price Validated"
        }

        GATE_FIELDS = [
            "g1_trend_15m", "g2_fractal_trend", "g3_limit_order", "g4_breathing",
            "g5_manual_cooldown", "g6_sl_validated", "g7_tp_validated"
        ]

        CONF_LABELS = {
            "c1": "KL as Support/Resistance",
            "c2": "Fractal Std (5-10-15m)",
            "c3": "Fractal 1M",
            "c4": "Fractal 1H (Cont/Inflection)",
            "c5": "KL as Target",
            "c6": "Liquidity Grabbed",
            "c7": "Retracement 0.4-0.6",
            "c8": "15M Convergence"
        }

        CONF_FIELDS = [
            "c1_kl_support", "c2_fractal_std", "c3_fractal_1m", "c4_fractal_1h",
            "c5_kl_target", "c6_liquidity", "c7_retracement", "c8_convergence_15m"
        ]

        # ── Extended Detail Query ──
        detail_query = """
        SELECT u.id, u.asset, u.market_bias, u.calc_edge, u.created_at, u.updated_at, u.edge_description, u.trade_status,
               u.p4_hierarchy, u.p1_timeframe, u.p1_type, u.nodes_l1, u.nodes_l2, u.tactical_classification,
               u.long_prob, u.short_prob, u.no_trade_prob, u.is_backdated, u.edge_validation_price, u.structural_invalidation,
               e.bias_a, e.resolution_type, e.real_bias_b, e.structural_resolution, e.failure_reason,
               e.specific_bias_compliance, e.false_regime_rate, e.lesson_learned as e_lesson, e.efficiency_timeframe,
               t.compliance, t.entry_time, t.exit_time, t.tier_setup, t.market_state, t.exit_type,
               t.followed_plan, t.primary_emotion, t.setup_type, t.htf_trend_context, t.ltf_trend_context,
               t.confirmation_status, t.anxiety_level, t.impatience_level, t.mental_clarity_level,
               t.emotions, t.behavioral_errors, t.cognitive_patterns, t.size, t.entry_price,
               t.closing_price, t.could_hit_tp, t.take_profit, t.stop_loss, t.pnl_and_cost,
               t.mae_adverse, t.captured_mae, t.mfe_favorable, t.notional_size, t.capital_at_risk,
               t.lesson_learned as t_lesson, t.session, t.visual_lesson_path, t.confirmation_5m_15m, t.confirmation_params,
               t.gates_failed, t.confirmations_count, t.mfe_potencial_estimado,
               t.g1_trend_15m, t.g2_fractal_trend, t.g3_limit_order, t.g4_breathing,
               t.g5_manual_cooldown, t.g6_sl_validated, t.g7_tp_validated,
               t.c1_kl_support, t.c2_fractal_std, t.c3_fractal_1m, t.c4_fractal_1h,
               t.c5_kl_target, t.c6_liquidity, t.c7_retracement, t.c8_convergence_15m,
               t.pre_trade_emotions, t.mid_trade_emotions, t.post_trade_emotions,
               al.department, al.layer_name, al.direction, al.strength, al.thesis
        FROM unified_department u
        LEFT JOIN efficiency_audit e ON u.id = e.id
        LEFT JOIN tactical_audit t ON u.id = t.id
        LEFT JOIN analysis_layer al ON u.id = al.trade_id
        WHERE u.id = :selected_id;
        """
        cursor = db_session.execute(text(detail_query), {"selected_id": selected_id})
        detail_rows = cursor.fetchall()
        
        if not detail_rows:
            console.print("[bold red]Analysis record not found![/bold red]")
            input("Press Enter to continue...")
            return
            
        cols = [
            "id", "asset", "market_bias", "calc_edge", "created_at", "updated_at", "edge_description", "trade_status",
            "p4_hierarchy", "p1_timeframe", "p1_type", "nodes_l1", "nodes_l2", "tactical_classification",
            "long_prob", "short_prob", "no_trade_prob", "is_backdated", "edge_validation_price", "structural_invalidation",
            "bias_a", "resolution_type", "real_bias_b", "structural_resolution", "failure_reason",
            "specific_bias_compliance", "false_regime_rate", "e_lesson", "efficiency_timeframe",
            "compliance", "entry_time", "exit_time", "tier_setup", "market_state", "exit_type",
            "followed_plan", "primary_emotion", "setup_type", "htf_trend_context", "ltf_trend_context",
            "confirmation_status", "anxiety_level", "impatience_level", "mental_clarity_level",
            "emotions", "behavioral_errors", "cognitive_patterns", "size", "entry_price",
            "closing_price", "could_hit_tp", "take_profit", "stop_loss", "pnl_and_cost",
            "mae_adverse", "captured_mae", "mfe_favorable", "notional_size", "capital_at_risk",
            "t_lesson", "session", "visual_lesson_path", "confirmation_5m_15m", "confirmation_params",
            "gates_failed", "confirmations_count", "mfe_potencial_estimado",
            "g1_trend_15m", "g2_fractal_trend", "g3_limit_order", "g4_breathing",
            "g5_manual_cooldown", "g6_sl_validated", "g7_tp_validated",
            "c1_kl_support", "c2_fractal_std", "c3_fractal_1m", "c4_fractal_1h",
            "c5_kl_target", "c6_liquidity", "c7_retracement", "c8_convergence_15m",
            "pre_trade_emotions", "mid_trade_emotions", "post_trade_emotions"
        ]
        
        record = {}
        for i, col in enumerate(cols):
            record[col] = detail_rows[0][i]
            
        # Extract layers
        layers_dict = {}
        for row in detail_rows:
            l_dept = row[-5]
            l_name = row[-4]
            l_dir = row[-3]
            l_str = row[-2]
            l_thesis = row[-1]
            if l_name:
                layers_dict[l_name] = {
                    "department": l_dept,
                    "direction": l_dir,
                    "strength": l_str,
                    "thesis": l_thesis
                }

        # Perform programmatic metric calculations in Python
        ep = Decimal(str(record["entry_price"])) if record["entry_price"] else Decimal('0.0')
        cp = Decimal(str(record["closing_price"])) if record["closing_price"] else Decimal('0.0')
        sl = Decimal(str(record["stop_loss"])) if record["stop_loss"] else Decimal('0.0')
        tp = Decimal(str(record["take_profit"])) if record["take_profit"] else Decimal('0.0')
        size = Decimal(str(record["size"])) if record["size"] else Decimal('0.0')
        mfe = Decimal(str(record["mfe_favorable"])) if record["mfe_favorable"] else Decimal('0.0')
        mae = Decimal(str(record["mae_adverse"])) if record["mae_adverse"] else Decimal('0.0')
        
        record["trade_decision"] = None
        record["notional_size_usd"] = None
        record["risk_usd"] = None
        record["dist_to_sl"] = None
        record["dist_to_tp"] = None
        record["r_r"] = None
        record["pnl"] = None
        record["cost"] = Decimal('0.0')
        record["r_multiple"] = None
        record["captured_mfe"] = None
        record["trade_duration"] = None
        
        if ep != Decimal('0.0') and sl != Decimal('0.0') and size != Decimal('0.0'):
            td = "Long" if ep > sl else "Short"
            record["trade_decision"] = td
            record["notional_size_usd"] = ep * size
            record["risk_usd"] = size * abs(ep - sl)
            
            if td == "Long":
                record["capital_at_risk"] = size * (ep - sl)
            else:
                record["capital_at_risk"] = size * (sl - ep)
                
            if ep != Decimal('0.0'):
                record["dist_to_sl"] = abs(ep - sl) / ep
            else:
                record["dist_to_sl"] = Decimal('0.0')
                
            if tp != Decimal('0.0') and ep != Decimal('0.0'):
                record["dist_to_tp"] = abs(ep - tp) / ep
                sl_dist_abs = abs(ep - sl)
                if sl_dist_abs != Decimal('0.0'):
                    if td == "Long":
                        record["r_r"] = (tp - ep) / sl_dist_abs
                    else:
                        record["r_r"] = (ep - tp) / sl_dist_abs
                else:
                    record["r_r"] = Decimal('0.0')
            
            if cp != Decimal('0.0'):
                if td == "Long":
                    record["pnl"] = (cp - ep) * size
                else:
                    record["pnl"] = (ep - cp) * size
                    
                if record["pnl_and_cost"] is not None:
                    record["cost"] = record["pnl"] - Decimal(str(record["pnl_and_cost"]))
                else:
                    record["cost"] = Decimal('0.0')
                    
                sl_dist_abs = abs(ep - sl)
                if sl_dist_abs != Decimal('0.0'):
                    if td == "Long":
                        record["r_multiple"] = (cp - ep) / sl_dist_abs
                    else:
                        record["r_multiple"] = (ep - cp) / sl_dist_abs
                else:
                    record["r_multiple"] = Decimal('0.0')
                    
                if record["r_multiple"] < Decimal('0.0') or mfe == Decimal('0.0'):
                    record["captured_mfe"] = Decimal('0.0')
                else:
                    record["captured_mfe"] = record["r_multiple"] / mfe

                if record["r_multiple"] < Decimal('0.0') and mae > Decimal('0.0'):
                    record["captured_mae"] = abs(record["r_multiple"]) / mae
                else:
                    record["captured_mae"] = Decimal('0.0')

        # Map mae_adverse to mae and mfe_favorable to mfe for display compatibility
        record["mae"] = record["mae_adverse"]
        record["mfe"] = record["mfe_favorable"]
        
        # Format entry and exit times to calculate duration
        if record["entry_time"] and record["exit_time"]:
            try:
                if isinstance(record["entry_time"], str):
                    ent_dt = datetime.datetime.fromisoformat(record["entry_time"])
                else:
                    ent_dt = record["entry_time"]
                if isinstance(record["exit_time"], str):
                    ext_dt = datetime.datetime.fromisoformat(record["exit_time"])
                else:
                    ext_dt = record["exit_time"]
                delta = ext_dt - ent_dt
                tot_sec = int(delta.total_seconds())
                hours = tot_sec // 3600
                minutes = (tot_sec % 3600) // 60
                record["trade_duration"] = f"{hours}h {minutes}m"
            except Exception:
                record["trade_duration"] = "N/A"
        else:
            record["trade_duration"] = "N/A"

        # ── Panel 1: Structural Vector Analysis ──
        struct_text = Text()

        # Header row
        struct_text.append("Asset: ", style="dim")
        struct_text.append(f"{record['asset']}", style="bold white")
        struct_text.append(" │ ID: ", style="dim")
        struct_text.append(f"{record['id'][:8]}", style="bold cyan")
        if record.get('is_backdated'):
            struct_text.append(" [BACKDATED]", style="bold yellow")
        else:
            struct_text.append(" [ORIGINAL]", style="bold green")
        created_str = to_local_display(record['created_at']) if isinstance(record['created_at'], datetime.datetime) else str(record['created_at'])
        struct_text.append(" │ ", style="dim")
        struct_text.append(Text.from_markup(f"[dim cyan]{created_str}[/dim cyan]\n\n"))

        # Market Bias + Edge Gauge
        struct_text.append("Market Bias: ", style="dim")
        bias_style = "bold green" if record['market_bias'] == "Bullish" else "bold red" if record['market_bias'] == "Bearish" else "bold yellow"
        struct_text.append(f"{record['market_bias']}", style=bias_style)
        edge_val = float(record['calc_edge'])
        edge_style = "bold green" if edge_val >= 0.26 else "bold red" if edge_val <= -0.26 else "bold yellow"
        struct_text.append("          I_CD (Edge): ", style="dim")
        struct_text.append(f"{edge_val:+.4f}\n", style=edge_style)
        gauge = render_edge_gauge(edge_val, width=44)
        struct_text.append(f"                              {gauge}\n", style="dim")
        struct_text.append("                          -1.0   -0.26  0  +0.26   +1.0\n\n", style="dim")

        # P-Layer Table
        def get_dir_weight(d):
            if d == "Long": return 1
            if d == "Short": return -1
            return 0
        def get_str_weight(s):
            if s == "Strong": return 2
            if s == "Mid": return 1
            return 0

        p_weights = {"P0": 0.30, "P1": 0.25, "P2": 0.15, "P3": 0.10, "P4": 0.20}
        formula_parts = []

        struct_text.append("  Layer │ Direction │ Strength │ Score │ Thesis\n", style="bold white")
        struct_text.append("  ──────┼───────────┼──────────┼───────┼─────────────────────────────\n", style="dim")

        for layer_name in ["P0", "P2", "P3", "P4", "P1"]:
            l = layers_dict.get(layer_name)
            d_val = l["direction"] if l else "N/A"
            s_val = l["strength"] if l else "N/A"
            score = get_dir_weight(d_val) * get_str_weight(s_val) if l else 0
            formula_parts.append((layer_name, p_weights.get(layer_name, 0), score))

            d_style = "bold green" if d_val == "Long" else "bold red" if d_val == "Short" else "bold yellow"
            thesis_raw = l.get("thesis", "") if l else ""
            # Truncate thesis for table row
            if thesis_raw:
                if layer_name == "P1":
                    try:
                        p1_data = json.loads(thesis_raw) if thesis_raw else {}
                        if isinstance(p1_data, dict):
                            thesis_short = f"NF: ...{str(p1_data.get('normal_fractal', 'N/A'))[-18:]} │ IF: ...{str(p1_data.get('inverted_fractal', 'N/A'))[-18:]}"
                        else:
                            thesis_short = str(thesis_raw)[:45]
                    except Exception:
                        thesis_short = str(thesis_raw)[:45]
                else:
                    first_line = str(thesis_raw).split('\n')[0]
                    thesis_short = first_line[:45] + ("…" if len(first_line) > 45 else "")
            else:
                thesis_short = "N/A"

            struct_text.append(f"  {layer_name:<5} │ ", style="dim")
            struct_text.append(f"{d_val:<9}", style=d_style)
            struct_text.append(f" │ {s_val:<8} │ {score:+4d}  │ ", style="dim")
            struct_text.append(f"{thesis_short}\n", style="dim italic")

        struct_text.append("\n")

        # I_CD Formula Breakdown
        numerator_str = " + ".join([f"{w:.2f}×{s:+d}" for name, w, s in formula_parts])
        struct_text.append(f"  I_CD = ({numerator_str}) / 2.0 = {edge_val:+.4f}\n\n", style="dim")

        # Directional Probabilities with bars
        struct_text.append("  Directional Probabilities:\n", style="bold white")
        long_p = float(record['long_prob'])
        short_p = float(record['short_prob'])
        no_trade_p = float(record['no_trade_prob'])
        struct_text.append(f"  Long:      {render_prob_bar(long_p)}\n", style="green")
        struct_text.append(f"  Short:     {render_prob_bar(short_p)}\n", style="red")
        struct_text.append(f"  No-Trade:  {render_prob_bar(no_trade_p)}\n\n", style="yellow")

        # Parametric Metrics
        evp_val = str(record['edge_validation_price']) if record['edge_validation_price'] is not None else "N/A"
        si_val = str(record['structural_invalidation']) if record['structural_invalidation'] is not None else "N/A"
        struct_text.append("  Edge Validation Price: ", style="dim")
        struct_text.append(f"{evp_val}", style="bold white")
        struct_text.append("    Structural Invalidation: ", style="dim")
        struct_text.append(f"{si_val}\n", style="bold white")

        # Tactical Classification Metadata
        struct_text.append("  Hierarchy: ", style="dim")
        struct_text.append(f"{record['p4_hierarchy']}", style="bold white")
        struct_text.append("      Tactical Class: ", style="dim")
        struct_text.append(f"{record['tactical_classification']}\n", style="bold cyan")
        struct_text.append("  P1 Timeframe: ", style="dim")
        struct_text.append(f"{record['p1_timeframe']}", style="bold white")
        struct_text.append("           Fractal Type: ", style="dim")
        struct_text.append(f"{record['p1_type']}\n", style="bold white")
        struct_text.append("  Nodes L1 / L2: ", style="dim")
        struct_text.append(f"{record['nodes_l1']} / {record['nodes_l2']}\n", style="bold white")

        # Edge Description
        if record['edge_description']:
            indented_desc = format_indented_block(record['edge_description'], indent_spaces=4, first_line_flush=False, wrap_width=80)
            struct_text.append(f"\n  Edge Description:\n{indented_desc}\n", style="italic white")

        # Full thesis detail rendering (below table for deep review)
        struct_text.append("\n")
        for layer_name in ["P0", "P2", "P3", "P4", "P1"]:
            l = layers_dict.get(layer_name)
            if l and l.get("thesis"):
                thesis_raw = l["thesis"]
                if layer_name == "P1":
                    try:
                        p1_data = json.loads(thesis_raw) if thesis_raw else {}
                        if isinstance(p1_data, dict) and p1_data:
                            struct_text.append(f"  {layer_name} Detail:\n", style="bold cyan")
                            struct_text.append(f"    Normal Fractal:   {p1_data.get('normal_fractal', 'None')}\n", style="dim cyan")
                            struct_text.append(f"    Inverted Fractal: {p1_data.get('inverted_fractal', 'None')}\n", style="dim cyan")
                        else:
                            raise ValueError()
                    except Exception:
                        indented_thesis = format_indented_block(thesis_raw, indent_spaces=4, first_line_flush=False, wrap_width=80)
                        struct_text.append(f"  {layer_name} Thesis:\n{indented_thesis}\n", style="dim italic")
                else:
                    indented_thesis = format_indented_block(thesis_raw, indent_spaces=4, first_line_flush=False, wrap_width=80)
                    struct_text.append(f"  {layer_name} Thesis:\n{indented_thesis}\n", style="dim italic")

        dashboard = Panel(
            struct_text,
            title=f"[white]Structural Vector Analysis: {record['asset']} - {to_local_display(record['created_at'], '%Y-%m-%d %H:%M:%S')}[/white]",
            border_style="bold blue",
            box=box.DOUBLE
        )

        # ── Panel 2: Efficiency Audit ──
        eff_text = Text()
        if record["bias_a"] is None:
            eff_text.append("\n  [ Pending Audit ]\n\n", style="bold yellow")
        else:
            bias_a_val = str(record['bias_a'])
            bias_b_val = str(record['real_bias_b']) if record['real_bias_b'] else "Pending"
            res_type = str(record['resolution_type']) if record['resolution_type'] else "Open"

            # Bias Flow Visualization
            is_confirmed = "Confirmed" in res_type
            is_invalidated = "Invalidated" in res_type
            flow_symbol = "[bold green]✓[/bold green]" if is_confirmed else "[bold red]✗[/bold red]" if is_invalidated else "[bold yellow]◌[/bold yellow]"
            eff_text.append("  Bias Flow:   ", style="dim")
            eff_text.append(f"{bias_a_val}", style="bold white")
            eff_text.append("  ─────→  ", style="dim")
            eff_text.append(f"{bias_b_val}", style="bold white")
            eff_text.append("          ")
            eff_text.append(Text.from_markup(f"{flow_symbol} "))
            eff_text.append(Text.from_markup(f"{format_row_value(res_type, is_bias=True)}\n"))
            eff_text.append("               (A)                (B)\n\n", style="dim")

            eff_text.append("  Resolution Type:       ", style="dim")
            eff_text.append(Text.from_markup(f"{format_row_value(record['resolution_type'], is_bias=True)}\n"))
            eff_text.append("  Structural Resolution: ", style="dim")
            eff_text.append(f"{record['structural_resolution']}\n", style="bold white")
            eff_text.append("  Efficiency Timeframe:  ", style="dim")
            eff_text.append(f"{record['efficiency_timeframe']}\n", style="bold white")
            eff_text.append("  Failure Reason:        ", style="dim")
            eff_text.append(f"{record['failure_reason']}\n\n", style="bold white")

            eff_text.append("  ── Auto-Calculated Metrics ──\n", style="bold cyan")
            eff_text.append("  Bias Compliance:  ", style="dim")
            comp_style = "bold green" if record['specific_bias_compliance'] == "Valid" else "bold red"
            eff_text.append(f"{record['specific_bias_compliance']}", style=comp_style)
            eff_text.append("        Regime Rate: ", style="dim")
            rr_val = str(record['false_regime_rate'])
            rr_style = "bold green" if "True Positive" in rr_val or "True Negative" in rr_val else "bold red" if "False" in rr_val else "bold yellow"
            eff_text.append(f"{rr_val}\n", style=rr_style)

            if record['e_lesson']:
                indented_lesson = format_indented_block(record['e_lesson'], indent_spaces=4, first_line_flush=False, wrap_width=80)
                eff_text.append(f"\n  Lesson Learned:\n{indented_lesson}\n", style="italic white")

        eff_panel = Panel(
            eff_text,
            title="[bold cyan]Efficiency Audit[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED
        )

        # ── Panel 3: Tactical Execution ──
        tact_text = Text()
        if record["compliance"] is None:
            tact_text.append("\n  [ Pending Audit ]\n\n", style="bold yellow")
        else:
            def fmt_f(val, p=2):
                if val is None:
                    return "N/A"
                try:
                    return f"{float(val):.{p}f}"
                except (ValueError, TypeError):
                    return str(val)

            # Header row
            td = record.get("trade_decision", "N/A")
            td_style = "bold green" if td == "Long" else "bold red" if td == "Short" else "bold white"
            tact_text.append("  Decision: ", style="dim")
            tact_text.append(f"{td}", style=td_style)
            tact_text.append("    │  Compliance: ", style="dim")
            tact_text.append(Text.from_markup(f"{format_row_value(record['compliance'], is_compliance=True)}"))
            tact_text.append("    │  Status: ", style="dim")
            tact_text.append(Text.from_markup(f"{format_row_value(record['trade_status'], is_status=True)}\n\n"))

            # Trade Timing
            tact_text.append("  ── Trade Timing ──\n", style="bold white")
            entry_str = to_local_display(record['entry_time']) if isinstance(record['entry_time'], datetime.datetime) else str(record['entry_time'])
            exit_str = to_local_display(record['exit_time']) if isinstance(record['exit_time'], datetime.datetime) else str(record['exit_time'])
            tact_text.append("  Entry: ", style="dim")
            tact_text.append(Text.from_markup(f"[dim cyan]{entry_str}[/dim cyan]"))
            tact_text.append("    Exit: ", style="dim")
            tact_text.append(Text.from_markup(f"[dim cyan]{exit_str}[/dim cyan]"))
            tact_text.append(f"    Duration: {record['trade_duration']}\n", style="dim")
            tact_text.append(f"  Session: {record['session']}\n\n", style="dim")

            # Position Sizing
            tact_text.append("  ── Position Sizing ──\n", style="bold white")
            tact_text.append(f"  Size: {record['size']}          Entry Price: {record['entry_price']}     Closing Price: {record['closing_price']}\n", style="dim")
            tact_text.append(f"  Stop Loss: {record['stop_loss']}    Take Profit: {record['take_profit']}    Could Hit TP: {record['could_hit_tp']}\n\n", style="dim")

            # Risk Metrics
            tact_text.append("  ── Risk Metrics ──\n", style="bold white")
            tact_text.append(f"  Notional (USD): ${fmt_f(record['notional_size_usd'])}        Risk (USD): ${fmt_f(record['risk_usd'])}\n", style="dim")
            tact_text.append(f"  Capital @ Risk: ${fmt_f(record['capital_at_risk'])}        ", style="dim")
            tact_text.append(f"Dist → SL: {fmt_f(record.get('dist_to_sl'), 4)}    Dist → TP: {fmt_f(record.get('dist_to_tp'), 4)}\n\n", style="dim")

            # P&L
            tact_text.append("  ── P&L ──\n", style="bold white")
            pnl_style = "bold green" if record['pnl'] is not None and record['pnl'] >= 0 else "bold red"
            tact_text.append("  PnL: ", style="dim")
            tact_text.append(Text.from_markup(f"[{pnl_style}]${fmt_f(record['pnl'], 4)}[/{pnl_style}]"))
            tact_text.append(f"       Cost: ${fmt_f(record['cost'], 4)}       ", style="dim")
            tact_text.append("PnL & Cost: ", style="dim")
            tact_text.append(Text.from_markup(f"[{pnl_style}]${fmt_f(record['pnl_and_cost'], 4)}[/{pnl_style}]\n"))
            r_mult_style = "bold green" if record['r_multiple'] is not None and record['r_multiple'] >= 0 else "bold red"
            tact_text.append("  R:R Ratio: ", style="dim")
            tact_text.append(f"{fmt_f(record['r_r'])}", style="bold white")
            tact_text.append("       R-Multiple: ", style="dim")
            tact_text.append(Text.from_markup(f"[{r_mult_style}]{fmt_f(record['r_multiple'])}[/{r_mult_style}]\n\n"))

            # Efficiency Capture Bars
            tact_text.append("  ── Efficiency Capture ──\n", style="bold white")
            mfe_raw = float(record['mfe']) if record.get('mfe') else 0.0
            cap_mfe_val = float(record['captured_mfe']) if record.get('captured_mfe') else 0.0
            cap_mae_val = float(record['captured_mae']) if record.get('captured_mae') else 0.0
            cap_mfe_pct = cap_mfe_val * 100 if cap_mfe_val <= 1.0 else cap_mfe_val
            cap_mae_pct = cap_mae_val * 100 if cap_mae_val <= 1.0 else cap_mae_val
            tact_text.append(f"  MFE Capture:  {render_bar(cap_mfe_val, 1.0, 28)}  {cap_mfe_pct:5.1f}% of {mfe_raw:.2f}R\n", style="green")
            tact_text.append(f"  MAE Exposure: {render_bar(cap_mae_val, 1.0, 28)}  {cap_mae_pct:5.1f}%\n", style="red")
            tact_text.append(f"  Raw MAE: {record.get('mae', 'N/A')}R     Raw MFE: {record.get('mfe', 'N/A')}R\n", style="dim")
            tact_text.append(f"  Captured MAE: {fmt_f(record.get('captured_mae'))}  Captured MFE: {fmt_f(record.get('captured_mfe'))}\n\n", style="dim")

            # Setup Context
            tact_text.append("  ── Setup Context ──\n", style="bold white")
            tact_text.append(f"  Tier: {record['tier_setup']} │ Market: {record['market_state']} │ Setup: {record['setup_type']} │ Exit: {record['exit_type']}\n", style="dim")
            tact_text.append(f"  HTF Trend: {record['htf_trend_context']}       LTF Trend: {record['ltf_trend_context']}\n", style="dim")
            tact_text.append(f"  Confirmation: {record['confirmation_status']}\n", style="dim")
            tact_text.append(f"  5m/15m Confirmation: {record.get('confirmation_5m_15m', 'N/A')}\n", style="dim")
            tact_text.append("  Followed Plan: ", style="dim")
            tact_text.append(f"{record.get('followed_plan')}\n", style="bold white")

        tact_panel = Panel(
            tact_text,
            title="[bold magenta]Tactical Execution[/bold magenta]",
            border_style="magenta",
            box=box.ROUNDED
        )

        # ── Panel 4: Execution Framework (Motor B) ──
        fw_text = Text()
        if record["compliance"] is None:
            fw_text.append("\n  [ Pending Audit ]\n\n", style="bold yellow")
        else:
            gates_failed_val = record.get("gates_failed", 0) or 0
            confs_count_val = record.get("confirmations_count", 0) or 0

            gf_style = "bold red" if gates_failed_val > 0 else "bold green"
            fw_text.append("  ── GATES (Pre-Entry Checklist) ──", style="bold white")
            fw_text.append("                            Failed: ", style="dim")
            fw_text.append(Text.from_markup(f"[{gf_style}]{gates_failed_val}[/{gf_style}]\n"))

            for gate_key, gate_label in GATE_LABELS.items():
                field_idx = int(gate_key[1]) - 1
                field_name = GATE_FIELDS[field_idx]
                passed = bool(record.get(field_name, False))
                icon = "[bold green]✓[/bold green]" if passed else "[bold red]✗[/bold red]"
                fw_text.append(Text.from_markup(f"  [{icon}] {gate_key.upper()}: {gate_label}\n"))

            fw_text.append(f"\n  ── CONFIRMATIONS (Signal Quality) ──", style="bold white")
            fw_text.append(f"                   Count: {confs_count_val}/8\n", style="dim")

            # Render confirmations in 2 columns: c1-c4 left, c5-c8 right
            for i in range(4):
                left_key = f"c{i+1}"
                right_key = f"c{i+5}"
                left_field = CONF_FIELDS[i]
                right_field = CONF_FIELDS[i + 4]
                left_passed = bool(record.get(left_field, False))
                right_passed = bool(record.get(right_field, False))
                left_icon = "[bold green]✓[/bold green]" if left_passed else "[bold red]✗[/bold red]"
                right_icon = "[bold green]✓[/bold green]" if right_passed else "[bold red]✗[/bold red]"
                left_label = CONF_LABELS[left_key]
                right_label = CONF_LABELS[right_key]
                fw_text.append(Text.from_markup(f"  [{left_icon}] {left_key.upper()}: {left_label:<30}  [{right_icon}] {right_key.upper()}: {right_label}\n"))

            # Confirmation Params
            raw_cp = record.get("confirmation_params")
            if raw_cp:
                try:
                    cp_list = json.loads(raw_cp) if isinstance(raw_cp, str) else (raw_cp or [])
                    cp_str = ", ".join(str(x) for x in cp_list) if isinstance(cp_list, list) else str(cp_list)
                except Exception:
                    cp_str = str(raw_cp)
                indented_cp = format_indented_block(cp_str, indent_spaces=4, first_line_flush=False, wrap_width=80)
                fw_text.append(f"\n  Confirmation Parameters:\n{indented_cp}\n", style="dim")

            mfe_pot = record.get("mfe_potencial_estimado")
            if mfe_pot is not None:
                fw_text.append("\n  MFE Potencial Estimado (S6): ", style="dim")
                fw_text.append(f"{mfe_pot}R\n", style="bold yellow")

        fw_panel = Panel(
            fw_text,
            title="[bold green]Execution Framework (Motor B)[/bold green]",
            border_style="green",
            box=box.ROUNDED
        )

        # ── Panel 5: Psychological & Cognitive Profile ──
        psych_text = Text()
        if record["compliance"] is None:
            psych_text.append("\n  [ Pending Audit ]\n\n", style="bold yellow")
        else:
            psych_text.append("  Primary Emotion: ", style="dim")
            psych_text.append(f"{record['primary_emotion']}\n\n", style="bold white")

            psych_text.append(f"  Anxiety:      {render_scale_bar(record['anxiety_level'])}\n", style="red")
            psych_text.append(f"  Impatience:   {render_scale_bar(record['impatience_level'])}\n", style="yellow")
            psych_text.append(f"  Clarity:      {render_scale_bar(record['mental_clarity_level'])}\n\n", style="green")

            # Emotion lists
            raw_emo = record.get("emotions")
            raw_be = record.get("behavioral_errors")
            raw_cpat = record.get("cognitive_patterns")
            try:
                emo_list = json.loads(raw_emo) if isinstance(raw_emo, str) else (raw_emo or [])
                emo_str = ", ".join([str(x).replace("Emotions.", "") for x in emo_list if x]) if emo_list else "N/A"
            except Exception: emo_str = "N/A"
            try:
                be_list = json.loads(raw_be) if isinstance(raw_be, str) else (raw_be or [])
                be_str = ", ".join([str(x).replace("BehavioralErrors.", "") for x in be_list if x]) if be_list else "N/A"
            except Exception: be_str = "N/A"
            try:
                cp_list_parsed = json.loads(raw_cpat) if isinstance(raw_cpat, str) else (raw_cpat or [])
                cp_str = ", ".join([str(x).replace("CognitivePatterns.", "") for x in cp_list_parsed if x]) if cp_list_parsed else "N/A"
            except Exception: cp_str = "N/A"

            psych_text.append(f"  Emotions:       {format_indented_block(emo_str, indent_spaces=18, first_line_flush=True, wrap_width=60)}\n", style="white")
            psych_text.append(f"  Behav. Errors:  {format_indented_block(be_str, indent_spaces=18, first_line_flush=True, wrap_width=60)}\n", style="white")
            psych_text.append(f"  Cognitive Pat.: {format_indented_block(cp_str, indent_spaces=18, first_line_flush=True, wrap_width=60)}\n", style="white")

            # Emotional Timeline (NEW - previously hidden data)
            pre_emo = record.get("pre_trade_emotions")
            mid_emo = record.get("mid_trade_emotions")
            post_emo = record.get("post_trade_emotions")
            if pre_emo or mid_emo or post_emo:
                psych_text.append("\n  ── Emotional Timeline ──\n", style="bold white")
                if pre_emo:
                    indented_pre = format_indented_block(str(pre_emo), indent_spaces=14, first_line_flush=True, wrap_width=70)
                    psych_text.append(f"  Pre-Trade:  {indented_pre}\n", style="dim italic")
                if mid_emo:
                    indented_mid = format_indented_block(str(mid_emo), indent_spaces=14, first_line_flush=True, wrap_width=70)
                    psych_text.append(f"  Mid-Trade:  {indented_mid}\n", style="dim italic")
                if post_emo:
                    indented_post = format_indented_block(str(post_emo), indent_spaces=14, first_line_flush=True, wrap_width=70)
                    psych_text.append(f"  Post-Trade: {indented_post}\n", style="dim italic")

            if record['t_lesson']:
                indented_lesson = format_indented_block(record['t_lesson'], indent_spaces=4, first_line_flush=False, wrap_width=80)
                psych_text.append(f"\n  Lesson Learned:\n{indented_lesson}\n", style="italic white")

            v_lesson = record.get("visual_lesson_path") or "None"
            if v_lesson == "nan": v_lesson = "None"
            psych_text.append(f"\n  Visual Lesson: {v_lesson}\n", style="dim cyan")

        psych_panel = Panel(
            psych_text,
            title="[bold yellow]Psychological & Cognitive Profile[/bold yellow]",
            border_style="yellow",
            box=box.ROUNDED
        )

        try:
            console.clear(home=True)
        except TypeError:
            console.clear()

        console.print(dashboard)
        console.print(eff_panel)
        console.print(tact_panel)
        console.print(fw_panel)
        console.print(psych_panel)
        action_prompt = inquirer.select(
            message="Select action (or Ctrl+F to open all linked image assets) >",
            choices=[
                Choice("back", name="[<] Return to Ledger Index"),
                Choice("clone", name="[C] Clone for Re-Entry (Fork Structural Vector)")
            ],
            pointer=">",
            qmark=""
        )

        @action_prompt.register_kb("c-f")
        def _open_linked_trade_images(event):
            import os
            import platform
            import subprocess
            import json
            
            paths_to_open = []
            
            # Extract Visual Lesson target path reference safely
            if record.get("visual_lesson_path") and record["visual_lesson_path"] not in ["nan", "None"]:
                paths_to_open.append(record["visual_lesson_path"])
                
            # Extract P1 Fractal target path references safely
            p1_layer = layers_dict.get("P1", {})
            p1_thesis_str = p1_layer.get("thesis", "{}") if isinstance(p1_layer, dict) else getattr(p1_layer, "thesis", "{}")
            try:
                p1_data = json.loads(p1_thesis_str) if p1_thesis_str else {}
                if isinstance(p1_data, dict):
                    if p1_data.get("normal_fractal") and p1_data["normal_fractal"] not in ["nan", "None"]:
                        paths_to_open.append(p1_data["normal_fractal"])
                    if p1_data.get("inverted_fractal") and p1_data["inverted_fractal"] not in ["nan", "None"]:
                        paths_to_open.append(p1_data["inverted_fractal"])
            except Exception:
                pass
                
            # Sequentially execute cross-platform process system calls inside shielded exception blocks
            for path_item in paths_to_open:
                if os.path.exists(path_item):
                    abs_target_path = os.path.abspath(path_item)
                    try:
                        if platform.system() == "Windows":
                            os.startfile(abs_target_path)
                        elif platform.system() == "Darwin":
                            subprocess.Popen(["open", abs_target_path])
                        else:
                            # Linux environment multi-command cascade fallback rule 
                            try:
                                subprocess.Popen(["xdg-open", abs_target_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            except FileNotFoundError:
                                subprocess.Popen(["gio", "open", abs_target_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
                        
        action = action_prompt.execute()
        if action == "back":
            return
        elif action == "clone":
            try:
                ts_choice = inquirer.select(
                    message="Select Timestamp mode for cloned trade >",
                    choices=[
                        Choice("current", name="[1] Use Current System Time"),
                        Choice("custom", name="[2] Enter Custom/Backdated Time")
                    ],
                    pointer=">",
                    qmark=""
                ).execute()

                backdated_ts = None
                if ts_choice == "custom":
                    backdated_ts = get_mandatory_datetime("Enter Target Timestamp", allow_cancel=True)

                macro_state = {
                    "asset": record["asset"],
                    "edge_desc": record["edge_description"],
                    "efficiency_timeframe": record["efficiency_timeframe"],
                    "bias_a": StructuralBias(record["bias_a"]),
                    "p0_dir": Direction(layers_dict["P0"]["direction"]),
                    "p0_str": Strength(layers_dict["P0"]["strength"]),
                    "p0_thesis": layers_dict["P0"]["thesis"],
                    "p2_dir": Direction(layers_dict["P2"]["direction"]),
                    "p2_str": Strength(layers_dict["P2"]["strength"]),
                    "p2_thesis": layers_dict["P2"]["thesis"],
                    "p3_dir": Direction(layers_dict["P3"]["direction"]),
                    "p3_str": Strength(layers_dict["P3"]["strength"]),
                    "p3_thesis": layers_dict["P3"]["thesis"]
                }
            except GoBackException:
                console.print("[warning]Cloning cancelled.[/warning]")
                return
            except (KeyError, ValueError) as e:
                console.print(f"[bold red]Error al clonar el estado estructural: {e}[/bold red]")
                input("Press Enter to continue...")
                return

            flow_new_analysis(backdated_timestamp=backdated_ts, cloned_state=macro_state)
            return

    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        console.rule("[bold cyan]Recent Analyses Index (Last 10)[/bold cyan]")
        console.print()
        
        with Session(get_active_engine()) as db_session:
            raw_conn = db_session.connection().connection
            
            # Fetch rolling 10 entries via LEFT JOINs
            cursor = db_session.execute(text(rolling_query))
            rows = cursor.fetchall()
            
            if not rows:
                console.print("[yellow]No analyses found in the database. Please perform a new analysis first![/yellow]\n")
                input("Press Enter to return to main menu...")
                break
                
            table = render_ledger_table(rows)
            console.print(table)
            console.print()
            
            choices = []
            for idx, r in enumerate(rows):
                created_str = to_local_display(r[4], '%Y-%m-%d %H:%M:%S')
                choices.append(Choice(r[0], name=f"[{idx+1}] {created_str} | {r[1]} (Bias: {r[2]}, Edge: {r[3]:.4f})"))
                
            choices.append(Choice("see_calendar", name="[📅] See Calendar"))
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
                
            if selected_id == "see_calendar":
                while True:
                    # Get distinct years and months via direct raw SQL query
                    calendar_query = """
                    SELECT DISTINCT strftime('%Y', created_at) as year, strftime('%m', created_at) as month 
                    FROM unified_department 
                    ORDER BY year DESC, month DESC;
                    """
                    cursor = db_session.execute(text(calendar_query))
                    calendar_rows = cursor.fetchall()
                    
                    month_names = {
                        "01": "January", "02": "February", "03": "March", "04": "April", "05": "May", "06": "June",
                        "07": "July", "08": "August", "09": "September", "10": "October", "11": "November", "12": "December"
                    }
                    
                    cal_choices = []
                    for y, m in calendar_rows:
                        if y and m:
                            cal_choices.append(Choice(f"{y}-{m}", name=f"📅 {month_names.get(m, m)} {y}"))
                    cal_choices.append(Choice("back", name="[<] Return to Main Ledger"))
                    
                    selected_month = inquirer.select(
                        message="Select Calendar Month >",
                        choices=cal_choices,
                        pointer=">",
                        qmark=""
                    ).execute()
                    
                    if selected_month == "back":
                        break
                        
                    year_str, month_str = selected_month.split("-")
                    year = int(year_str)
                    month = int(month_str)
                    
                    # Python programmatic range calculation
                    last_day = calendar.monthrange(year, month)[1]
                    start_str = f"{year:04d}-{month:02d}-01 00:00:00"
                    end_str = f"{year:04d}-{month:02d}-{last_day:02d} 23:59:59.999999"
                    
                    # Executing month search with LEFT JOINs
                    month_query = """
                    SELECT u.id, u.asset, u.market_bias, u.calc_edge, u.created_at, 
                           e.bias_a, e.real_bias_b, e.resolution_type, 
                           t.compliance, u.trade_status, u.is_backdated
                    FROM unified_department u
                    LEFT JOIN efficiency_audit e ON u.id = e.id
                    LEFT JOIN tactical_audit t ON u.id = t.id
                    WHERE u.created_at BETWEEN :start_str AND :end_str
                    ORDER BY u.created_at DESC;
                    """
                    cursor = db_session.execute(text(month_query), {"start_str": start_str, "end_str": end_str})
                    month_rows = cursor.fetchall()
                    
                    if not month_rows:
                        console.print(f"[yellow]No records found for {month_names.get(month_str)} {year}.[/yellow]")
                        input("Press Enter to continue...")
                        continue
                        
                    while True:
                        try:
                            console.clear(home=True)
                        except TypeError:
                            console.clear()
                            
                        m_table = render_ledger_table(month_rows)
                        console.rule(f"[bold cyan]Analyses for {month_names.get(month_str)} {year}[/bold cyan]")
                        console.print()
                        console.print(m_table)
                        console.print()
                        
                        trade_choices = []
                        for idx, row in enumerate(month_rows):
                            r_id = row[0]
                            asset = row[1]
                            market_bias = row[2]
                            calc_edge = row[3]
                            created_at = row[4]
                            
                            created_str = to_local_display(created_at, '%Y-%m-%d %H:%M:%S') if isinstance(created_at, datetime.datetime) else str(created_at)[0:19]
                            short_id = r_id[:8]
                            calc_edge_val = float(calc_edge) if calc_edge is not None else 0.0
                            
                            trade_choices.append(Choice(r_id, name=f"[{idx+1}] {created_str} | {short_id} - {asset} (Bias: {market_bias}, Edge: {calc_edge_val:.4f})"))
                            
                        trade_choices.append(Choice("back", name="[<] Return to Months List"))
                        
                        inspect_id = inquirer.select(
                            message=f"Select Trade from {month_names.get(month_str)} {year}:",
                            choices=trade_choices,
                            pointer=">",
                            qmark=""
                        ).execute()
                        
                        if inspect_id == "back":
                            break
                            
                        show_unified_detail(inspect_id, raw_conn)
                
            else:
                # selected_id is a specific trade ID
                show_unified_detail(selected_id, raw_conn)
def flow_new_analysis(backdated_timestamp=None, cloned_state: dict = None):
    trade_id = str(uuid.uuid4())
    console.print(f"\n[muted]Initialized new unified trade context: {trade_id}[/muted]")
    
    session = AnalysisSession(trade_id)
    if cloned_state:
        session.state.update(cloned_state)
        
    while True:
        try:
            # --- STEP 1: Structure Parameters ---
            def prompt_asset():
                available_assets = get_assets(get_active_engine())
                choices = [Choice(a, name=a) for a in available_assets]
                choices.append(Choice("back_to_main", name="[<] Back to Main Menu"))
                
                asset_choice = bind_pause(inquirer.select(
                    message="Select Asset >",
                    choices=choices,
                    pointer=">",
                    qmark="",
                    keybindings={"skip": []}
                )).execute()
                
                if asset_choice == "back_to_main":
                    raise ExitToMainMenuException("Go back requested")
                return asset_choice

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

            import json
            p1_normal = session.prompt("p1_normal_fractal", handle_visual_lesson_assignment, trade_id, asset, "nan", "_NF")
            p1_inverted = session.prompt("p1_inverted_fractal", handle_visual_lesson_assignment, trade_id, asset, "nan", "_IF")
            p1_thesis_payload = json.dumps({"normal_fractal": p1_normal, "inverted_fractal": p1_inverted})
            session.state["p1_thesis"] = p1_thesis_payload
            p1_thesis = p1_thesis_payload

            # --- STEP 2: Tactical & Meta Parameters ---
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

            def prompt_edge_desc():
                current_asset = session.state.get("asset")
                previous_edge = "No previous asset history found."
                from sqlalchemy.orm import Session
                
                with Session(get_active_engine()) as db_session:
                    try:
                        raw_conn = db_session.connection().connection
                        try:
                            cursor = raw_conn.execute(
                                "SELECT edge_description FROM unified_department WHERE asset = ? ORDER BY created_at DESC LIMIT 1;",
                                (current_asset,)
                            )
                        except Exception:
                            cursor = raw_conn.execute(
                                "SELECT edge_desc FROM unified_department WHERE asset = ? ORDER BY created_at DESC LIMIT 1;",
                                (current_asset,)
                            )
                        row = cursor.fetchone()
                        if row and row[0]:
                            previous_edge = row[0]
                    except Exception:
                        previous_edge = "No previous asset history found."

                # Invoke real-time math metrics subroutines using locked staging memory parameters
                p0_dir = session.state.get("p0_dir")
                p0_str = session.state.get("p0_str")
                p1_dir = session.state.get("p1_dir")
                p1_str = session.state.get("p1_str")
                p2_dir = session.state.get("p2_dir")
                p2_str = session.state.get("p2_str")
                p3_dir = session.state.get("p3_dir")
                p3_str = session.state.get("p3_str")
                p4_dir = session.state.get("p4_dir")
                p4_str = session.state.get("p4_str")

                def get_dir_val(d): return 1 if d == Direction.LONG else -1 if d == Direction.SHORT else 0
                def get_str_val(s): return 2 if s == Strength.STRONG else 1 if s == Strength.MID else 0

                x0 = get_dir_val(p0_dir) * get_str_val(p0_str)
                x1 = get_dir_val(p1_dir) * get_str_val(p1_str)
                x2 = get_dir_val(p2_dir) * get_str_val(p2_str)
                x3 = get_dir_val(p3_dir) * get_str_val(p3_str)
                x4 = get_dir_val(p4_dir) * get_str_val(p4_str)

                i_cd = (0.30 * x0 + 0.25 * x1 + 0.15 * x2 + 0.10 * x3 + 0.20 * x4) / 2.0
                
                market_bias = determine_market_bias(i_cd)
                long_prob, short_prob, no_trade_prob = calculate_probabilities(i_cd)

                # Render dashboard
                bias_color = "success" if market_bias == "Bullish" else "danger" if market_bias == "Bearish" else "warning"
                
                indented_prev_edge = format_indented_block(previous_edge, indent_spaces=11, first_line_flush=False, wrap_width=60)

                dashboard_text = Text()
                dashboard_text.append("--- Previous Session Edge Description Reference ---\n", style="bold cyan")
                dashboard_text.append(f"{indented_prev_edge}\n\n", style="italic white")
                
                dashboard_text.append("--- Real-Time Metrics & Probability Dashboard ---\n", style="bold cyan")
                dashboard_text.append("Market Bias: ", style="dim")
                dashboard_text.append(f"{market_bias}\n", style=bias_color)
                dashboard_text.append("Directional Probabilities:\n", style="dim")
                dashboard_text.append("  Long Prob:      ", style="dim")
                dashboard_text.append(f"{long_prob * 100:.1f}%\n", style="success")
                dashboard_text.append("  Short Prob:     ", style="dim")
                dashboard_text.append(f"{short_prob * 100:.1f}%\n", style="danger")
                dashboard_text.append("  No-Trade Prob:  ", style="dim")
                dashboard_text.append(f"{no_trade_prob * 100:.1f}%\n", style="warning")

                dashboard_panel = Panel(
                    dashboard_text,
                    title="[bold primary]Edge Description Context[/bold primary]",
                    border_style="primary",
                    box=box.ROUNDED
                )
                console.print(dashboard_panel)
                console.print()

                return get_mandatory_text("Efficiency Edge Description", multiline=True)

            edge_desc = session.prompt("edge_desc", prompt_edge_desc)
            
            efficiency_timeframe = session.prompt("efficiency_timeframe", lambda: bind_pause(inquirer.select(
                message="Select Efficiency Timeframe [15M/1H/4H] >",
                choices=[Choice("15M", name="15M"), Choice("1H", name="1H"), Choice("4H", name="4H")],
                pointer=">",
                qmark=""
            )).execute())
            
            evp_raw = session.prompt("evp_raw", lambda: bind_pause(inquirer.text(message="Edge Validation Price (Target Convergence) [Optional] >")).execute())
            si_raw = session.prompt("si_raw", lambda: bind_pause(inquirer.text(message="Structural Invalidation Price (Nullification Threshold) [Optional] >")).execute())
            
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
                efficiency_timeframe = session.state["efficiency_timeframe"]
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
                            if label == "P1":
                                try:
                                    import json
                                    raw_thesis = t
                                    fractal_data = json.loads(raw_thesis) if raw_thesis else {}
                                    if not isinstance(fractal_data, dict):
                                        raise ValueError()
                                    
                                    tact_text.append(f"   Normal Fractal:   {fractal_data.get('normal_fractal', 'nan')}\n", style="dim cyan")
                                    tact_text.append(f"   Inverted Fractal: {fractal_data.get('inverted_fractal', 'nan')}\n", style="dim cyan")
                                except (Exception, ValueError):
                                    # Safe fallback for legacy flat text entries
                                    indented_thesis = format_indented_block(t, indent_spaces=11, wrap_width=38)
                                    tact_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
                            else:
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
                meta_text.append(f"Efficiency Timeframe: ", style="dim")
                meta_text.append(f"{efficiency_timeframe}\n", style="white")
                    
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
                    from tools.database import UnifiedDepartment, AnalysisLayer, EfficiencyAudit as ModelEfficiencyAudit
                    from sqlalchemy.orm import Session
                    
                    with Session(get_active_engine()) as db_session:
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
                                no_trade_prob=tactical.no_trade_prob,
                                is_backdated=backdated_timestamp is not None
                            )
                            
                            evp_val = session.state.get("evp_raw", "")
                            si_val = session.state.get("si_raw", "")
                            try:
                                new_record.edge_validation_price = Decimal(str(evp_val)) if evp_val.strip() else None
                                new_record.structural_invalidation = Decimal(str(si_val)) if si_val.strip() else None
                            except Exception:
                                console.print("[bold red]Invalid decimal input for price metrics. Setting to None.[/bold red]")
                                new_record.edge_validation_price = None
                                new_record.structural_invalidation = None
                            if backdated_timestamp:
                                new_record.created_at = backdated_timestamp
                                new_record.updated_at = backdated_timestamp
                            db_session.add(new_record)

                            new_ea = ModelEfficiencyAudit(id=trade_id, bias_a=bias_a.value, efficiency_timeframe=efficiency_timeframe)
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
                        Choice("efficiency_timeframe", name=f"Efficiency Timeframe: {efficiency_timeframe}"),
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
                            available_assets = get_assets(get_active_engine())
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
                                add_asset(new_val, category="Crypto", engine=get_active_engine())
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
                        elif field_to_edit == "efficiency_timeframe":
                            new_val = inquirer.select(message="Edit Efficiency Timeframe >", choices=[Choice("1H", name="1H"), Choice("4H", name="4H")], pointer=">", qmark="").execute()
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
        except GoBackException:
            console.print("\n[warning]Operation cancelled.[/warning]")
            input("Press Enter to continue...")
            return
        except ExitToMainMenuException:
            return

def flow_pending_audits():
    records = get_records_by_state(LifecycleState.PENDING_AUDITS, engine=get_active_engine())
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
            
        eff_tf_val = payload.get("audit_efficiency", {}).get("efficiency_timeframe", "N/A")
        console.print(f"[bold cyan]Original Market Bias (Bias A):[/bold cyan] {market_bias_val} / {bias_a_val} | [bold cyan]Timeframe:[/bold cyan] {eff_tf_val}")
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
                real_bias_b = session.prompt("real_bias_b", get_enum_choice, "Real Bias B", StructuralBias)
                res_type = session.prompt("res_type", get_enum_choice, "Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN])
                struct_res = session.prompt("struct_res", get_enum_choice, "Structural Resolution", StructuralResolution)
                fail_reason = session.prompt("fail_reason", get_enum_choice, "Failure Reason", FailureReason)
                lesson_eff = session.prompt("lesson_eff", get_optional_text, "Efficiency Lesson Learned")

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
                rev_text.append(f"Original Bias (Bias A): {bias_a.value if hasattr(bias_a, 'value') else bias_a}\n")
                rev_text.append(f"Real Bias B: {real_bias_b.value if hasattr(real_bias_b, 'value') else real_bias_b}\n")
                rev_text.append(f"Efficiency Timeframe: {eff_tf_val}\n")
                rev_text.append(f"Resolution Type: {res_type.value if hasattr(res_type, 'value') else res_type}\n")
                rev_text.append(f"Structural Resolution: {struct_res.value if hasattr(struct_res, 'value') else struct_res}\n")
                rev_text.append(f"Failure Reason: {fail_reason.value if hasattr(fail_reason, 'value') else fail_reason}\n")
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
                    display_bias_b = real_bias_b.value if hasattr(real_bias_b, 'value') else real_bias_b
                    edit_choices = [
                        Choice("real_bias_b", name=f"Real Bias B: {display_bias_b}"),
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
                        t_comp = session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                        htf_trend = session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                        ltf_trend = session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)
                        lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                        visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))
                        
                        audit_tactical = TacticalAudit(
                            tactical_id=trade_id,
                            compliance=t_comp,
                            htf_trend_context=htf_trend,
                            ltf_trend_context=ltf_trend,
                            lesson_learned=lesson_tact,
                            visual_lesson_path=visual_path
                        )
                        
                        rev_text = Text()
                        rev_text.append(f"Trade Status: {t_status.value}\n")
                        rev_text.append(f"Compliance State: {t_comp.value if isinstance(t_comp, Enum) else t_comp}\n")
                        rev_text.append(f"HTF Trend Context: {htf_trend.value if isinstance(htf_trend, Enum) else htf_trend}\n")
                        rev_text.append(f"LTF Trend Context: {ltf_trend.value if isinstance(ltf_trend, Enum) else ltf_trend}\n")
                        rev_text.append(f"Tactical Lesson Learned: {lesson_tact}\n")
                        if visual_path and visual_path != "nan":
                            rev_text.append(f"Visual Lesson: {visual_path}\n")
                        
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
                            at_dump = audit_tactical.model_dump()
                            string_enum_keys = {
                                "compliance", "tier_setup", "market_state", "session", "exit_type",
                                "trade_status", "followed_plan", "primary_emotion", "setup_type",
                                "htf_trend_context", "confirmation_status", "ltf_trend_context",
                                "pre_trade_emotions", "mid_trade_emotions", "post_trade_emotions",
                                "could_hit_tp", "lesson_learned", "trade_decision", "trade_duration",
                                "visual_lesson_path"
                            }
                            for k, v in at_dump.items():
                                if v is None and k in string_enum_keys:
                                    at_dump[k] = "nan"
                            new_payload["audit_tactical"] = at_dump
                            console.print("[green]Tactical Audit saved (No Trade Taken).[/green]")
                            session.clear_state()
                            break
                        elif action_choice == "discard":
                            raise PauseAuditException("Discard requested")
                        elif action_choice == "edit":
                            edit_choices = [
                                Choice("t_comp", name=f"Compliance State: {t_comp.value if isinstance(t_comp, Enum) else t_comp}"),
                                Choice("htf_trend", name=f"HTF Trend: {htf_trend.value if isinstance(htf_trend, Enum) else htf_trend}"),
                                Choice("ltf_trend", name=f"LTF Trend: {ltf_trend.value if isinstance(ltf_trend, Enum) else ltf_trend}"),
                                Choice("lesson_tact", name=f"Lesson: {lesson_tact[:30]}..."),
                                Choice("visual_lesson_path", name=f"Visual Lesson Path: {session.state.get('visual_lesson_path', 'nan')}"),
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
                            if field_to_edit == "t_comp":
                                session.state["t_comp"] = get_enum_choice("Edit Compliance State", ComplianceState)
                            elif field_to_edit == "htf_trend":
                                session.state["htf_trend"] = get_enum_choice("Edit HTF Trend Context", HTFTrendContext)
                            elif field_to_edit == "ltf_trend":
                                session.state["ltf_trend"] = get_enum_choice("Edit LTF Trend Context", TrendContext)
                            elif field_to_edit == "lesson_tact":
                                session.state["lesson_tact"] = get_mandatory_text("Edit Tactical Lesson Learned", multiline=True)
                            elif field_to_edit == "visual_lesson_path":
                                visual_path = handle_visual_lesson_assignment(trade_id, payload.get("asset", "Unknown"), session.state.get("visual_lesson_path", "nan"))
                                session.state["visual_lesson_path"] = visual_path
                    break
                else:
                    while True:
                        # --- Auto-Heal Legacy Tactical State ---
                        legacy_trigger_vals = [
                            "Yes", "yes but bad entry point (too tight)", 
                            "Yes but late entry", "yes but closed too early - Fear", "No"
                        ]
                        current_conf = session.state.get("conf_status") or session.state.get("confirmation_status")
                        if isinstance(current_conf, str) and current_conf in legacy_trigger_vals:
                            # Purgar claves tácticas legacy para forzar re-evaluación bajo Motor B
                            for k in ["conf_status", "confirmation_status", "selected_gates", "selected_confs", "mfe_potencial_estimado", "gate_action", "htf_trend", "ltf_trend", "lesson_tact", "visual_lesson_path"]:
                                session.state.pop(k, None)

                        sl = session.prompt("sl", get_mandatory_float, "Stop Loss")
                        entry_p = session.prompt("entry_p", get_mandatory_float, "Entry Price")
                        size = session.prompt("size", get_mandatory_float, "Size")
                        tp = session.prompt("tp", get_mandatory_float, "Take Profit")
                        entry_time = session.prompt("entry_time", get_mandatory_datetime, "Entry Time")

                        def ask_gates():
                            choices = [
                                Choice("g1", name="G1: P0 Trend 15m"),
                                Choice("g2", name="G2: Trend Fractal (5m or 15m)"),
                                Choice("g3", name="G3: Limit Order"),
                                Choice("g4", name="G4: Breathing - Mindfulness"),
                                Choice("g5", name="G5: Manual Cooldown"),
                                Choice("g6", name="G6: SL Validated"),
                                Choice("g7", name="G7: TP Validated")
                            ]
                            return bind_pause(inquirer.checkbox(message="Select fulfilled Gates >", choices=choices)).execute()
                            
                        selected_gates = session.prompt("selected_gates", ask_gates)
                        gates_failed_cnt = 7 - len(selected_gates)
                        
                        g1_trend_15m = "g1" in selected_gates
                        g2_fractal_trend = "g2" in selected_gates
                        g3_limit_order = "g3" in selected_gates
                        g4_breathing = "g4" in selected_gates
                        g5_manual_cooldown = "g5" in selected_gates
                        g6_sl_validated = "g6" in selected_gates
                        g7_tp_validated = "g7" in selected_gates

                        abort_trade = False
                        if gates_failed_cnt > 0:
                            gate_action = session.prompt("gate_action", lambda: bind_pause(inquirer.select(
                                message="[GATES FAILED] Operación inválida estructuralmente:",
                                choices=["Abortar Trade", "Forzar Entrada (Revenge)"]
                            )).execute())
                            if gate_action == "Abortar Trade":
                                abort_trade = True
                                t_status = next((c for c in TradeStatus if c.value == "Trade_no_taken"), TradeStatus.NO_TAKEN)
                                session.state["t_status"] = t_status
                        
                        if abort_trade:
                            t_comp = session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                            htf_trend = session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                            ltf_trend = session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)
                            lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                            visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))
                            
                            audit_tactical = TacticalAudit(
                                tactical_id=trade_id,
                                trade_status=t_status,
                                compliance=t_comp,
                                htf_trend_context=htf_trend,
                                ltf_trend_context=ltf_trend,
                                lesson_learned=lesson_tact,
                                visual_lesson_path=visual_path,
                                gates_failed=gates_failed_cnt,
                                confirmations_count=0,
                                stop_loss=0.0,
                                entry_price=0.0,
                                size=0.0,
                                take_profit=0.0,
                                cost=0.0,
                                mae=0.0,
                                mfe=0.0,
                                g1_trend_15m=g1_trend_15m,
                                g2_fractal_trend=g2_fractal_trend,
                                g3_limit_order=g3_limit_order,
                                g4_breathing=g4_breathing,
                                g5_manual_cooldown=g5_manual_cooldown,
                                g6_sl_validated=g6_sl_validated,
                                g7_tp_validated=g7_tp_validated
                            )
                        else:
                            if gates_failed_cnt == 0:
                                def ask_confirmations():
                                    choices = [
                                        Choice("c1", name="C1: KL as Support/Resistance"),
                                        Choice("c2", name="C2: Standard Fractal Confirmation (5-15m)"),
                                        Choice("c3", name="C3: 1m Fractal Confirmation/Assistance"),
                                        Choice("c4", name="C4: 1h Fractal Continuation or Inflection"),
                                        Choice("c5", name="C5: KL as target"),
                                        Choice("c6", name="C6: Liquidity grabbed or to be grabbed"),
                                        Choice("c7", name="C7: 0.4-0.6 Retracement in P015m"),
                                        Choice("c8", name="C8: Convergence with P015m")
                                    ]
                                    return bind_pause(inquirer.checkbox(message="Select fulfilled Confirmations >", choices=choices)).execute()
                                
                                selected_confs = session.prompt("selected_confs", ask_confirmations)
                                conf_status = session.prompt("conf_status", lambda: bind_pause(inquirer.select(
                                    message="Confirmation Status >",
                                    choices=[c for c in ConfirmationStatus if c not in (ConfirmationStatus.S7_REVENGE_FORCED, ConfirmationStatus.SKIP)]
                                )).execute())
                                
                                if conf_status == ConfirmationStatus.S6_FEAR_NO_ENTRY:
                                    mfe_potencial = session.prompt("mfe_potencial_estimado", get_mandatory_float, "MFE Potencial Estimado")
                                else:
                                    mfe_potencial = None
                            else:
                                selected_confs = []
                                conf_status = ConfirmationStatus.S7_REVENGE_FORCED
                                mfe_potencial = None
                                
                            confirmations_count = len(selected_confs)
                            c1_kl_support = "c1" in selected_confs
                            c2_fractal_std = "c2" in selected_confs
                            c3_fractal_1m = "c3" in selected_confs
                            c4_fractal_1h = "c4" in selected_confs
                            c5_kl_target = "c5" in selected_confs
                            c6_liquidity = "c6" in selected_confs
                            c7_retracement = "c7" in selected_confs
                            c8_convergence_15m = "c8" in selected_confs

                            if conf_status == ConfirmationStatus.S7_REVENGE_FORCED or gates_failed_cnt >= 3:
                                tier_setup = TierSetup.F
                            elif gates_failed_cnt >= 1:
                                tier_setup = TierSetup.D
                            elif confirmations_count >= 5:
                                tier_setup = TierSetup.A
                            elif confirmations_count >= 4:
                                tier_setup = TierSetup.B
                            else:
                                tier_setup = TierSetup.C

                            htf_trend = session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                            ltf_trend = session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)
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
                            market_state = session.prompt("market_state", get_enum_choice, "Market State", MarketState)
                            f_plan = session.prompt("f_plan", get_enum_choice, "Followed Plan", FollowedPlan)
                            setup_t = session.prompt("setup_t", get_enum_choice, "Setup Type", SetupType)
                            behav_errors = session.prompt("behav_errors", get_multi_enum_choice, "Behavioral Errors", BehavioralErrors)
                            cog_patterns = session.prompt("cog_patterns", get_multi_enum_choice, "Cognitive Patterns", CognitivePatterns)
                            mae = session.prompt("mae", get_mandatory_float, "MAE (0 <= MAE <= 10)", min_val=0, max_val=10)
                            mfe = session.prompt("mfe", get_mandatory_float, "MFE (0 <= MFE <= 10)", min_val=0, max_val=10)
                            cost = session.prompt("cost", get_mandatory_float, "Cost (Fees/Funding)")
                            lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                            visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))

                            audit_tactical = TacticalAudit(
                                tactical_id=trade_id,
                                trade_status=t_status,
                                htf_trend_context=htf_trend,
                                ltf_trend_context=ltf_trend,
                                stop_loss=sl,
                                entry_price=entry_p,
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
                                lesson_learned=lesson_tact,
                                visual_lesson_path=visual_path,
                                g1_trend_15m=g1_trend_15m,
                                g2_fractal_trend=g2_fractal_trend,
                                g3_limit_order=g3_limit_order,
                                g4_breathing=g4_breathing,
                                g5_manual_cooldown=g5_manual_cooldown,
                                g6_sl_validated=g6_sl_validated,
                                g7_tp_validated=g7_tp_validated,
                                c1_kl_support=c1_kl_support,
                                c2_fractal_std=c2_fractal_std,
                                c3_fractal_1m=c3_fractal_1m,
                                c4_fractal_1h=c4_fractal_1h,
                                c5_kl_target=c5_kl_target,
                                c6_liquidity=c6_liquidity,
                                c7_retracement=c7_retracement,
                                c8_convergence_15m=c8_convergence_15m,
                                gates_failed=gates_failed_cnt,
                                confirmations_count=confirmations_count,
                                mfe_potencial_estimado=mfe_potencial
                            )

                        # Post Review Panel Reconstructed & Homologated
                        ep = audit_tactical.entry_price or 0.0
                        sl = audit_tactical.stop_loss or 0.0
                        tp = audit_tactical.take_profit or 0.0
                        dist_to_sl = abs(ep - sl) / ep if ep != 0.0 else 0.0
                        dist_to_tp = abs(ep - tp) / ep if ep != 0.0 else 0.0

                        rev_text = Text()
                        
                        # --- Core Inputs ---
                        rev_text.append("--- Core Inputs ---\n", style="bold green")
                        rev_text.append(f"Trade Status:         {t_status.value if hasattr(t_status, 'value') else t_status}\n", style="white")
                        rev_text.append(f"Compliance:           {t_comp.value if hasattr(t_comp, 'value') else t_comp}\n", style="white")
                        rev_text.append(f"Entry Price:          {ep}\n", style="white")
                        rev_text.append(f"Closing Price:        {audit_tactical.closing_price}\n", style="white")
                        rev_text.append(f"Size:                 {audit_tactical.size}\n", style="white")
                        rev_text.append(f"Stop Loss:            {sl}\n", style="white")
                        rev_text.append(f"Take Profit:          {tp}\n", style="white")
                        rev_text.append(f"MAE Adverse:          {audit_tactical.mae}\n", style="white")
                        rev_text.append(f"MFE Favorable:        {audit_tactical.mfe}\n", style="white")
                        rev_text.append(f"Could Hit TP:         {audit_tactical.could_hit_tp}\n", style="white")
                        entry_str = to_local_display(audit_tactical.entry_time) if audit_tactical.entry_time else "N/A"
                        exit_str = to_local_display(audit_tactical.exit_time) if audit_tactical.exit_time else "N/A"
                        rev_text.append(f"Entry Time:           {entry_str}\n", style="white")
                        rev_text.append(f"Exit Time:            {exit_str}\n\n", style="white")
                        
                        # --- Distance Calculations ---
                        rev_text.append("--- Distance Calculations ---\n", style="bold cyan")
                        rev_text.append(f"Dist to SL:           {dist_to_sl * 100:.2f}%\n", style="white")
                        rev_text.append(f"Dist to TP:           {dist_to_tp * 100:.2f}%\n\n", style="white")
                        
                        # --- Calculated Algebraic Metrics ---
                        rev_text.append("--- Calculated Algebraic Metrics ---\n", style="bold yellow")
                        rev_text.append(f"Resolved Direction:   {audit_tactical.trade_decision}\n", style="bold cyan")
                        rev_text.append(f"Notional Size:        {audit_tactical.notional_size:.2f}\n", style="white")
                        rev_text.append(f"Notional Size USD:    {audit_tactical.notional_size:.2f}\n", style="white")
                        rev_text.append(f"Capital At Risk:      {audit_tactical.capital_at_risk:.2f}\n", style="white")
                        rev_text.append(f"Risk USD:             {audit_tactical.risk_usd:.2f}\n", style="white")
                        rev_text.append(f"PnL:                  {audit_tactical.pnl:.2f}\n", style="white")
                        rev_text.append(f"PnL and Cost:         {audit_tactical.pnl_and_cost:.2f}\n", style="white")
                        rev_text.append(f"R:R:                  {audit_tactical.r_r:.2f}\n", style="white")
                        rev_text.append(f"R Multiple:           {audit_tactical.r_multiple:.2f}\n", style="white")
                        rev_text.append(f"Captured MFE:         {audit_tactical.captured_mfe:.2f}\n\n", style="white")
                        
                        # --- Execution Framework Context ---
                        rev_text.append("--- Execution Framework Context ---\n", style="bold magenta")
                        rev_text.append(f"Tier Setup:           {tier_setup.value if hasattr(tier_setup, 'value') else tier_setup}\n", style="white")
                        rev_text.append(f"Setup Type:           {setup_t.value if hasattr(setup_t, 'value') else setup_t}\n", style="white")
                        rev_text.append(f"Market State:         {market_state.value if hasattr(market_state, 'value') else market_state}\n", style="white")
                        rev_text.append(f"HTF Trend Context:    {htf_trend.value if hasattr(htf_trend, 'value') else htf_trend}\n", style="white")
                        rev_text.append(f"LTF Trend Context:    {ltf_trend.value if hasattr(ltf_trend, 'value') else ltf_trend}\n", style="white")
                        rev_text.append(f"Confirmation Status:  {conf_status.value if hasattr(conf_status, 'value') else conf_status}\n", style="white")
                        rev_text.append(f"Followed Plan:        {f_plan.value if hasattr(f_plan, 'value') else f_plan}\n", style="white")
                        
                        # --- Motor B (Gates & Confirmations) ---
                        rev_text.append("--- Motor B (Gates & Confirmations) ---\n", style="bold magenta")
                        rev_text.append(f"Gates Failed:         {gates_failed_cnt}\n", style="red" if gates_failed_cnt > 0 else "green")
                        if not abort_trade:
                            rev_text.append(f"Confirmations Count:  {confirmations_count}\n", style="white")
                            if mfe_potencial is not None:
                                rev_text.append(f"MFE Potencial (S6):   {mfe_potencial}\n", style="yellow")

                        # --- Psychological & Cognitive Logging ---
                        rev_text.append("--- Psychological & Cognitive Logging ---\n", style="bold blue")
                        rev_text.append(f"Primary Emotion:      {p_emotion.value if hasattr(p_emotion, 'value') else p_emotion}\n", style="white")
                        emotions_str = ", ".join([e.value if hasattr(e, 'value') else str(e) for e in emotions]) if isinstance(emotions, list) else str(emotions)
                        be_str = ", ".join([b.value if hasattr(b, 'value') else str(b) for b in behav_errors]) if isinstance(behav_errors, list) else str(behav_errors)
                        cp_str = ", ".join([c.value if hasattr(c, 'value') else str(c) for c in cog_patterns]) if isinstance(cog_patterns, list) else str(cog_patterns)
                        rev_text.append(f"Emotions:             {format_indented_block(emotions_str, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")
                        rev_text.append(f"Behav. Errors:        {format_indented_block(be_str, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")
                        rev_text.append(f"Cognitive Pat:        {format_indented_block(cp_str, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")
                        rev_text.append(f"Pre-Trade Emotions:   {format_indented_block(pre_trade_emotions, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")
                        rev_text.append(f"Mid-Trade Emotions:   {format_indented_block(mid_trade_emotions, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")
                        rev_text.append(f"Post-Trade Emotions:  {format_indented_block(post_trade_emotions, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n\n", style="white")
                        
                        # --- Internal State Thresholds ---
                        rev_text.append("--- Internal State Thresholds ---\n", style="bold orange1")
                        rev_text.append(f"Anxiety Level:        {anxiety}\n", style="white")
                        rev_text.append(f"Impatience Level:     {impatience}\n", style="white")
                        rev_text.append(f"Mental Clarity Level: {mental_clarity}\n\n", style="white")
                        
                        # --- Qualitative Notes ---
                        rev_text.append("--- Qualitative Notes ---\n", style="bold cyan")
                        if lesson_tact and lesson_tact != "nan":
                            rev_text.append(f"Lesson Learned:\n  {format_indented_block(lesson_tact, indent_spaces=2, first_line_flush=False, wrap_width=60)}\n", style="dim italic")
                        else:
                            rev_text.append("Lesson Learned:       N/A\n", style="dim italic")
                        
                        v_path = session.state.get("visual_lesson_path", "nan")
                        if v_path and v_path != "nan":
                            rev_text.append(f"Visual Lesson Path: {v_path}\n", style="dim cyan")

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
                                Choice("htf_trend", name=f"HTF Trend: {htf_trend.value if hasattr(htf_trend, 'value') else htf_trend}"),
                                Choice("ltf_trend", name=f"LTF Trend: {ltf_trend.value if hasattr(ltf_trend, 'value') else ltf_trend}"),
                                Choice("confirmation_5m_15m", name=f"5m/15m Confirmation: {confirmation_5m_15m}"),
                                Choice("sl", name=f"Stop Loss: {sl}"),
                                Choice("entry_p", name=f"Entry Price: {entry_p}"),
                                Choice("size", name=f"Size: {size}"),
                                Choice("tp", name=f"Take Profit: {tp}"),
                                Choice("entry_time", name=f"Entry Time: {entry_time}"),
                                Choice("exit_time", name=f"Exit Time: {exit_time}"),
                                Choice("exit_type", name=f"Exit Type: {exit_type.value if hasattr(exit_type, 'value') else exit_type}"),
                                Choice("conf_status", name=f"Confirmation Status: {conf_status.value if hasattr(conf_status, 'value') else conf_status}"),
                                Choice("conf_params", name=f"Confirmation Params: {len(conf_params) if isinstance(conf_params, list) else 0} chosen"),
                                Choice("close_p", name=f"Closing Price: {close_p}"),
                                Choice("could_hit_tp", name=f"Could hit TP: {could_hit_tp}"),
                                Choice("t_comp", name=f"Compliance State: {t_comp.value if hasattr(t_comp, 'value') else t_comp}"),
                                Choice("tier_setup", name=f"Tier Setup: {tier_setup.value if hasattr(tier_setup, 'value') else tier_setup}"),
                                Choice("market_state", name=f"Market State: {market_state.value if hasattr(market_state, 'value') else market_state}"),
                                Choice("f_plan", name=f"Followed Plan: {f_plan.value if hasattr(f_plan, 'value') else f_plan}"),
                                Choice("setup_t", name=f"Setup Type: {setup_t.value if hasattr(setup_t, 'value') else setup_t}"),
                                Choice("mae", name=f"MAE: {mae}"),
                                Choice("mfe", name=f"MFE: {mfe}"),
                                Choice("cost", name=f"Cost: {session.state.get('cost', 0.0)}"),
                                Choice("primary_emotion", name=f"Primary Emotion: {p_emotion.value if hasattr(p_emotion, 'value') else p_emotion}"),
                                Choice("emotions", name=f"Emotions: {len(emotions) if isinstance(emotions, list) else 0} chosen"),
                                Choice("behav_errors", name=f"Behavioral Errors: {len(behav_errors) if isinstance(behav_errors, list) else 0} chosen"),
                                Choice("cog_patterns", name=f"Cognitive Patterns: {len(cog_patterns) if isinstance(cog_patterns, list) else 0} chosen"),
                                Choice("anxiety", name=f"Anxiety Level: {anxiety}"),
                                Choice("impatience", name=f"Impatience Level: {impatience}"),
                                Choice("mental_clarity", name=f"Mental Clarity Level: {mental_clarity}"),
                                Choice("pre_trade_emotions", name=f"Pre Trade Emotions: {pre_trade_emotions[:25] if pre_trade_emotions else 'N/A'}..."),
                                Choice("mid_trade_emotions", name=f"Mid Trade Emotions: {mid_trade_emotions[:25] if mid_trade_emotions else 'N/A'}..."),
                                Choice("post_trade_emotions", name=f"Post Trade Emotions: {post_trade_emotions[:25] if post_trade_emotions else 'N/A'}..."),
                                Choice("lesson_tact", name=f"Lesson: {lesson_tact[:30] if lesson_tact else 'N/A'}..."),
                                Choice("visual_lesson_path", name=f"Visual Lesson Path: {session.state.get('visual_lesson_path', 'nan')}"),
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
                            elif field_to_edit == "confirmation_5m_15m":
                                session.state["confirmation_5m_15m"] = bind_pause(inquirer.select(
                                    message="Edit 5m_15m_confirmation >",
                                    choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                    pointer=">",
                                    qmark=""
                                )).execute()
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
                            elif field_to_edit == "conf_params":
                                session.state["conf_params"] = get_multi_enum_choice("Edit Confirmation Params", ConfirmationParams)
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
                            elif field_to_edit == "cost":
                                session.state["cost"] = get_mandatory_float("Edit Cost")
                            elif field_to_edit == "primary_emotion":
                                session.state["p_emotion"] = get_enum_choice("Edit Primary Emotion", PrimaryEmotion)
                            elif field_to_edit == "emotions":
                                session.state["emotions"] = get_multi_enum_choice("Edit Emotions", Emotions)
                            elif field_to_edit == "behav_errors":
                                session.state["behav_errors"] = get_multi_enum_choice("Edit Behavioral Errors", BehavioralErrors)
                            elif field_to_edit == "cog_patterns":
                                session.state["cog_patterns"] = get_multi_enum_choice("Edit Cognitive Patterns", CognitivePatterns)
                            elif field_to_edit == "anxiety":
                                session.state["anxiety"] = get_mandatory_int("Edit Anxiety Level (1 to 5)", 1, 5)
                            elif field_to_edit == "impatience":
                                session.state["impatience"] = get_mandatory_int("Edit Impatience Level (1 to 5)", 1, 5)
                            elif field_to_edit == "mental_clarity":
                                session.state["mental_clarity"] = get_mandatory_int("Edit Mental Clarity Level (1 to 5)", 1, 5)
                            elif field_to_edit == "pre_trade_emotions":
                                session.state["pre_trade_emotions"] = get_mandatory_text("Edit Pre Trade Emotions")
                            elif field_to_edit == "mid_trade_emotions":
                                session.state["mid_trade_emotions"] = get_mandatory_text("Edit Mid Trade Emotions")
                            elif field_to_edit == "post_trade_emotions":
                                session.state["post_trade_emotions"] = get_mandatory_text("Edit Post Trade Emotions")
                            elif field_to_edit == "lesson_tact":
                                session.state["lesson_tact"] = get_mandatory_text("Edit Tactical Lesson Learned", multiline=True)
                            elif field_to_edit == "visual_lesson_path":
                                visual_path = handle_visual_lesson_assignment(trade_id, payload.get("asset", "Unknown"), session.state.get("visual_lesson_path", "nan"))
                                session.state["visual_lesson_path"] = visual_path

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
        
    update_record_state(trade_id, final_state, append_payload=new_payload, engine=get_active_engine())
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
        eff_tf = workspace.get("efficiency_timeframe") if (workspace and "efficiency_timeframe" in workspace) else ea.efficiency_timeframe
        struct_text.append("Eff Timeframe: ", style="dim")
        struct_text.append(f"{eff_tf}\n", style="white")
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
                if label == "P1":
                    try:
                        import json
                        raw_thesis = p_layer.thesis if hasattr(p_layer, 'thesis') else p_layer
                        fractal_data = json.loads(raw_thesis) if raw_thesis else {}
                        if not isinstance(fractal_data, dict):
                            raise ValueError()
                        
                        tact_text.append(f"   Normal Fractal:   {fractal_data.get('normal_fractal', 'nan')}\n", style="dim cyan")
                        tact_text.append(f"   Inverted Fractal: {fractal_data.get('inverted_fractal', 'nan')}\n", style="dim cyan")
                    except (Exception, ValueError):
                        # Safe fallback for legacy flat text entries
                        indented_thesis = format_indented_block(p_layer.thesis if hasattr(p_layer, 'thesis') else p_layer, indent_spaces=11, wrap_width=38)
                        tact_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
                else:
                    indented_thesis = format_indented_block(p_layer.thesis, indent_spaces=11, wrap_width=38)
                    tact_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
        else:
            tact_text.append("N/A\n", style="dim")
            
    # Tactical Audit Table Metrics
    tact_text.append("\n--- Tactical Audit Metrics ---\n", style="bold magenta")
    ta = record.tactical_audit
    if ta:
        t_comp = workspace.get("compliance") if (workspace and "compliance" in workspace) else (ta.compliance if ta else None)
        could_hit = workspace.get("could_hit_tp") if (workspace and "could_hit_tp" in workspace) else (ta.could_hit_tp if ta else None)
        entry_p = workspace.get("entry_price") if (workspace and "entry_price" in workspace) else (ta.entry_price if ta else None)
        close_p = workspace.get("closing_price") if (workspace and "closing_price" in workspace) else (ta.closing_price if ta else None)
        size = workspace.get("size") if (workspace and "size" in workspace) else (ta.size if ta else None)
        sl = workspace.get("stop_loss") if (workspace and "stop_loss" in workspace) else (ta.stop_loss if ta else None)
        tp = workspace.get("take_profit") if (workspace and "take_profit" in workspace) else (ta.take_profit if ta else None)
        mae = workspace.get("mae") if (workspace and "mae" in workspace) else (ta.mae_adverse if ta else None)
        mfe = workspace.get("mfe") if (workspace and "mfe" in workspace) else (ta.mfe_favorable if ta else None)
        lesson_tact = workspace.get("lesson_tact") if (workspace and "lesson_tact" in workspace) else (ta.lesson_learned if ta else None)
        vis_path = workspace.get("visual_lesson_path") if (workspace and "visual_lesson_path" in workspace) else (ta.visual_lesson_path if ta else None)
        
        t_entry = workspace.get("entry_time") if (workspace and "entry_time" in workspace) else (ta.entry_time if ta else None)
        t_exit = workspace.get("exit_time") if (workspace and "exit_time" in workspace) else (ta.exit_time if ta else None)
        tier = workspace.get("tier_setup") if (workspace and "tier_setup" in workspace) else (ta.tier_setup if ta else None)
        m_state = workspace.get("market_state") if (workspace and "market_state" in workspace) else (ta.market_state if ta else None)
        sess_val = workspace.get("session") if (workspace and "session" in workspace) else (ta.session if ta else None)
        e_type = workspace.get("exit_type") if (workspace and "exit_type" in workspace) else (ta.exit_type if ta else None)
        f_plan = workspace.get("followed_plan") if (workspace and "followed_plan" in workspace) else (ta.followed_plan if ta else None)
        p_emo = workspace.get("primary_emotion") if (workspace and "primary_emotion" in workspace) else (ta.primary_emotion if ta else None)
        s_type = workspace.get("setup_type") if (workspace and "setup_type" in workspace) else (ta.setup_type if ta else None)
        h_trend = workspace.get("htf_trend_context") if (workspace and "htf_trend_context" in workspace) else (ta.htf_trend_context if ta else None)
        l_trend = workspace.get("ltf_trend_context") if (workspace and "ltf_trend_context" in workspace) else (ta.ltf_trend_context if ta else None)
        c_status = workspace.get("confirmation_status") if (workspace and "confirmation_status" in workspace) else (ta.confirmation_status if ta else None)
        c_params = workspace.get("confirmation_params") if (workspace and "confirmation_params" in workspace) else (ta.confirmation_params if ta else None)
        conf_5m_15m = workspace.get("confirmation_5m_15m") if (workspace and "confirmation_5m_15m" in workspace) else (ta.confirmation_5m_15m if ta else None)
        
        anx = workspace.get("anxiety_level") if (workspace and "anxiety_level" in workspace) else (ta.anxiety_level if ta else None)
        imp = workspace.get("impatience_level") if (workspace and "impatience_level" in workspace) else (ta.impatience_level if ta else None)
        clar = workspace.get("mental_clarity_level") if (workspace and "mental_clarity_level" in workspace) else (ta.mental_clarity_level if ta else None)
        
        em_list = workspace.get("emotions") if (workspace and "emotions" in workspace) else (ta.emotions if ta else None)
        be_list = workspace.get("behavioral_errors") if (workspace and "behavioral_errors" in workspace) else (ta.behavioral_errors if ta else None)
        cp_list = workspace.get("cognitive_patterns") if (workspace and "cognitive_patterns" in workspace) else (ta.cognitive_patterns if ta else None)
        
        pre_emo = workspace.get("pre_trade_emotions") if (workspace and "pre_trade_emotions" in workspace) else (ta.pre_trade_emotions if ta else None)
        mid_emo = workspace.get("mid_trade_emotions") if (workspace and "mid_trade_emotions" in workspace) else (ta.mid_trade_emotions if ta else None)
        post_emo = workspace.get("post_trade_emotions") if (workspace and "post_trade_emotions" in workspace) else (ta.post_trade_emotions if ta else None)

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
        
        tact_text.append("Entry Time: ", style="dim")
        tact_text.append(f"{to_local_display(t_entry)}\n", style="white")
        tact_text.append("Exit Time: ", style="dim")
        tact_text.append(f"{to_local_display(t_exit)}\n", style="white")
        
        tact_text.append("Tier Setup: ", style="dim")
        tact_text.append(f"{tier}\n", style="white")
        tact_text.append("Market State: ", style="dim")
        tact_text.append(f"{m_state}\n", style="white")
        tact_text.append("Session: ", style="dim")
        tact_text.append(f"{sess_val}\n", style="white")
        tact_text.append("Exit Type: ", style="dim")
        tact_text.append(f"{e_type}\n", style="white")
        tact_text.append("Followed Plan: ", style="dim")
        tact_text.append(f"{f_plan}\n", style="white")
        tact_text.append("Setup Type: ", style="dim")
        tact_text.append(f"{s_type}\n", style="white")
        tact_text.append("HTF Trend Context: ", style="dim")
        tact_text.append(f"{h_trend}\n", style="white")
        tact_text.append("LTF Trend Context: ", style="dim")
        tact_text.append(f"{l_trend}\n", style="white")
        tact_text.append("5m/15m Conf:       ", style="dim")
        tact_text.append(f"{conf_5m_15m}\n", style="bold white")
        tact_text.append("Confirmation Status: ", style="dim")
        tact_text.append(f"{c_status}\n", style="white")
        
        tact_text.append("Primary Emotion: ", style="dim")
        tact_text.append(f"{p_emo}\n", style="white")
        tact_text.append(f"Anxiety/Impatience/Clarity Levels: {anx} / {imp} / {clar}\n", style="white")
        
        conf_str = ", ".join([str(p.value if hasattr(p, 'value') else p) for p in c_params]) if isinstance(c_params, list) else str(c_params)
        emotions_str = ", ".join([str(e.value if hasattr(e, 'value') else e) for e in em_list]) if isinstance(em_list, list) else str(em_list)
        be_str = ", ".join([str(b.value if hasattr(b, 'value') else b) for b in be_list]) if isinstance(be_list, list) else str(be_list)
        cp_str = ", ".join([str(c.value if hasattr(c, 'value') else c) for c in cp_list]) if isinstance(cp_list, list) else str(cp_list)

        tact_text.append(f"  Conf. Params:         {format_indented_block(conf_str, indent_spaces=24, first_line_flush=True, wrap_width=60)}\n", style="white")
        tact_text.append(f"  Emotions:             {format_indented_block(emotions_str, indent_spaces=24, first_line_flush=True, wrap_width=60)}\n", style="white")
        tact_text.append(f"  Behav. Errors:        {format_indented_block(be_str, indent_spaces=24, first_line_flush=True, wrap_width=60)}\n", style="white")
        tact_text.append(f"  Cognitive Pat:        {format_indented_block(cp_str, indent_spaces=24, first_line_flush=True, wrap_width=60)}\n", style="white")

        if pre_emo:
            tact_text.append(f"Pre-Trade Emotions:\n  {format_indented_block(pre_emo, 2, 38)}\n", style="white")
        if mid_emo:
            tact_text.append(f"Mid-Trade Emotions:\n  {format_indented_block(mid_emo, 2, 38)}\n", style="white")
        if post_emo:
            tact_text.append(f"Post-Trade Emotions:\n  {format_indented_block(post_emo, 2, 38)}\n", style="white")

        if lesson_tact:
            indented_lesson = format_indented_block(lesson_tact, indent_spaces=11, wrap_width=38)
            tact_text.append(f"Lesson Learned:\n  {indented_lesson}\n", style="dim italic")
            
        if vis_path and vis_path != "nan":
            tact_text.append(f"Visual Lesson: {vis_path}\n", style="dim cyan")
            
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
    meta_text.append(f"{to_local_display(record.created_at, '%Y-%m-%d %H:%M:%S')}\n", style="white")
    meta_text.append("Updated: ", style="dim")
    meta_text.append(f"{to_local_display(record.updated_at, '%Y-%m-%d %H:%M:%S')}\n\n", style="white")
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

def flow_assets_configuration():
    from sqlalchemy.orm import Session
    from sqlalchemy import select, text
    from tools.database import AssetConfig
    
    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        console.rule("[bold cyan]Assets Configuration[/bold cyan]")
        console.print()
        
        choice = inquirer.select(
            message="Assets Configuration >",
            choices=[
                Choice("add_asset", name="[1] Add New Asset"),
                Choice("delete_asset", name="[2] Delete Asset"),
                Choice("back", name="[3] Back")
            ],
            pointer=">",
            qmark=""
        ).execute()
        
        if choice == "back":
            return
        elif choice == "add_asset":
            flow_add_whitelisted_asset()
            input("Press Enter to continue...")
        elif choice == "delete_asset":
            engine = get_active_engine()
            with Session(engine) as session:
                assets = session.scalars(select(AssetConfig)).all()
                if not assets:
                    console.print("[red]No assets found in registry.[/red]")
                    input("Press Enter to continue...")
                    continue
                
                asset_choices = [Choice(a.asset_name, name=a.asset_name) for a in assets]
                asset_choices.append(Choice("cancel", name="Cancel"))
                
                del_target = inquirer.select(
                    message="Select asset to delete >",
                    choices=asset_choices,
                    pointer=">",
                    qmark=""
                ).execute()
                
                if del_target == "cancel":
                    continue
                
                count1 = session.scalar(text("SELECT COUNT(*) FROM unified_department WHERE asset = :asset"), {"asset": del_target})
                count2 = session.scalar(text("SELECT COUNT(t.id) FROM tactical_audit t JOIN unified_department u ON t.id = u.id WHERE u.asset = :asset"), {"asset": del_target})
                
                if count1 > 0 or count2 > 0:
                    console.print("[bold red]Aborting: Cannot delete asset. There are active trades or analysis entries linked to this asset in the current Flight Session.[/bold red]")
                    input("Press Enter to continue...")
                    continue
                    
                confirm = inquirer.text(message=f"Type 'yes' to permanently delete {del_target}").execute()
                if confirm == "yes":
                    session.execute(text("DELETE FROM asset_config WHERE asset_name = :asset"), {"asset": del_target})
                    session.commit()
                    console.print(f"[green]Successfully deleted {del_target}.[/green]")
                else:
                    console.print("[yellow]Deletion cancelled.[/yellow]")
                input("Press Enter to continue...")

def flow_add_whitelisted_asset():
    console.print("\n[bold cyan]Add New Asset to Whitelist[/bold cyan]")
    asset_name = get_mandatory_text("Enter Asset Tick Symbol (e.g., BTCUSDT.P)")
    
    from sqlalchemy.orm import Session
    from sqlalchemy import select, text
    from tools.database import AssetConfig
    
    engine = get_active_engine()
    with Session(engine) as session:
        existing = session.scalar(select(AssetConfig).where(AssetConfig.asset_name == asset_name))
        if existing:
            console.print(f"[bold red]Error: Asset '{asset_name}' already exists in the whitelist.[/bold red]")
            return
            
        # Parameterized insert query
        session.execute(
            text("INSERT INTO asset_config (asset_name, category, code, display_name, active) VALUES (:name, :category, :code, :display_name, :active)"),
            {
                "name": asset_name,
                "category": "Crypto",
                "code": asset_name,
                "display_name": asset_name,
                "active": True
            }
        )
        session.commit()
        console.print(f"[green]Successfully added '{asset_name}' to the asset whitelist.[/green]")

def flow_repair_analysis_audits():
    from tools.database import UnifiedDepartment, EfficiencyAudit as DbEfficiencyAudit, TacticalAudit as DbTacticalAudit
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    import json
    
    while True:
        try:
            console.clear(home=True)
        except TypeError:
            console.clear()
            
        console.rule("[bold cyan]Database Administrative Workspace: Repair Executed Analysis & Audits[/bold cyan]")
        console.print()
        
        with Session(get_active_engine()) as db_session:
            stmt = select(UnifiedDepartment)
            records = db_session.scalars(stmt).all()
            
            if not records:
                console.print("[yellow]No trade sessions logged in the database.[/yellow]\n")
                input("Press Enter to continue...")
                return
                
            from rich.table import Table
            from rich import box
            table = Table(title="Database Ledger", box=box.ROUNDED, border_style="magenta", expand=False)
            table.add_column("#", justify="center", style="bold yellow", width=4)
            table.add_column("Short ID", justify="center", style="cyan", no_wrap=True, width=14)
            table.add_column("Asset", justify="center", style="bold white", no_wrap=True, width=12)
            table.add_column("Market Bias", justify="center", no_wrap=True, width=14)
            table.add_column("Calc Edge", justify="right", no_wrap=True, width=10)
            table.add_column("Created At", justify="center", style="dim cyan", no_wrap=True, width=16)
            table.add_column("Audit Status", justify="center", no_wrap=True, width=14)
            
            choices = []
            
            for idx, r in enumerate(records):
                ts_str = to_local_display(r.created_at)
                bias_val = r.market_bias or "Neutral"
                edge_val = f"{r.calc_edge:.3f}" if r.calc_edge is not None else "N/A"
                
                is_completed = r.efficiency_audit is not None and r.tactical_audit is not None
                status_str = "[bold green]Finalized[/bold green]" if is_completed else "[bold yellow]Pending[/bold yellow]"
                
                table.add_row(
                    f"\\[{idx + 1}]",
                    r.id[:8],
                    r.asset,
                    bias_val,
                    edge_val,
                    ts_str,
                    status_str
                )
                
                choice_name = f"[{idx + 1}] Inspect Record {r.id[:8]} | {r.asset}"
                choices.append(Choice(r.id, name=choice_name))
                
            console.print(table)
            console.print()
            
            choices.append(Choice("back", name="[Back to Analysis Modification Menu]"))
            
            selected_id = inquirer.select(
                message="Select Trade to Inspect and Repair >",
                choices=choices,
                pointer=">",
                qmark=""
            ).execute()
            
            if selected_id == "back":
                return
                
            record = db_session.get(UnifiedDepartment, selected_id)
            if not record:
                console.print("[red]Error: Record not found.[/red]")
                input("Press Enter to continue...")
                continue
                
            # Fetch P-layers
            layers = record.analysis_layers
            p0 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P0'), None)
            p2 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P2'), None)
            p3 = next((l for l in layers if l.department == 'EFFICIENCY' and l.layer_name == 'P3'), None)
            p4 = next((l for l in layers if l.department == 'TACTICAL' and l.layer_name == 'P4'), None)
            p1 = next((l for l in layers if l.department == 'TACTICAL' and l.layer_name == 'P1'), None)
            
            # Initialize defaults extracting attributes directly from related ORM model instances
            ea = getattr(record, "efficiency_audit", None)
            ta = getattr(record, "tactical_audit", None)

            ea_defaults = {
                "bias_a": ea.bias_a if (ea and hasattr(ea, "bias_a") and ea.bias_a) else "Choppy / Neutral",
                "resolution_type": ea.resolution_type if (ea and hasattr(ea, "resolution_type") and ea.resolution_type) else "Open",
                "real_bias_b": ea.real_bias_b if (ea and hasattr(ea, "real_bias_b") and ea.real_bias_b) else "Choppy / Neutral",
                "structural_resolution": ea.structural_resolution if (ea and hasattr(ea, "structural_resolution") and ea.structural_resolution) else "Choppy / Neutral",
                "failure_reason": ea.failure_reason if (ea and hasattr(ea, "failure_reason") and ea.failure_reason) else "N/A",
                "specific_bias_compliance": ea.specific_bias_compliance if (ea and hasattr(ea, "specific_bias_compliance") and ea.specific_bias_compliance) else "N/A",
                "false_regime_rate": ea.false_regime_rate if (ea and hasattr(ea, "false_regime_rate") and ea.false_regime_rate) else "N/A",
                "lesson_learned": ea.lesson_learned if (ea and hasattr(ea, "lesson_learned") and ea.lesson_learned) else "",
                "lesson_eff": ea.lesson_learned if (ea and hasattr(ea, "lesson_learned") and ea.lesson_learned) else "",
                "efficiency_timeframe": ea.efficiency_timeframe if (ea and hasattr(ea, "efficiency_timeframe") and ea.efficiency_timeframe) else "1H"
            }
            
            ta_defaults = {
                "compliance": ta.compliance if (ta and hasattr(ta, "compliance") and ta.compliance) else "Invalid_edge",
                "confirmation_5m_15m": ta.confirmation_5m_15m if (ta and hasattr(ta, "confirmation_5m_15m") and ta.confirmation_5m_15m) else "no",
                "followed_plan": ta.followed_plan if (ta and hasattr(ta, "followed_plan") and ta.followed_plan) else "Skip",
                "entry_time": ta.entry_time if (ta and hasattr(ta, "entry_time") and ta.entry_time) else None,
                "exit_time": ta.exit_time if (ta and hasattr(ta, "exit_time") and ta.exit_time) else None,
                "tier_setup": ta.tier_setup if (ta and hasattr(ta, "tier_setup") and ta.tier_setup) else None,
                "market_state": ta.market_state if (ta and hasattr(ta, "market_state") and ta.market_state) else None,
                "session": ta.session if (ta and hasattr(ta, "session") and ta.session) else None,
                "exit_type": ta.exit_type if (ta and hasattr(ta, "exit_type") and ta.exit_type) else None,
                "trade_decision": ta.trade_decision if (ta and hasattr(ta, "trade_decision") and ta.trade_decision) else None,
                "primary_emotion": ta.primary_emotion if (ta and hasattr(ta, "primary_emotion") and ta.primary_emotion) else None,
                "setup_type": ta.setup_type if (ta and hasattr(ta, "setup_type") and ta.setup_type) else None,
                "htf_trend_context": ta.htf_trend_context if (ta and hasattr(ta, "htf_trend_context") and ta.htf_trend_context) else None,
                "ltf_trend_context": ta.ltf_trend_context if (ta and hasattr(ta, "ltf_trend_context") and ta.ltf_trend_context) else None,
                "confirmation_status": ta.confirmation_status if (ta and hasattr(ta, "confirmation_status") and ta.confirmation_status) else None,
                "anxiety_level": ta.anxiety_level if (ta and hasattr(ta, "anxiety_level") and ta.anxiety_level) else 0,
                "impatience_level": ta.impatience_level if (ta and hasattr(ta, "impatience_level") and ta.impatience_level) else 0,
                "mental_clarity_level": ta.mental_clarity_level if (ta and hasattr(ta, "mental_clarity_level") and ta.mental_clarity_level) else 0,
                "emotions": ta.emotions if (ta and hasattr(ta, "emotions") and ta.emotions) else [],
                "behavioral_errors": ta.behavioral_errors if (ta and hasattr(ta, "behavioral_errors") and ta.behavioral_errors) else [],
                "cognitive_patterns": ta.cognitive_patterns if (ta and hasattr(ta, "cognitive_patterns") and ta.cognitive_patterns) else [],
                "risk_usd": ta.risk_usd if (ta and hasattr(ta, "risk_usd") and ta.risk_usd) else 0.0,
                "size": ta.size if (ta and hasattr(ta, "size") and ta.size) else 0.0,
                "r_r": ta.r_r if (ta and hasattr(ta, "r_r") and ta.r_r) else 0.0,
                "entry_price": ta.entry_price if (ta and hasattr(ta, "entry_price") and ta.entry_price) else 0.0,
                "closing_price": ta.closing_price if (ta and hasattr(ta, "closing_price") and ta.closing_price) else 0.0,
                "could_hit_tp": ta.could_hit_tp if (ta and hasattr(ta, "could_hit_tp") and ta.could_hit_tp is not None) else None,
                "take_profit": ta.take_profit if (ta and hasattr(ta, "take_profit") and ta.take_profit) else 0.0,
                "stop_loss": ta.stop_loss if (ta and hasattr(ta, "stop_loss") and ta.stop_loss) else 0.0,
                "pnl_and_cost": ta.pnl_and_cost if (ta and hasattr(ta, "pnl_and_cost") and ta.pnl_and_cost) else 0.0,
                "mae": ta.mae_adverse if (ta and hasattr(ta, "mae_adverse") and ta.mae_adverse) else 0.0,
                "mfe": ta.mfe_favorable if (ta and hasattr(ta, "mfe_favorable") and ta.mfe_favorable) else 0.0,
                "notional_size": ta.notional_size if (ta and hasattr(ta, "notional_size") and ta.notional_size) else 0.0,
                "capital_at_risk": ta.capital_at_risk if (ta and hasattr(ta, "capital_at_risk") and ta.capital_at_risk) else 0.0,
                "lesson_tact": ta.lesson_learned if (ta and hasattr(ta, "lesson_learned") and ta.lesson_learned) else "",
                "visual_lesson_path": ta.visual_lesson_path if (ta and hasattr(ta, "visual_lesson_path") and ta.visual_lesson_path) else "nan",
                "pre_trade_emotions": ta.pre_trade_emotions if (ta and hasattr(ta, "pre_trade_emotions") and ta.pre_trade_emotions) else "",
                "mid_trade_emotions": ta.mid_trade_emotions if (ta and hasattr(ta, "mid_trade_emotions") and ta.mid_trade_emotions) else "",
                "post_trade_emotions": ta.post_trade_emotions if (ta and hasattr(ta, "post_trade_emotions") and ta.post_trade_emotions) else "",
                "confirmation_params": ta.confirmation_params if (ta and hasattr(ta, "confirmation_params") and ta.confirmation_params) else []
            }
                
            # Build global workspace
            workspace = {
                "asset": record.asset,
                "market_bias": record.market_bias,
                "calc_edge": record.calc_edge,
                "edge_description": record.edge_description or "",
                "p4_hierarchy": record.p4_hierarchy,
                "p1_timeframe": record.p1_timeframe,
                "p1_type": record.p1_type,
                "nodes_l1": record.nodes_l1,
                "nodes_l2": record.nodes_l2,
                "tactical_classification": record.tactical_classification,
                "long_prob": record.long_prob,
                "short_prob": record.short_prob,
                "no_trade_prob": record.no_trade_prob,
                "trade_status": record.trade_status or None,
                "edge_validation_price": record.edge_validation_price,
                "structural_invalidation": record.structural_invalidation,
                
                "p0_dir": p0.direction if p0 else "Neutral",
                "p0_str": p0.strength if p0 else "Weak",
                "p0_thesis": p0.thesis if p0 else "",
                
                "p2_dir": p2.direction if p2 else "Neutral",
                "p2_str": p2.strength if p2 else "Weak",
                "p2_thesis": p2.thesis if p2 else "",
                
                "p3_dir": p3.direction if p3 else "Neutral",
                "p3_str": p3.strength if p3 else "Weak",
                "p3_thesis": p3.thesis if p3 else "",
                
                "p4_dir": p4.direction if p4 else "Neutral",
                "p4_str": p4.strength if p4 else "Weak",
                "p4_thesis": p4.thesis if p4 else "",
                
                "p1_dir": p1.direction if p1 else "Neutral",
                "p1_str": p1.strength if p1 else "Weak",
                "p1_thesis": p1.thesis if p1 else "",
                
                **ea_defaults,
                **ta_defaults
            }
            
            # Inspection Sub-Menu
            while True:
                try:
                    console.clear(home=True)
                except TypeError:
                    console.clear()
                    
                console.rule(f"[bold cyan]Inspect / Repair: [{record.id[:8]}] {workspace['asset']}[/bold cyan]")
                console.print()
                
                comp_choice = inquirer.select(
                    message="Select Component to Inspect/Repair >",
                    choices=[
                        Choice("unified", name="[1] View & Edit Unified Analysis (P0 -> P4)"),
                        Choice("efficiency", name="[2] View & Edit Efficiency Audit"),
                        Choice("tactical", name="[3] View & Edit Tactical Audit"),
                        Choice("datetime", name="[4] View & Edit Date/Time Metadata"),
                        Choice("back", name="[5] Back"),
                        Choice("delete_record", name="[DELETE] Permanently Purge Trade Record From Database")
                    ],
                    pointer=">",
                    qmark=""
                ).execute()
                
                if comp_choice == "back":
                    break
                    
                elif comp_choice == "delete_record":
                    import os
                    import json
                    
                    warn_text = Text()
                    warn_text.append("CRITICAL WARNING: DESTRUCTIVE ACTION\n\n", style="bold red")
                    warn_text.append(f"You are about to permanently purge Trade ID: {record.id}\n", style="white")
                    warn_text.append("This will cascade-delete:\n", style="dim")
                    warn_text.append(" - Unified Department Parent Entity Row Data\n", style="dim")
                    warn_text.append(" - Efficiency and Tactical Audit Metadata Framework Metrics\n", style="dim")
                    warn_text.append(" - All 5 Analysis Layers (P0, P1, P2, P3, P4) and structural payloads\n\n", style="dim")
                    warn_text.append("WARNING: This will cause desynchronization if the record has already been exported to Notion.", style="bold yellow")
                    
                    console.print(Panel(warn_text, title="[DANGER - PURGE SYSTEM]", border_style="bold red"))
                    console.print()
                    
                    confirm_purge = inquirer.text(
                        message='Type "yes" to confirm deletion or press Enter to cancel >',
                        qmark="!"
                    ).execute()
                    
                    if confirm_purge == "yes":
                        try:
                            raw_conn = db_session.connection().connection
                            
                            # Force SQLite runtime foreign key checking enforcement
                            raw_conn.execute("PRAGMA foreign_keys = ON;")
                            
                            # Execute parent deletion to trigger database level engine cascades
                            raw_conn.execute("DELETE FROM unified_department WHERE id = ?;", (record.id,))
                            
                            # Evict from localized session JSON caches to maintain application state integrity
                            if os.path.exists(".data/paused_audits.json"):
                                try:
                                    with open(".data/paused_audits.json", "r") as f:
                                        cache = json.load(f)
                                    if record.id in cache:
                                        del cache[record.id]
                                        with open(".data/paused_audits.json", "w") as f:
                                            json.dump(cache, f)
                                except Exception:
                                    pass
                                    
                            db_session.commit()
                            console.print("[success]Trade record and associated sub-components successfully purged from database and cache layers.[/success]")
                            input("Press Enter to return to record index...")
                            break # Exits the record inspection loop to refresh the general ledger
                        except Exception as e:
                            db_session.rollback()
                            console.print(f"[bold red]Critical Deletion Transaction Failure: {e}[/bold red]")
                            input("Press Enter to continue...")
                    else:
                        console.print("[warning]Deletion cancelled.[/warning]")
                        input("Press Enter to continue...")
                    
                elif comp_choice == "unified":
                    def recalculate_unified_metrics(w):
                        # Multi-Layer Recalculation Pipeline (Defect 3)
                        def get_dir_val(d): return 1 if d == "Long" else -1 if d == "Short" else 0
                        def get_str_val(s): return 2 if s == "Strong" else 1 if s == "Mid" else 0

                        x0 = get_dir_val(w["p0_dir"]) * get_str_val(w["p0_str"])
                        x1 = get_dir_val(w["p1_dir"]) * get_str_val(w["p1_str"])
                        x2 = get_dir_val(w["p2_dir"]) * get_str_val(w["p2_str"])
                        x3 = get_dir_val(w["p3_dir"]) * get_str_val(w["p3_str"])
                        x4 = get_dir_val(w["p4_dir"]) * get_str_val(w["p4_str"])

                        val = (0.30 * x0 + 0.25 * x1 + 0.15 * x2 + 0.10 * x3 + 0.20 * x4) / 2.0
                        w["calc_edge"] = val

                        if abs(val) < 0.26:
                            w["market_bias"] = "Choppy / Neutral"
                        elif val >= 0.26:
                            w["market_bias"] = "Bullish"
                        else:
                            w["market_bias"] = "Bearish"

                        from core.math_engine import calculate_probabilities as _calc_probs
                        _probs = _calc_probs(val)
                        w["long_prob"] = float(_probs.long_prob)
                        w["short_prob"] = float(_probs.short_prob)
                        w["no_trade_prob"] = float(_probs.no_trade_prob)


                    while True:
                        struct_text = Text()
                        struct_text.append(f"Asset: {workspace['asset']}  |  Market Bias: ", style="dim")
                        bias_style = "bold green" if workspace["market_bias"] == "Bullish" else "bold red" if workspace["market_bias"] == "Bearish" else "bold yellow"
                        struct_text.append(f"{workspace['market_bias']}\n\n", style=bias_style)
                        
                        for label in ["P0", "P2", "P3"]:
                            struct_text.append(f"{label}: ", style="bold cyan")
                            d = workspace[f"{label.lower()}_dir"]
                            s = workspace[f"{label.lower()}_str"]
                            t = workspace[f"{label.lower()}_thesis"]
                            d_style = "green" if d == "Long" else "red" if d == "Short" else "yellow"
                            struct_text.append(f"{d.upper()}", style=d_style)
                            struct_text.append(" | ", style="dim")
                            s_style = "bold" if s == "Strong" else ""
                            struct_text.append(f"{s.upper()}\n", style=s_style)
                            if t:
                                indented_thesis = format_indented_block(t, indent_spaces=11, wrap_width=38)
                                struct_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
                                
                        tact_text = Text()
                        tact_text.append(f"Hierarchy: {workspace['p4_hierarchy']}  |  Timeframe: {workspace['p1_timeframe']}  |  Fractal: {workspace['p1_type']}\n", style="dim")
                        tact_text.append(f"Nodes L1/L2: {workspace['nodes_l1']} / {workspace['nodes_l2']}\n\n", style="dim")
                        
                        for label in ["P4", "P1"]:
                            tact_text.append(f"{label}: ", style="bold magenta")
                            d = workspace[f"{label.lower()}_dir"]
                            s = workspace[f"{label.lower()}_str"]
                            t = workspace[f"{label.lower()}_thesis"]
                            d_style = "green" if d == "Long" else "red" if d == "Short" else "yellow"
                            tact_text.append(f"{d.upper()}", style=d_style)
                            tact_text.append(" | ", style="dim")
                            s_style = "bold" if s == "Strong" else ""
                            tact_text.append(f"{s.upper()}\n", style=s_style)
                            if t:
                                indented_thesis = format_indented_block(t, indent_spaces=11, wrap_width=38)
                                tact_text.append(f"   Thesis: {indented_thesis}\n", style="dim italic")
                                
                        quant_text = Text()
                        quant_text.append(f"Calc Edge: {workspace['calc_edge']:.4f}\n", style="bold yellow")
                        quant_text.append(f"Long Prob: {workspace['long_prob']*100:.1f}%  |  Short Prob: {workspace['short_prob']*100:.1f}%  |  No-Trade Prob: {workspace['no_trade_prob']*100:.1f}%\n", style="dim")
                        quant_text.append(f"Tactical Classification: {workspace['tactical_classification']}\n", style="cyan")
                        
                        grid = Table.grid(expand=True)
                        grid.add_column(ratio=50)
                        grid.add_column(ratio=50)
                        grid.add_row(
                            Panel(struct_text, title="[bold cyan]Structural Vector Panel[/bold cyan]", border_style="cyan"),
                            Panel(tact_text, title="[bold magenta]Tactical Vector Panel[/bold magenta]", border_style="magenta")
                        )
                        grid.add_row(
                            Panel(quant_text, title="[bold yellow]Quantitative Metrics[/bold yellow]", border_style="yellow"),
                            Panel(f"Description: {workspace['edge_description']}", title="[bold white]Edge Description[/bold white]", border_style="white")
                        )
                        
                        dashboard = Panel(
                            grid,
                            title=f"[white]Unified Analysis Repair Screen: {record.id[:8]}[/white]",
                            border_style="bold blue",
                            box=box.DOUBLE
                        )
                        
                        try:
                            console.clear(home=True)
                        except TypeError:
                            console.clear()
                        console.print(dashboard)
                        
                        action = inquirer.select(
                            message="Action >",
                            choices=[
                                Choice("edit", name="[1] Edit a Field"),
                                Choice("save", name="[2] Confirm & Save Repair"),
                                Choice("back", name="[3] Discard")
                            ],
                            pointer=">",
                            qmark=""
                        ).execute()
                        
                        if action == "back":
                            break
                        elif action == "edit":
                            try:
                                import json
                                import os
                                p1_data = json.loads(workspace.get("p1_thesis", "{}"))
                                if not isinstance(p1_data, dict):
                                    p1_data = {}
                            except Exception:
                                p1_data = {}
                            
                            nf_name = os.path.basename(p1_data.get("normal_fractal", "nan"))
                            if_name = os.path.basename(p1_data.get("inverted_fractal", "nan"))
                            
                            edit_choices = [
                                Choice("asset", name=f"Asset: {workspace['asset']}"),
                                Choice("edge_description", name=f"Edge Description: {workspace['edge_description']}"),
                                Choice("edge_validation_price", name=f"[Edit] Edge Validation Price: {workspace.get('edge_validation_price', 'N/A')}"),
                                Choice("structural_invalidation", name=f"[Edit] Structural Invalidation Price: {workspace.get('structural_invalidation', 'N/A')}"),
                                Choice("efficiency_timeframe", name=f"[Edit] Efficiency Timeframe: {workspace.get('efficiency_timeframe', '1H')}"),
                                Choice("p4_hierarchy", name=f"P4 Hierarchy: {workspace['p4_hierarchy']}"),
                                Choice("p1_timeframe", name=f"P1 Timeframe: {workspace['p1_timeframe']}"),
                                Choice("p1_type", name=f"P1 Fractal Type: {workspace['p1_type']}"),
                                Choice("nodes_l1", name=f"Nodes L1: {workspace['nodes_l1']}"),
                                Choice("nodes_l2", name=f"Nodes L2: {workspace['nodes_l2']}"),
                                Choice("tactical_classification", name=f"Tactical Classification: {workspace['tactical_classification']}"),
                                Choice("p0_dir", name=f"P0 Direction: {workspace['p0_dir']}"),
                                Choice("p0_str", name=f"P0 Strength: {workspace['p0_str']}"),
                                Choice("p0_thesis", name=f"P0 Thesis: {workspace['p0_thesis'][:25]}..."),
                                Choice("p2_dir", name=f"P2 Direction: {workspace['p2_dir']}"),
                                Choice("p2_str", name=f"P2 Strength: {workspace['p2_str']}"),
                                Choice("p2_thesis", name=f"P2 Thesis: {workspace['p2_thesis'][:25]}..."),
                                Choice("p3_dir", name=f"P3 Direction: {workspace['p3_dir']}"),
                                Choice("p3_str", name=f"P3 Strength: {workspace['p3_str']}"),
                                Choice("p3_thesis", name=f"P3 Thesis: {workspace['p3_thesis'][:25]}..."),
                                Choice("p4_dir", name=f"P4 Direction: {workspace['p4_dir']}"),
                                Choice("p4_str", name=f"P4 Strength: {workspace['p4_str']}"),
                                Choice("p4_thesis", name=f"P4 Thesis: {workspace['p4_thesis'][:25]}..."),
                                Choice("p1_dir", name=f"P1 Direction: {workspace['p1_dir']}"),
                                Choice("p1_str", name=f"P1 Strength: {workspace['p1_str']}"),
                                Choice("p1_thesis", name=f"P1 Fractals -> Normal: {nf_name} | Inverted: {if_name}"),
                                Choice("back", name="[<] Back")
                            ]
                            
                            field = inquirer.select(
                                message="Select Field to Edit >",
                                choices=edit_choices,
                                pointer=">",
                                qmark=""
                            ).execute()
                            
                            if field == "back":
                                continue
                                
                            if field == "asset":
                                workspace["asset"] = get_mandatory_text("Enter Asset Name (e.g. BTC/USDT)")
                            elif field == "edge_description":
                                workspace["edge_description"] = get_mandatory_text("Enter Edge Description")
                            elif field == "edge_validation_price":
                                try:
                                    val = get_mandatory_text("Enter Edge Validation Price (Target Convergence) [Leave empty for None]")
                                    workspace["edge_validation_price"] = Decimal(val) if val.strip() else None
                                except Exception:
                                    console.print("[bold red]Invalid decimal input. Aborting modification.[/bold red]")
                            elif field == "structural_invalidation":
                                try:
                                    val = get_mandatory_text("Enter Structural Invalidation Price [Leave empty for None]")
                                    workspace["structural_invalidation"] = Decimal(val) if val.strip() else None
                                except Exception:
                                    console.print("[bold red]Invalid decimal input. Aborting modification.[/bold red]")
                            elif field == "efficiency_timeframe":
                                workspace["efficiency_timeframe"] = inquirer.select(
                                    message="Edit Efficiency Timeframe [15M/1H/4H] >",
                                    choices=[Choice("15M", name="15M"), Choice("1H", name="1H"), Choice("4H", name="4H")],
                                    pointer=">",
                                    qmark=""
                                ).execute()
                            elif field == "p4_hierarchy":
                                workspace["p4_hierarchy"] = get_enum_choice("Edit P4 Hierarchy", Hierarchy).value
                            elif field == "p1_timeframe":
                                workspace["p1_timeframe"] = get_enum_choice("Edit P1 Timeframe", Timeframe).value
                            elif field == "p1_type":
                                workspace["p1_type"] = get_enum_choice("Edit P1 Fractal Type", FractalType).value
                            elif field in ["nodes_l1", "nodes_l2"]:
                                workspace[field] = get_mandatory_int(f"Enter {field.replace('_', ' ').upper()}", 0, 100)
                            elif field == "tactical_classification":
                                workspace["tactical_classification"] = get_enum_choice("Edit Tactical Classification", TacticalClassification).value
                            elif field in ["p0_dir", "p2_dir", "p3_dir", "p4_dir", "p1_dir"]:
                                workspace[field] = get_enum_choice(f"Edit {field.replace('_dir', '').upper()} Direction", Direction).value
                                recalculate_unified_metrics(workspace)
                            elif field in ["p0_str", "p2_str", "p3_str", "p4_str", "p1_str"]:
                                workspace[field] = get_enum_choice(f"Edit {field.replace('_str', '').upper()} Strength", Strength).value
                                recalculate_unified_metrics(workspace)
                            elif field in ["p0_thesis", "p2_thesis", "p3_thesis", "p4_thesis"]:
                                workspace[field] = get_mandatory_text(f"Edit {field.replace('_thesis', '').upper()} Thesis", multiline=True)
                            elif field == "p1_thesis":
                                import json
                                try:
                                    existing_data = json.loads(workspace.get("p1_thesis", "{}"))
                                    if not isinstance(existing_data, dict):
                                        existing_data = {}
                                except Exception:
                                    existing_data = {}
                                    
                                console.print("[cyan]Updating P1 Normal Fractal Image...[/cyan]")
                                new_normal = handle_visual_lesson_assignment(selected_id, workspace.get("asset", "Unknown"), existing_data.get("normal_fractal", "nan"), "_NF")
                                
                                console.print("[cyan]Updating P1 Inverted Fractal Image...[/cyan]")
                                new_inverted = handle_visual_lesson_assignment(selected_id, workspace.get("asset", "Unknown"), existing_data.get("inverted_fractal", "nan"), "_IF")
                                
                                workspace["p1_thesis"] = json.dumps({"normal_fractal": new_normal, "inverted_fractal": new_inverted})
                                
                        elif action == "save":
                            try:
                                raw_conn = db_session.connection().connection
                                raw_conn.execute("""
                                    UPDATE unified_department SET 
                                        asset = ?,
                                        market_bias = ?,
                                        calc_edge = ?,
                                        edge_description = ?,
                                        p4_hierarchy = ?,
                                        p1_timeframe = ?,
                                        p1_type = ?,
                                        nodes_l1 = ?,
                                        nodes_l2 = ?,
                                        tactical_classification = ?,
                                        long_prob = ?,
                                        short_prob = ?,
                                        no_trade_prob = ?
                                    WHERE id = ?
                                """, (
                                    workspace["asset"],
                                    workspace["market_bias"],
                                    float(workspace["calc_edge"]),
                                    workspace["edge_description"],
                                    workspace["p4_hierarchy"],
                                    workspace["p1_timeframe"],
                                    workspace["p1_type"],
                                    int(workspace["nodes_l1"]),
                                    int(workspace["nodes_l2"]),
                                    workspace["tactical_classification"],
                                    float(workspace["long_prob"]),
                                    float(workspace["short_prob"]),
                                    float(workspace["no_trade_prob"]),
                                    record.id
                                ))
                                
                                # Update analysis_layer (singular table name on disk, Defect 4)
                                for l_name in ["P0", "P2", "P3", "P4", "P1"]:
                                    dept = "EFFICIENCY" if l_name in ["P0", "P2", "P3"] else "TACTICAL"
                                    exists = raw_conn.execute(
                                        "SELECT 1 FROM analysis_layer WHERE trade_id = ? AND department = ? AND layer_name = ?",
                                        (record.id, dept, l_name)
                                    ).fetchone()
                                    
                                    dir_val = workspace[f"{l_name.lower()}_dir"]
                                    str_val = workspace[f"{l_name.lower()}_str"]
                                    thesis_val = workspace[f"{l_name.lower()}_thesis"]
                                    
                                    score_val = 0
                                    if dir_val != "Neutral":
                                        d_weight = 1 if dir_val == "Long" else -1
                                        s_weight = 2 if str_val == "Strong" else 1 if str_val == "Mid" else 0
                                        score_val = d_weight * s_weight
                                        
                                    if exists:
                                        raw_conn.execute("""
                                            UPDATE analysis_layer SET 
                                                direction = ?,
                                                strength = ?,
                                                thesis = ?,
                                                score = ?
                                            WHERE trade_id = ? AND department = ? AND layer_name = ?
                                        """, (dir_val, str_val, thesis_val, score_val, record.id, dept, l_name))
                                    else:
                                        raw_conn.execute("""
                                            INSERT INTO analysis_layer (trade_id, department, layer_name, direction, strength, thesis, score)
                                            VALUES (?, ?, ?, ?, ?, ?, ?)
                                        """, (record.id, dept, l_name, dir_val, str_val, thesis_val, score_val))
                                        
                                record.edge_validation_price = workspace.get("edge_validation_price")
                                record.structural_invalidation = workspace.get("structural_invalidation")
                                if getattr(record, "efficiency_audit", None):
                                    record.efficiency_audit.efficiency_timeframe = workspace.get("efficiency_timeframe")
                                        
                                db_session.commit()
                                console.print("[green]Unified Analysis Repair saved successfully.[/green]")
                            except Exception as e:
                                db_session.rollback()
                                console.print(f"[bold red]Failed to save Unified Analysis Repair: {e}[/bold red]")
                            input("Press Enter to continue...")
                            break
                            
                elif comp_choice == "efficiency":
                    while True:
                        rev_text = Text()
                        rev_text.append(f"Bias A (Original): {workspace['bias_a']}\n", style="white")
                        rev_text.append(f"Real Bias B: {workspace['real_bias_b']}\n", style="white")
                        rev_text.append(f"Resolution Type: {workspace['resolution_type']}\n", style="white")
                        rev_text.append(f"Structural Resolution: {workspace['structural_resolution']}\n", style="white")
                        rev_text.append(f"Failure Reason: {workspace['failure_reason']}\n", style="white")
                        rev_text.append(f"Specific Bias Compliance: {workspace['specific_bias_compliance']}\n", style="white")
                        rev_text.append(f"False Regime Rate: {workspace['false_regime_rate']}\n", style="white")
                        rev_text.append(f"Efficiency Timeframe: {workspace['efficiency_timeframe']}\n", style="white")
                        if workspace["lesson_eff"] and workspace["lesson_eff"] != "nan":
                            indented_lesson = format_indented_block(workspace["lesson_eff"], indent_spaces=11, wrap_width=38)
                            rev_text.append(f"Lesson Learned:\n  {indented_lesson}\n", style="dim italic")
                            
                        dashboard = Panel(
                            rev_text,
                            title=f"Efficiency Audit Repair: {record.id[:8]}",
                            border_style="cyan"
                        )
                        
                        try:
                            console.clear(home=True)
                        except TypeError:
                            console.clear()
                        console.print(dashboard)
                        
                        action = inquirer.select(
                            message="Action >",
                            choices=[
                                Choice("edit", name="[1] Edit a Field"),
                                Choice("save", name="[2] Confirm & Save Repair"),
                                Choice("back", name="[3] Discard")
                            ],
                            pointer=">",
                            qmark=""
                        ).execute()
                        
                        if action == "back":
                            break
                        elif action == "edit":
                            edit_choices = [
                                Choice("bias_a", name=f"Bias A (Original): {workspace['bias_a']}"),
                                Choice("real_bias_b", name=f"Real Bias B: {workspace['real_bias_b']}"),
                                Choice("resolution_type", name=f"Resolution Type: {workspace['resolution_type']}"),
                                Choice("structural_resolution", name=f"Structural Resolution: {workspace['structural_resolution']}"),
                                Choice("failure_reason", name=f"Failure Reason: {workspace['failure_reason']}"),
                                Choice("specific_bias_compliance", name=f"Specific Bias Compliance: {workspace['specific_bias_compliance']}"),
                                Choice("false_regime_rate", name=f"False Regime Rate: {workspace['false_regime_rate']}"),
                                Choice("efficiency_timeframe", name=f"Efficiency Timeframe: {workspace['efficiency_timeframe']}"),
                                Choice("lesson_eff", name=f"Lesson Learned: {workspace['lesson_eff'][:25]}..."),
                                Choice("back", name="[<] Back")
                            ]
                            
                            field = inquirer.select(
                                message="Select Field to Edit >",
                                choices=edit_choices,
                                pointer=">",
                                qmark=""
                            ).execute()
                            
                            if field == "back":
                                continue
                                
                            if field == "bias_a":
                                workspace["bias_a"] = get_enum_choice("Edit Initial Structural Bias (Bias A)", StructuralBias).value
                            elif field == "real_bias_b":
                                workspace["real_bias_b"] = get_enum_choice("Edit Real Bias B", StructuralBias).value
                            elif field == "resolution_type":
                                workspace["resolution_type"] = get_enum_choice("Edit Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN]).value
                            elif field == "structural_resolution":
                                workspace["structural_resolution"] = get_enum_choice("Edit Structural Resolution", StructuralResolution).value
                            elif field == "failure_reason":
                                workspace["failure_reason"] = get_enum_choice("Edit Failure Reason", FailureReason).value
                            elif field == "specific_bias_compliance":
                                workspace["specific_bias_compliance"] = inquirer.select(
                                    message="Select Specific Bias Compliance >",
                                    choices=[Choice("Valid", name="Valid"), Choice("Invalid", name="Invalid")],
                                    pointer=">",
                                    qmark=""
                                ).execute()
                            elif field == "false_regime_rate":
                                workspace["false_regime_rate"] = inquirer.select(
                                    message="Select False Regime Rate >",
                                    choices=[Choice("True Positive", name="True Positive"), Choice("False Positive", name="False Positive"), Choice("True Negative", name="True Negative"), Choice("False Negative", name="False Negative")],
                                    pointer=">",
                                    qmark=""
                                ).execute()
                            elif field == "efficiency_timeframe":
                                workspace["efficiency_timeframe"] = inquirer.select(
                                    message="Select Efficiency Timeframe >",
                                    choices=[Choice("1H", name="1H"), Choice("4H", name="4H")],
                                    pointer=">",
                                    qmark=""
                                ).execute()
                            elif field == "lesson_eff":
                                workspace["lesson_eff"] = get_optional_text("Edit Efficiency Lesson Learned")
                                
                        elif action == "save":
                            try:
                                raw_conn = db_session.connection().connection
                                exists = raw_conn.execute("SELECT 1 FROM efficiency_audit WHERE id = ?", (record.id,)).fetchone()
                                
                                now_ts = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                                if exists:
                                    raw_conn.execute("""
                                        UPDATE efficiency_audit SET 
                                            bias_a = ?,
                                            resolution_type = ?,
                                            real_bias_b = ?,
                                            structural_resolution = ?,
                                            failure_reason = ?,
                                            specific_bias_compliance = ?,
                                            false_regime_rate = ?,
                                            efficiency_timeframe = ?,
                                            lesson_learned = ?,
                                            updated_at = ?
                                        WHERE id = ?
                                    """, (
                                        workspace["bias_a"],
                                        workspace["resolution_type"],
                                        workspace["real_bias_b"],
                                        workspace["structural_resolution"],
                                        workspace["failure_reason"],
                                        workspace["specific_bias_compliance"],
                                        workspace["false_regime_rate"],
                                        workspace["efficiency_timeframe"],
                                        workspace["lesson_eff"],
                                        now_ts,
                                        record.id
                                    ))
                                else:
                                    raw_conn.execute("""
                                        INSERT INTO efficiency_audit (id, bias_a, resolution_type, real_bias_b, structural_resolution, failure_reason, specific_bias_compliance, false_regime_rate, efficiency_timeframe, lesson_learned, created_at, updated_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """, (
                                        record.id,
                                        workspace["bias_a"],
                                        workspace["resolution_type"],
                                        workspace["real_bias_b"],
                                        workspace["structural_resolution"],
                                        workspace["failure_reason"],
                                        workspace["specific_bias_compliance"],
                                        workspace["false_regime_rate"],
                                        workspace["efficiency_timeframe"],
                                        workspace["lesson_eff"],
                                        now_ts,
                                        now_ts
                                    ))
                                db_session.commit()
                                console.print("[green]Efficiency Audit Repair saved successfully.[/green]")
                            except Exception as e:
                                db_session.rollback()
                                console.print(f"[bold red]Failed to save Efficiency Audit Repair: {e}[/bold red]")
                            input("Press Enter to continue...")
                            break
                            
                elif comp_choice == "tactical":
                    def recalculate_tactical_math(w, p0_dir, p2_dir, p4_dir):
                        # Strict Two-Pass Sequenced Recalculation Engine (Amendment 2)
                        # Pass 1: Direction Resolution
                        ep = float(w["entry_price"]) if w.get("entry_price") else 0.0
                        sl = float(w["stop_loss"]) if w.get("stop_loss") else 0.0
                        size = float(w["size"]) if w.get("size") else 0.0
                        cp = float(w["closing_price"]) if w.get("closing_price") else 0.0
                        tp = float(w["take_profit"]) if w.get("take_profit") else 0.0
                        mae = float(w["mae"]) if w.get("mae") else 0.0
                        mfe = float(w["mfe"]) if w.get("mfe") else 0.0
                        
                        direction = "Long"
                        if ep != 0.0 and sl != 0.0 and ep != sl:
                            direction = "Long" if ep > sl else "Short"
                        else:
                            active_dirs = [p0_dir, p2_dir, p4_dir]
                            long_count = sum(1 for d in active_dirs if d == "Long")
                            short_count = sum(1 for d in active_dirs if d == "Short")
                            if long_count > short_count:
                                direction = "Long"
                            elif short_count > long_count:
                                direction = "Short"
                                
                        w["trade_decision"] = direction
                        
                        cost = float(w["cost"]) if w.get("cost") else 0.0
                        
                        # Pass 2: Calculated Math passing the resolved direction as an input parameter
                        try:
                            metrics = calculate_algebraic_metrics(direction, ep, sl, cp, tp, size, mae, mfe, cost)
                            w.update(metrics)
                        except ValueError as e:
                            console.print(f"[bold red]Validation Error: {e}[/bold red]")
                            w.update({
                                "notional_size": Decimal('0.0'),
                                "notional_size_usd": Decimal('0.0'),
                                "dist_to_sl": Decimal('0.0'),
                                "dist_to_tp": Decimal('0.0'),
                                "capital_at_risk": Decimal('0.0'),
                                "risk_usd": Decimal('0.0'),
                                "pnl": Decimal('0.0'),
                                "pnl_and_cost": Decimal('0.0'),
                                "cost": Decimal('0.0'),
                                "r_r": Decimal('0.0'),
                                "r_multiple": Decimal('0.0'),
                                "captured_mfe": Decimal('0.0'),
                                "captured_mae": Decimal('0.0')
                            })
                        
                    # Run initial recalculation pass
                    recalculate_tactical_math(workspace, workspace["p0_dir"], workspace["p2_dir"], workspace["p4_dir"])
                    
                    while True:
                        rev_text = Text()
                        # --- Core Inputs ---
                        rev_text.append("--- Core Inputs ---\n", style="bold green")
                        rev_text.append(f"Trade Status: {workspace['trade_status']}\n", style="white")
                        rev_text.append(f"Compliance: {workspace['compliance']}\n", style="white")
                        rev_text.append(f"Entry Price: {workspace['entry_price']}\n", style="white")
                        rev_text.append(f"Closing Price: {workspace['closing_price']}\n", style="white")
                        rev_text.append(f"Size: {workspace['size']}\n", style="white")
                        rev_text.append(f"Stop Loss: {workspace['stop_loss']}\n", style="white")
                        rev_text.append(f"Take Profit: {workspace['take_profit']}\n", style="white")
                        rev_text.append(f"MAE Adverse: {workspace['mae']}\n", style="white")
                        rev_text.append(f"MFE Favorable: {workspace['mfe']}\n", style="white")
                        rev_text.append(f"Could Hit TP: {workspace['could_hit_tp']}\n", style="white")
                        rev_text.append(f"Entry Time: {to_local_display(workspace.get('entry_time'))}\n", style="white")
                        rev_text.append(f"Exit Time: {to_local_display(workspace.get('exit_time'))}\n", style="white")
                        
                        # --- Distance Calculations ---
                        rev_text.append("\n--- Distance Calculations ---\n", style="bold cyan")
                        rev_text.append(f"Dist to SL: {workspace.get('dist_to_sl', 0.0):.4f}\n", style="white")
                        rev_text.append(f"Dist to TP: {workspace.get('dist_to_tp', 0.0):.4f}\n", style="white")
                        
                        # --- Calculated Algebraic Metrics ---
                        rev_text.append("\n--- Calculated Algebraic Metrics ---\n", style="bold yellow")
                        rev_text.append(f"Resolved Direction: {workspace['trade_decision']}\n", style="bold cyan")
                        rev_text.append(f"Notional Size: {workspace.get('notional_size', 0.0):.2f}\n", style="white")
                        rev_text.append(f"Notional Size USD: {workspace.get('notional_size_usd', 0.0):.2f}\n", style="white")
                        rev_text.append(f"Capital At Risk: {workspace.get('capital_at_risk', 0.0):.2f}\n", style="white")
                        rev_text.append(f"Risk USD: {workspace.get('risk_usd', 0.0):.2f}\n", style="white")
                        rev_text.append(f"PnL: {workspace.get('pnl', 0.0):.2f}\n", style="white")
                        rev_text.append(f"PnL and Cost: {workspace.get('pnl_and_cost', 0.0):.2f}\n", style="white")
                        rev_text.append(f"R:R: {workspace.get('r_r', 0.0):.2f}\n", style="white")
                        rev_text.append(f"R Multiple: {workspace.get('r_multiple', 0.0):.2f}\n", style="white")
                        rev_text.append(f"Captured MFE: {workspace.get('captured_mfe', 0.0):.2f}\n", style="white")
                        rev_text.append(f"Captured MAE: {workspace.get('captured_mae', 0.0):.2f}\n", style="white")
                        rev_text.append(f"Cost: {workspace.get('cost', 0.0):.4f}\n", style="white")
                        
                        # --- Execution Framework Context ---
                        rev_text.append("\n--- Execution Framework Context ---\n", style="bold magenta")
                        rev_text.append(f"Tier Setup: {workspace.get('tier_setup')}\n", style="white")
                        rev_text.append(f"Setup Type: {workspace.get('setup_type')}\n", style="white")
                        rev_text.append(f"Market State: {workspace.get('market_state')}\n", style="white")
                        rev_text.append(f"HTF Trend Context: {workspace.get('htf_trend_context')}\n", style="white")
                        rev_text.append(f"LTF Trend Context: {workspace.get('ltf_trend_context')}\n", style="white")
                        rev_text.append(f"Confirmation Status: {workspace.get('confirmation_status')}\n", style="white")
                        rev_text.append(f"Followed Plan: {workspace.get('followed_plan')}\n", style="white")
                        
                        cp_params_list = workspace.get("confirmation_params") or []
                        conf_str = ", ".join([str(p.value if hasattr(p, 'value') else p) for p in cp_params_list]) if isinstance(cp_params_list, list) else str(cp_params_list)
                        rev_text.append(f"  Conf. Params:         {format_indented_block(conf_str, indent_spaces=24, first_line_flush=True)}\n", style="white")
                        
                        # --- Psychological & Cognitive Logging ---
                        rev_text.append("\n--- Psychological & Cognitive Logging ---\n", style="bold blue")
                        rev_text.append(f"Primary Emotion: {workspace.get('primary_emotion')}\n", style="white")
                        
                        emotions_list = workspace.get("emotions") or []
                        emotions_str = ", ".join([str(e.value if hasattr(e, 'value') else e) for e in emotions_list]) if isinstance(emotions_list, list) else str(emotions_list)
                        rev_text.append(f"  Emotions:             {format_indented_block(emotions_str, indent_spaces=24, first_line_flush=True)}\n", style="white")
                        
                        be_list = workspace.get("behavioral_errors") or []
                        be_str = ", ".join([str(b.value if hasattr(b, 'value') else b) for b in be_list]) if isinstance(be_list, list) else str(be_list)
                        wrapped_be = format_indented_block(be_str, indent_spaces=24, first_line_flush=True)
                        rev_text.append(f"  Behavioral Errors:    {wrapped_be}\n", style="white")
                        
                        cp_list = workspace.get("cognitive_patterns") or []
                        cp_str = ", ".join([str(c.value if hasattr(c, 'value') else c) for c in cp_list]) if isinstance(cp_list, list) else str(cp_list)
                        wrapped_cp = format_indented_block(cp_str, indent_spaces=24, first_line_flush=True)
                        rev_text.append(f"  Cognitive Patterns:   {wrapped_cp}\n", style="white")
                        
                        pre_e = workspace.get("pre_trade_emotions")
                        mid_e = workspace.get("mid_trade_emotions")
                        post_e = workspace.get("post_trade_emotions")
                        if pre_e:
                            rev_text.append(f"  Pre-Trade Emotions:   {format_indented_block(pre_e, indent_spaces=24, first_line_flush=True)}\n", style="white")
                        if mid_e:
                            rev_text.append(f"  Mid-Trade Emotions:   {format_indented_block(mid_e, indent_spaces=24, first_line_flush=True)}\n", style="white")
                        if post_e:
                            rev_text.append(f"  Post-Trade Emotions:  {format_indented_block(post_e, indent_spaces=24, first_line_flush=True)}\n", style="white")
                        
                        # --- Internal State Thresholds ---
                        rev_text.append("\n--- Internal State Thresholds ---\n", style="bold orange1")
                        rev_text.append(f"Anxiety Level: {workspace.get('anxiety_level')}\n", style="white")
                        rev_text.append(f"Impatience Level: {workspace.get('impatience_level')}\n", style="white")
                        rev_text.append(f"Mental Clarity Level: {workspace.get('mental_clarity_level')}\n", style="white")
                        
                        # --- Qualitative Notes ---
                        rev_text.append("\n--- Qualitative Notes ---\n", style="bold cyan")
                        if workspace["lesson_tact"] and workspace["lesson_tact"] != "nan":
                            indented_lesson = format_indented_block(workspace["lesson_tact"], indent_spaces=11, wrap_width=38)
                            rev_text.append(f"Lesson Learned:\n  {indented_lesson}\n", style="dim italic")
                        else:
                            rev_text.append("Lesson Learned: N/A\n", style="dim italic")      
                        dashboard = Panel(
                            rev_text,
                            title=f"Tactical Audit Repair: {record.id[:8]}",
                            border_style="magenta"
                        )
                        
                        try:
                            console.clear(home=True)
                        except TypeError:
                            console.clear()
                        console.print(dashboard)
                        
                        action = inquirer.select(
                            message="Action >",
                            choices=[
                                Choice("edit", name="[1] Edit a Field"),
                                Choice("save", name="[2] Confirm & Save Repair"),
                                Choice("back", name="[3] Discard")
                            ],
                            pointer=">",
                            qmark=""
                        ).execute()
                        
                        if action == "back":
                            break
                        elif action == "edit":
                            edit_choices = [
                                Choice("trade_status", name=f"Trade Status: {workspace['trade_status']}"),
                                Choice("compliance", name=f"Compliance: {workspace['compliance']}"),
                                Choice("entry_price", name=f"Entry Price: {workspace['entry_price']}"),
                                Choice("closing_price", name=f"Closing Price: {workspace['closing_price']}"),
                                Choice("size", name=f"Size: {workspace['size']}"),
                                Choice("stop_loss", name=f"Stop Loss: {workspace['stop_loss']}"),
                                Choice("take_profit", name=f"Take Profit: {workspace['take_profit']}"),
                                Choice("mae", name=f"MAE: {workspace['mae']}"),
                                Choice("mfe", name=f"MFE: {workspace['mfe']}"),
                                Choice("cost", name=f"Cost: {workspace.get('cost', 0.0)}"),
                                Choice("could_hit_tp", name=f"Could Hit TP: {workspace['could_hit_tp']}"),
                                Choice("entry_time", name=f"Entry Time: {workspace['entry_time']}"),
                                Choice("exit_time", name=f"Exit Time: {workspace['exit_time']}"),
                                Choice("tier_setup", name=f"Tier Setup: {workspace['tier_setup']}"),
                                Choice("market_state", name=f"Market State: {workspace['market_state']}"),
                                Choice("followed_plan", name=f"Followed Plan: {workspace['followed_plan']}"),
                                Choice("primary_emotion", name=f"Primary Emotion: {workspace['primary_emotion']}"),
                                Choice("setup_type", name=f"Setup Type: {workspace['setup_type']}"),
                                Choice("htf_trend_context", name=f"HTF Trend: {workspace['htf_trend_context']}"),
                                Choice("ltf_trend_context", name=f"LTF Trend: {workspace['ltf_trend_context']}"),
                                Choice("confirmation_5m_15m", name=f"5m/15m Confirmation: {workspace['confirmation_5m_15m']}"),
                                Choice("confirmation_status", name=f"Confirmation Status: {workspace['confirmation_status']}"),
                                Choice("confirmation_params", name=f"Confirmation Params: {len(workspace['confirmation_params'])} chosen"),
                                Choice("emotions", name=f"Emotions List: {len(workspace['emotions'])} chosen"),
                                Choice("behav_errors", name=f"Behavioral Errors List: {len(workspace['behavioral_errors'])} chosen"),
                                Choice("cog_patterns", name=f"Cognitive Patterns List: {len(workspace['cognitive_patterns'])} chosen"),
                                Choice("anxiety_level", name=f"Anxiety Level: {workspace['anxiety_level']}"),
                                Choice("impatience_level", name=f"Impatience Level: {workspace['impatience_level']}"),
                                Choice("mental_clarity_level", name=f"Mental Clarity Level: {workspace['mental_clarity_level']}"),
                                Choice("pre_trade_emotions", name=f"Pre Trade Emotions: {workspace['pre_trade_emotions'][:25] if workspace['pre_trade_emotions'] else 'N/A'}..."),
                                Choice("mid_trade_emotions", name=f"Mid Trade Emotions: {workspace['mid_trade_emotions'][:25] if workspace['mid_trade_emotions'] else 'N/A'}..."),
                                Choice("post_trade_emotions", name=f"Post Trade Emotions: {workspace['post_trade_emotions'][:25] if workspace['post_trade_emotions'] else 'N/A'}..."),
                                Choice("lesson_tact", name=f"Lesson Learned: {workspace['lesson_tact'][:25]}..."),
                                Choice("visual_lesson_path", name=f"Visual Lesson: {workspace.get('visual_lesson_path', 'nan')}"),
                                Choice("back", name="[<] Back")
                            ]
                            
                            field = inquirer.select(
                                message="Select Field to Edit >",
                                choices=edit_choices,
                                pointer=">",
                                qmark=""
                            ).execute()
                            
                            if field == "back":
                                continue
                                
                            if field == "trade_status":
                                workspace["trade_status"] = get_enum_choice("Edit Trade Status", TradeStatus).value
                            elif field == "compliance":
                                workspace["compliance"] = get_enum_choice("Edit Compliance State", ComplianceState).value
                            elif field in ["take_profit", "entry_price", "closing_price", "stop_loss", "size"]:
                                workspace[field] = get_mandatory_float(f"Edit {field.replace('_', ' ').title()}")
                                recalculate_tactical_math(workspace, workspace["p0_dir"], workspace["p2_dir"], workspace["p4_dir"])
                            elif field in ["mae", "mfe"]:
                                workspace[field] = get_mandatory_float(f"Edit {field.upper()} (0 <= val <= 10)", min_val=0, max_val=10)
                                recalculate_tactical_math(workspace, workspace["p0_dir"], workspace["p2_dir"], workspace["p4_dir"])
                            elif field == "cost":
                                workspace[field] = get_mandatory_float("Edit Cost")
                                recalculate_tactical_math(workspace, workspace["p0_dir"], workspace["p2_dir"], workspace["p4_dir"])
                            elif field == "could_hit_tp":
                                workspace["could_hit_tp"] = inquirer.select(
                                    message="Could hit TP? >",
                                    choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                    pointer=">",
                                    qmark="",
                                    keybindings={"skip": []}
                                 ).execute()
                            elif field == "entry_time":
                                workspace["entry_time"] = get_mandatory_datetime("Edit Entry Time")
                            elif field == "exit_time":
                                workspace["exit_time"] = get_mandatory_datetime("Edit Exit Time")
                            elif field == "tier_setup":
                                workspace["tier_setup"] = get_enum_choice("Edit Tier Setup", TierSetup).value
                            elif field == "market_state":
                                workspace["market_state"] = get_enum_choice("Edit Market State", MarketState).value
                            elif field == "followed_plan":
                                workspace["followed_plan"] = get_enum_choice("Edit Followed Plan", FollowedPlan).value
                            elif field == "primary_emotion":
                                workspace["primary_emotion"] = get_enum_choice("Edit Primary Emotion", PrimaryEmotion).value
                            elif field == "setup_type":
                                workspace["setup_type"] = get_enum_choice("Edit Setup Type", SetupType).value
                            elif field == "htf_trend_context":
                                workspace["htf_trend_context"] = get_enum_choice("Edit HTF Trend Context", HTFTrendContext).value
                            elif field == "ltf_trend_context":
                                workspace["ltf_trend_context"] = get_enum_choice("Edit LTF Trend Context", TrendContext).value
                            elif field == "confirmation_5m_15m":
                                workspace["confirmation_5m_15m"] = inquirer.select(
                                    message="New 5m_15m_confirmation >",
                                    choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                    pointer=">",
                                    qmark=""
                                ).execute()
                            elif field == "confirmation_status":
                                workspace["confirmation_status"] = get_enum_choice("Edit Confirmation Status", ConfirmationStatus).value
                            elif field == "confirmation_5m_15m":
                                workspace["confirmation_5m_15m"] = inquirer.select(
                                    message="Edit 5m/15m Confirmation >",
                                    choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                    pointer=">",
                                    qmark=""
                                ).execute()
                                recalculate_tactical_math(workspace, workspace["p0_dir"], workspace["p2_dir"], workspace["p4_dir"])
                            elif field == "confirmation_params":
                                workspace["confirmation_params"] = [p.value if hasattr(p, 'value') else p for p in get_multi_enum_choice("Edit Confirmation Params", ConfirmationParams)]
                            elif field == "emotions":
                                workspace["emotions"] = [e.value if hasattr(e, 'value') else e for e in get_multi_enum_choice("Edit Emotions", Emotions)]
                            elif field == "behav_errors":
                                workspace["behavioral_errors"] = [b.value if hasattr(b, 'value') else b for b in get_multi_enum_choice("Edit Behavioral Errors", BehavioralErrors)]
                            elif field == "cog_patterns":
                                workspace["cognitive_patterns"] = [c.value if hasattr(c, 'value') else c for c in get_multi_enum_choice("Edit Cognitive Patterns", CognitivePatterns)]
                            elif field == "lesson_tact":
                                workspace["lesson_tact"] = get_mandatory_text("Edit Tactical Lesson Learned", multiline=True)
                            elif field == "visual_lesson_path":
                                workspace["visual_lesson_path"] = handle_visual_lesson_assignment(selected_id, workspace.get("asset", "Unknown"), workspace.get("visual_lesson_path", "nan"))
                            elif field in ["anxiety_level", "impatience_level", "mental_clarity_level"]:
                                workspace[field] = get_mandatory_int(f"Enter {field.replace('_', ' ').title()} (1 to 5)", 1, 5)
                            elif field == "pre_trade_emotions":
                                workspace["pre_trade_emotions"] = get_mandatory_text("Edit Pre Trade Emotions")
                            elif field == "mid_trade_emotions":
                                workspace["mid_trade_emotions"] = get_mandatory_text("Edit Mid Trade Emotions")
                            elif field == "post_trade_emotions":
                                workspace["post_trade_emotions"] = get_mandatory_text("Edit Post Trade Emotions")
                                
                        elif action == "save":
                            try:
                                raw_conn = db_session.connection().connection
                                exists = raw_conn.execute("SELECT 1 FROM tactical_audit WHERE id = ?", (record.id,)).fetchone()
                                
                                raw_conn.execute("UPDATE unified_department SET trade_status = ? WHERE id = ?", (workspace["trade_status"], record.id))
                                
                                if exists:
                                    raw_conn.execute("""
                                        UPDATE tactical_audit SET 
                                            compliance = ?,
                                            confirmation_5m_15m = ?,
                                            entry_price = ?,
                                            closing_price = ?,
                                            size = ?,
                                            stop_loss = ?,
                                            take_profit = ?,
                                            mae_adverse = ?,
                                            mfe_favorable = ?,
                                            could_hit_tp = ?,
                                            lesson_learned = ?,
                                            tier_setup = ?,
                                            market_state = ?,
                                            followed_plan = ?,
                                            primary_emotion = ?,
                                            setup_type = ?,
                                            htf_trend_context = ?,
                                            ltf_trend_context = ?,
                                            confirmation_status = ?,
                                            anxiety_level = ?,
                                            impatience_level = ?,
                                            mental_clarity_level = ?,
                                            risk_usd = ?,
                                            r_r = ?,
                                            pnl_and_cost = ?,
                                            notional_size = ?,
                                            capital_at_risk = ?,
                                            trade_decision = ?,
                                            emotions = ?,
                                            behavioral_errors = ?,
                                            cognitive_patterns = ?,
                                            visual_lesson_path = ?,
                                            pre_trade_emotions = ?,
                                            mid_trade_emotions = ?,
                                            post_trade_emotions = ?,
                                            confirmation_params = ?,
                                            entry_time = ?,
                                            exit_time = ?
                                        WHERE id = ?
                                    """, (
                                        workspace["compliance"] or "nan",
                                        workspace["confirmation_5m_15m"] or "no",
                                        float(workspace["entry_price"]),
                                        float(workspace["closing_price"]),
                                        float(workspace["size"]),
                                        float(workspace["stop_loss"]),
                                        float(workspace["take_profit"]),
                                        float(workspace["mae"]),
                                        float(workspace["mfe"]),
                                        workspace["could_hit_tp"],
                                        workspace["lesson_tact"],
                                        workspace["tier_setup"],
                                        workspace["market_state"],
                                        workspace["followed_plan"],
                                        workspace["primary_emotion"],
                                        workspace["setup_type"],
                                        workspace["htf_trend_context"],
                                        workspace["ltf_trend_context"],
                                        workspace["confirmation_status"],
                                        int(workspace["anxiety_level"]),
                                        int(workspace["impatience_level"]),
                                        int(workspace["mental_clarity_level"]),
                                        float(workspace["risk_usd"]),
                                        float(workspace["r_r"]),
                                        float(workspace["pnl_and_cost"]),
                                        float(workspace["notional_size"]),
                                        float(workspace["capital_at_risk"]),
                                        workspace["trade_decision"],
                                        json.dumps(workspace["emotions"]) if workspace["emotions"] else None,
                                        json.dumps(workspace["behavioral_errors"]) if workspace["behavioral_errors"] else None,
                                        json.dumps(workspace["cognitive_patterns"]) if workspace["cognitive_patterns"] else None,
                                        workspace["visual_lesson_path"] if workspace["visual_lesson_path"] != "nan" else None,
                                        workspace.get("pre_trade_emotions"),
                                        workspace.get("mid_trade_emotions"),
                                        workspace.get("post_trade_emotions"),
                                        json.dumps(workspace["confirmation_params"]) if workspace.get("confirmation_params") else None,
                                        workspace["entry_time"].isoformat() if hasattr(workspace.get("entry_time"), "isoformat") else workspace.get("entry_time"),
                                        workspace["exit_time"].isoformat() if hasattr(workspace.get("exit_time"), "isoformat") else workspace.get("exit_time"),
                                        record.id
                                    ))
                                else:
                                    raw_conn.execute("""
                                        INSERT INTO tactical_audit (
                                            id, compliance, confirmation_5m_15m, entry_price, closing_price, size, stop_loss, take_profit, mae_adverse, mfe_favorable, could_hit_tp, lesson_learned,
                                            tier_setup, market_state, followed_plan, primary_emotion, setup_type, htf_trend_context, ltf_trend_context, confirmation_status, anxiety_level, impatience_level, mental_clarity_level,
                                            risk_usd, r_r, pnl_and_cost, notional_size, capital_at_risk, trade_decision, emotions, behavioral_errors, cognitive_patterns, visual_lesson_path,
                                            pre_trade_emotions, mid_trade_emotions, post_trade_emotions, confirmation_params, entry_time, exit_time,
                                            g1_trend_15m, g2_fractal_trend, g3_limit_order, g4_breathing, g5_manual_cooldown, g6_sl_validated, g7_tp_validated,
                                            c1_kl_support, c2_fractal_std, c3_fractal_1m, c4_fractal_1h, c5_kl_target, c6_liquidity, c7_retracement, c8_convergence_15m,
                                            gates_failed, confirmations_count, mfe_potencial_estimado
                                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                                  0, 0, 0, 0, 0, 0, 0,
                                                  0, 0, 0, 0, 0, 0, 0, 0,
                                                  NULL, NULL, NULL)
                                    """, (
                                        record.id,
                                        workspace["compliance"] or "nan",
                                        workspace["confirmation_5m_15m"] or "no",
                                        float(workspace["entry_price"]),
                                        float(workspace["closing_price"]),
                                        float(workspace["size"]),
                                        float(workspace["stop_loss"]),
                                        float(workspace["take_profit"]),
                                        float(workspace["mae"]),
                                        float(workspace["mfe"]),
                                        workspace["could_hit_tp"],
                                        workspace["lesson_tact"],
                                        workspace["tier_setup"],
                                        workspace["market_state"],
                                        workspace["followed_plan"],
                                        workspace["primary_emotion"],
                                        workspace["setup_type"],
                                        workspace["htf_trend_context"],
                                        workspace["ltf_trend_context"],
                                        workspace["confirmation_status"],
                                        int(workspace["anxiety_level"]),
                                        int(workspace["impatience_level"]),
                                        int(workspace["mental_clarity_level"]),
                                        float(workspace["risk_usd"]),
                                        float(workspace["r_r"]),
                                        float(workspace["pnl_and_cost"]),
                                        float(workspace["notional_size"]),
                                        float(workspace["capital_at_risk"]),
                                        workspace["trade_decision"],
                                        json.dumps(workspace["emotions"]) if workspace["emotions"] else None,
                                        json.dumps(workspace["behavioral_errors"]) if workspace["behavioral_errors"] else None,
                                        json.dumps(workspace["cognitive_patterns"]) if workspace["cognitive_patterns"] else None,
                                        workspace["visual_lesson_path"] if workspace["visual_lesson_path"] != "nan" else None,
                                        workspace.get("pre_trade_emotions"),
                                        workspace.get("mid_trade_emotions"),
                                        workspace.get("post_trade_emotions"),
                                        json.dumps(workspace["confirmation_params"]) if workspace.get("confirmation_params") else None,
                                        workspace["entry_time"].isoformat() if hasattr(workspace.get("entry_time"), "isoformat") else workspace.get("entry_time"),
                                        workspace["exit_time"].isoformat() if hasattr(workspace.get("exit_time"), "isoformat") else workspace.get("exit_time")
                                    ))
                                db_session.commit()
                                console.print("[green]Tactical Audit Repair saved successfully.[/green]")
                            except Exception as e:
                                db_session.rollback()
                                console.print(f"[bold red]Failed to save Tactical Audit Repair: {e}[/bold red]")
                            input("Press Enter to continue...")
                            break

                elif comp_choice == "datetime":
                    while True:
                        try:
                            console.clear(home=True)
                        except TypeError:
                            console.clear()
                            
                        console.rule(f"[bold cyan]Date/Time Metadata Repair: {record.id[:8]}[/bold cyan]")
                        
                        dt_choices = []
                        dt_choices.append(Choice("u_created_at", name=f"Unified Created At: {to_local_display(record.created_at)}"))
                        dt_choices.append(Choice("u_updated_at", name=f"Unified Updated At: {to_local_display(record.updated_at)}"))
                        
                        if record.efficiency_audit:
                            dt_choices.append(Choice("ea_created_at", name=f"Efficiency Created At: {to_local_display(record.efficiency_audit.created_at)}"))
                            dt_choices.append(Choice("ea_updated_at", name=f"Efficiency Updated At: {to_local_display(record.efficiency_audit.updated_at)}"))
                            dt_choices.append(Choice("ea_res_time", name=f"Efficiency Res Time: {to_local_display(record.efficiency_audit.resolution_time)}"))
                            
                        if record.tactical_audit:
                            dt_choices.append(Choice("ta_entry", name=f"Tactical Entry Time: {to_local_display(record.tactical_audit.entry_time)}"))
                            dt_choices.append(Choice("ta_exit", name=f"Tactical Exit Time: {to_local_display(record.tactical_audit.exit_time)}"))
                            
                        dt_choices.append(Separator())
                        dt_choices.append(Choice("save", name="[SAVE] Confirm & Commit Changes"))
                        dt_choices.append(Choice("back", name="[BACK] Cancel & Return"))
                        
                        dt_choice = inquirer.select(
                            message="Select Date/Time Field to Modify >",
                            choices=dt_choices,
                            pointer=">",
                            qmark=""
                        ).execute()
                        
                        if dt_choice == "back":
                            break
                            
                        elif dt_choice == "save":
                            try:
                                db_session.commit()
                                console.print("[green]Date/Time Metadata saved successfully.[/green]")
                            except Exception as e:
                                db_session.rollback()
                                console.print(f"[bold red]Failed to save Date/Time Metadata: {e}[/bold red]")
                            input("Press Enter to continue...")
                            break
                            
                        else:
                            try:
                                new_dt = get_mandatory_datetime("Enter new UTC-6 naive datetime", allow_cancel=True)
                                if dt_choice == "u_created_at":
                                    record.created_at = new_dt
                                elif dt_choice == "u_updated_at":
                                    record.updated_at = new_dt
                                elif dt_choice == "ea_created_at":
                                    record.efficiency_audit.created_at = new_dt
                                elif dt_choice == "ea_updated_at":
                                    record.efficiency_audit.updated_at = new_dt
                                elif dt_choice == "ea_res_time":
                                    record.efficiency_audit.resolution_time = new_dt
                                elif dt_choice == "ta_entry":
                                    record.tactical_audit.entry_time = new_dt
                                elif dt_choice == "ta_exit":
                                    record.tactical_audit.exit_time = new_dt
                            except GoBackException:
                                pass

if __name__ == "__main__":
    cli()
