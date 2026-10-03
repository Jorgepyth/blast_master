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
# `python cli/main.py ...` pone `cli/` en sys.path, no la raíz del repo, y los imports de abajo fallan con "No module
# named 'core'". `python -m cli.main` (la función `trading`) sí la pone. Con esto funcionan las dos formas (spec 002, T33b).
import sys
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.math_engine import calculate_probabilities, calculate_algebraic_metrics, calculate_structural_entry, calculate_edge_score

getcontext().prec = 18

try:
    signal.signal(signal.SIGTSTP, signal.SIG_IGN)
except Exception:
    pass

from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, IntPrompt
from rich.layout import Layout
from rich.columns import Columns
from rich.align import Align
from rich.text import Text
from rich import box
from rich.live import Live

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator
from InquirerPy.utils import get_style

from cli.schemas.efficiency import EfficiencyAnalysis, Direction, Strength
from cli.schemas.audit_efficiency import EfficiencyAudit, StructuralBias, ResolutionType, StructuralResolution, FailureReason, RESOLUTION_TIME_SOURCES, RESOLUTION_TIME_SOURCE_CANDLES, RESOLUTION_TIME_SOURCE_CORRECTED
from cli.schemas.tactical import TacticalAnalysis, Hierarchy, Timeframe, FractalType, TacticalClassification
from cli.schemas.audit_tactical import TacticalAudit, TierSetup, MarketState, Session, ExitType, TradeDecision, FollowedPlan, PrimaryEmotion, SetupType, HTFTrendContext, TrendContext, ConfirmationStatus, ConfirmationParams, Emotions, ACTIVE_EMOTIONS, BehavioralErrors, SkipReason, StopDeviationReason, STOP_DEVIATION_REASON_LABELS
from tools.database import (
    init_db, update_record_state, get_records_by_state,
    LifecycleState, add_asset, get_assets, to_local_display,
    get_unified_created_at, get_unified_structural_invalidation
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
    console,
    STEP1_KEYS,
    STEP2_KEYS
)

CACHE_FILE = ".data/paused_audits.json"

# Shared InquirerPy style, mapped 1:1 to the rich `blast_theme` semantic colors
# (cli/ui_manager.py) so prompts no longer fall back to InquirerPy's default
# "one dark" palette (blue/gold) once a rich panel above them is already themed
# cyan/magenta/green/yellow/red. ANSI color names (not fixed hex) are used so both
# libraries resolve against the same terminal palette. get_style() with the default
# style_override=True blanks any key left out, so every supported key is declared
# explicitly here.
INQUIRER_STYLE = get_style({
    "questionmark": "ansicyan bold",
    "answermark": "ansicyan bold",
    "answer": "ansicyan bold",
    "input": "ansiwhite",
    "question": "",
    "answered_question": "",
    "instruction": "ansiwhite",
    "long_instruction": "ansiwhite",
    "pointer": "ansicyan bold",
    "checkbox": "ansigreen bold",
    "separator": "ansimagenta bold",
    "skipped": "ansiwhite",
    "validator": "ansired bold",
    "marker": "ansiyellow bold",
    "fuzzy_prompt": "ansimagenta bold",
    "fuzzy_info": "ansiwhite",
    "fuzzy_border": "ansiwhite",
    "fuzzy_match": "ansimagenta bold",
    "spinner_pattern": "ansiyellow bold",
    "spinner_text": "",
}, style_override=True)

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
            elif func.__name__ == 'ask_entry_time' and isinstance(val, str):
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

# Todo campo que core/math_engine.calculate_algebraic_metrics multiplique por
# contract_size va aquí. Cuando el símbolo no está VERIFICADO (contract_size None),
# el repair flow los deja en None en vez de persistir el valor sin multiplicador.
# Mantener sincronizado con calculate_algebraic_metrics — evita que un campo nuevo
# quede desprotegido silenciosamente, como pasó con notional_size_usd.
MONEY_FIELDS_REQUIRING_CONTRACT_SIZE = (
    "risk_usd", "capital_at_risk", "notional_size", "notional_size_usd",
)

# Gate emocional (flow_pending_audits, "camino principal") -- ver ARCHITECTURE.md
# §15. Independiente del gate de Tier D/F (§14): éste sí admite override, con
# justificación obligatoria persistida en TacticalAudit.emotional_gate_override_reason.
# No fusionar ambos gates sin revisar esa decisión.
ANXIETY_GATE_THRESHOLD = 4

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

def _now_gt() -> datetime.datetime:
    """La hora de ahora en GT naive (UTC−6, sin tzinfo), igual que `created_at` (tools/database.py). Las horas del
    análisis de la spec 002 (RF-13 a RF-13e) salen de acá; los tests la reemplazan por un reloj falso."""
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-6))).replace(tzinfo=None)


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
        elif ch.lower() in ['1', '2', '3', '4', '5', '6', 't', 'e', 'x']:
            return ch.lower()
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return None

def get_enum_choice(prompt_text, enum_class, exclude=None, default=None):
    """`default` (spec 002, RF-7; plan T11): un valor propuesto, como miembro del enum o como su texto. Queda
    preseleccionado y el mensaje lo marca `(auto: ...)`; si no está entre las opciones, se ignora. Sin default, el prompt
    es el de siempre."""
    if exclude is None:
        exclude = []
    choices = [e for e in enum_class if e.name != "SKIP" and e not in exclude]
    inq_choices = [Choice(e, name=f"[{i+1}] {e.value}") for i, e in enumerate(choices)]
    proposed = next((e for e in choices if default is not None and (e == default or e.value == default)), None)
    extra = {}
    message = f"{prompt_text} >"
    if proposed is not None:
        extra["default"] = proposed
        message = f"{prompt_text} (auto: {proposed.value}) >"

    result = bind_pause(inquirer.select(
        message=message,
        choices=inq_choices,
        pointer=">",
        qmark="",
        keybindings={"skip": []},
        style=INQUIRER_STYLE,
        **extra
    )).execute()
    return result

def get_multi_enum_choice(prompt_text, enum_class, choices=None, preselected=None):
    source = choices if choices is not None else [e for e in enum_class if e.name != "SKIP"]
    preselected_vals = set()
    if preselected:
        for p in preselected:
            preselected_vals.add(p.value if hasattr(p, "value") else p)
    inq_choices = [Choice(e, name=f"[{i+1}] {e.value}", enabled=(e.value in preselected_vals)) for i, e in enumerate(source)]

    while True:
        result = bind_pause(inquirer.checkbox(
            message=f"{prompt_text} (Select at least one) >",
            choices=inq_choices,
            pointer=">",
            qmark="",
            keybindings={"skip": []},
            style=INQUIRER_STYLE
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
            keybindings={"skip": []},
            style=INQUIRER_STYLE
        )).execute()
        if val and val.strip():
            return val.strip()

# Intentionally separate from ui_manager.format_indented_block: different defaults
# (wrap_width=80 here vs None there) with real call-sites in each file depending on
# their own default.
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

def compute_detected_patterns(psych):
    primary_emotion = psych.get("primary_emotion")
    anxiety = psych.get("anxiety_level")
    impatience = psych.get("impatience_level")
    clarity = psych.get("mental_clarity_level")
    behav_errors = psych.get("behavioral_errors") or []
    conf_status = str(psych.get("confirmation_status") or "")
    gates_failed = psych.get("gates_failed")
    followed_plan = psych.get("followed_plan")

    tags = []
    if conf_status.startswith("S7") or (gates_failed is not None and gates_failed >= 1 and followed_plan == "No"):
        tags.append(("Overconfidence / Rule Override", "bold red"))
    if conf_status.startswith("S6") and primary_emotion in ("Fear of being wrong", "Self-doubt", "Anxiety"):
        tags.append(("Loss Aversion (Missed Opportunity)", "bold yellow"))
    if impatience is not None and impatience >= 4 and "Overtrading" in behav_errors:
        tags.append(("Impulsivity Loop", "bold red"))
    if clarity is not None and clarity <= 2 and "bad entry" in conf_status.lower():
        tags.append(("Impaired Judgment Execution", "bold yellow"))
    if anxiety is not None and anxiety >= 4 and "Lack of Discipline" in behav_errors:
        tags.append(("Anxiety-Driven Discipline Breakdown", "bold red"))
    if primary_emotion == "Equanimity" and clarity is not None and clarity >= 4:
        tags.append(("Flow State", "bold green"))

    if not tags:
        if anxiety is None and impatience is None and clarity is None:
            tags.append(("Insufficient data (legacy trade)", "dim"))
        else:
            tags.append(("No pattern detected", "dim white"))
    return tags

def auto_fetch_tradingview_screenshot(asset: str) -> Optional[str]:
    """
    Escanea la carpeta Downloads, filtra por el ticker del activo,
    selecciona el archivo más reciente, lo mueve a staging y retorna el nombre.
    """
    downloads_dir_env = os.getenv("TRADINGVIEW_DOWNLOADS_DIR")
    if not downloads_dir_env:
        raise RuntimeError(
            "TRADINGVIEW_DOWNLOADS_DIR no está configurada. Define esta variable en tu .env "
            "con la ruta local de tu carpeta de Downloads (ver .env.template) para usar el "
            "auto-fetch de screenshots de TradingView."
        )
    downloads_dir = Path(downloads_dir_env)
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
            keybindings={"skip": []},
            style=INQUIRER_STYLE
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

def get_dir_val(d):
    """Mapea Direction (o un string equivalente en contenido, ya que Direction
    es str+Enum) a su valor firmado. Compartida por los 3 sitios que computan
    i_cd para evitar divergencia silenciosa entre ellos."""
    return 1 if d == Direction.LONG else -1 if d == Direction.SHORT else 0

def get_str_val(s):
    """Mapea Strength (o un string equivalente en contenido) a su peso.
    Compartida por los 3 sitios que computan i_cd."""
    return 2 if s == Strength.STRONG else 1 if s == Strength.MID else 0

def get_optional_text(prompt_text, multiline=False):
    message = f"{prompt_text} (Optional, press Enter to skip) >"
    if multiline:
        message += " (Presiona Esc + Enter para guardar)"
    val = bind_pause(inquirer.text(message=message, multiline=multiline, keybindings={"skip": []}, style=INQUIRER_STYLE)).execute()
    return val.strip() if val else None

def ask_stop_deviation_reason():
    """Selección obligatoria (sin skip) de StopDeviationReason. InquirerPy no envuelve
    líneas largas -- en una terminal angosta, el texto completo de cada razón
    (STOP_DEVIATION_REASON_LABELS) se corta antes de terminar la frase. Por eso se
    imprime primero un panel Rich (que sí envuelve) con las 6 razones completas, y la
    lista de selección solo muestra el título corto de cada una (la parte antes de
    " -- " en el label). No usa get_enum_choice() porque el valor persistido (slug
    corto) y el texto mostrado al operador son distintos -- a diferencia de
    FailureReason, donde el texto largo vive directo en el .value."""
    ref_text = Text()
    for i, r in enumerate(StopDeviationReason):
        title, _, detail = STOP_DEVIATION_REASON_LABELS[r].partition(" -- ")
        ref_text.append(f"[{i+1}] {title}\n", style="bold yellow")
        ref_text.append(f"    {detail}\n\n", style="white")
    console.print(Panel(ref_text, title="Stop Deviation Reason — opciones", border_style="yellow"))

    inq_choices = [
        Choice(r, name=f"[{i+1}] {STOP_DEVIATION_REASON_LABELS[r].partition(' -- ')[0]}")
        for i, r in enumerate(StopDeviationReason)
    ]
    return bind_pause(inquirer.select(
        message="Stop Deviation Reason (obligatorio) >",
        choices=inq_choices,
        pointer=">",
        qmark="",
        keybindings={"skip": []},
        style=INQUIRER_STYLE
    )).execute()

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
        keybindings={"skip": []},
        style=INQUIRER_STYLE
    )).execute()
    return int(val)

def _plain_number(value: float) -> str:
    """Un número sin notación científica ni ceros de más: 4369.62, 0.00005, 2650."""
    text = format(value, "f").rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def get_mandatory_float(prompt_text, min_val=None, max_val=None, default=None):
    """`default` (spec 002, RF-7): un valor propuesto, ya escrito en el campo (Enter lo acepta) y marcado `(auto: ...)`
    en el mensaje. Sin default, el prompt es el de siempre."""
    def validate_float(result):
        if not result: return False
        is_float = result.replace('.', '', 1).isdigit() or (result.startswith('-') and result[1:].replace('.', '', 1).isdigit())
        if not is_float: return False
        v = float(result)
        if min_val is not None and v < min_val: return False
        if max_val is not None and v > max_val: return False
        return True

    extra = {}
    message = f"{prompt_text} >"
    if default is not None:
        extra["default"] = _plain_number(float(default))
        message = f"{prompt_text} (auto: {extra['default']}) >"

    val = bind_pause(inquirer.text(
        message=message,
        validate=validate_float,
        invalid_message="Must be a valid float",
        keybindings={"skip": []},
        style=INQUIRER_STYLE,
        **extra
    )).execute()
    return float(val)


def get_optional_datetime(prompt_text, default=None, precision=None):
    """
    Spec 002 (RF-7c, RF-7g; plan T11): una fecha y hora opcional. Con `default` (la hora propuesta por las velas), el
    campo ya viene escrito y el mensaje dice `(auto: YYYY-MM-DD HH:MM, <precision>)`; Enter la acepta, otra hora la
    corrige y dejarlo vacío devuelve `None`. Sin default, el mensaje dice `[Optional]`.
    """
    def validate_datetime(result):
        if not result or not result.strip():
            return True
        try:
            datetime.datetime.strptime(result.strip(), "%Y-%m-%d %H:%M")
            return True
        except ValueError:
            return False

    extra = {}
    message = f"{prompt_text} (YYYY-MM-DD HH:MM) [Optional] >"
    if default is not None:
        extra["default"] = default.strftime("%Y-%m-%d %H:%M")
        note = f", {precision}" if precision else ""
        message = f"{prompt_text} (YYYY-MM-DD HH:MM) (auto: {extra['default']}{note}) >"

    val = bind_pause(inquirer.text(
        message=message,
        validate=validate_datetime,
        invalid_message="Must be in format YYYY-MM-DD HH:MM, or empty",
        keybindings={"skip": []},
        style=INQUIRER_STYLE,
        **extra
    )).execute()
    if not val or not val.strip():
        return None
    return datetime.datetime.strptime(val.strip(), "%Y-%m-%d %H:%M")


def render_pnl_box(asset, entry_price, size_lots, stop_loss, take_profit):
    """Cuadro EFÍMERO de P&L potencial (no persiste nada) que se muestra en el wizard
    tras capturar SL/Entry/Size/TP y antes de Entry Time.

    Delega el cálculo en tools/pnl_calculator (contract_size por instrumento). Si el
    símbolo no está VERIFICADO / es desconocido, muestra "N/A (símbolo no verificado)"
    y su motivo -- nunca bloquea el avance del wizard.

    Devuelve la elección del usuario: "accept" | "sl" | "entry_p" | "size" | "tp".
    """
    from tools import pnl_calculator as _pnl
    try:
        q = _pnl.quantify(asset, entry_price, size_lots, stop_loss, take_profit)
        err = None
    except (_pnl.UnverifiedSymbolError, _pnl.UnknownSymbolError) as exc:
        q, err = None, str(exc)

    try:
        console.clear(home=True)
    except TypeError:
        console.clear()

    # Estilo replicado de "Pre-Flight Validation" de cli-trading-binance
    # (cli/main.py:95-9x de ese repo): box.HEAVY, Metric cian / Value gris,
    # add_section() entre grupos, markup semántico por celda, sin Panel envolvente.
    table = Table(title="Potential P&L (pre-save · not persisted)", box=box.HEAVY)
    table.add_column("Metric", style="#00aaff")
    table.add_column("Value", style="#aaaaaa")

    # --- Order Specs ---
    table.add_row("Symbol", str(q["symbol"]) if q else str(asset))
    table.add_row("Size (lots)", str(size_lots))
    table.add_row("Contract Size", str(q["contract_size"]) if q else "—")

    table.add_section()

    # --- Risk Metrics ---
    if q is None:
        table.add_row(
            "Potential P&L",
            "[#ffffff on #ff0000]N/A (símbolo no verificado)[/#ffffff on #ff0000]",
            style="bold #ffffff on #ff0000",
        )
        table.add_row("Reason", err, style="dim")
    else:
        table.add_row("Potential Loss (→ SL)", f"[#ff0055]-${q['loss_usd']:.2f}[/#ff0055]")
        table.add_row("Potential Gain (→ TP)", f"[#00ff00]+${q['gain_usd']:.2f}[/#00ff00]")
        if q["rr"] is not None:
            table.add_row("R:R", f"{q['rr']:.2f}")

    console.print(table)

    return inquirer.select(
        message="P&L >",
        choices=[
            Choice("accept", name="[1] Aceptar y continuar"),
            Choice("sl", name="[2] Modificar Stop Loss"),
            Choice("entry_p", name="[3] Modificar Entry Price"),
            Choice("size", name="[4] Modificar Size"),
            Choice("tp", name="[5] Modificar Take Profit"),
        ],
        pointer=">",
        qmark="",
    ).execute()


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
        keybindings={"skip": []},
        style=INQUIRER_STYLE
    )).execute()

    if allow_cancel and val.lower() == 'c':
        raise GoBackException("Cancelled by user")
    dt = datetime.datetime.strptime(val, "%Y-%m-%d %H:%M")
    return dt

def get_mandatory_time(prompt_text, allow_cancel=False):
    """Como get_mandatory_datetime pero solo pide HH:MM — usada donde la fecha
    se deriva de otro lado (ej. Entry Time del Tactical Audit, que toma
    año/mes/día de UnifiedDepartment.created_at y solo pide la hora al
    usuario). Retorna un datetime.time."""
    def validate_time(result):
        if not result: return False
        if allow_cancel and result.lower() == 'c': return True
        try:
            datetime.datetime.strptime(result, "%H:%M")
            return True
        except ValueError:
            return False

    msg = f"{prompt_text} (HH:MM)"
    if allow_cancel:
        msg += " (or 'c' to cancel)"
    msg += " >"

    val = bind_pause(inquirer.text(
        message=msg,
        validate=validate_time,
        invalid_message="Must be in format HH:MM or 'c'",
        keybindings={"skip": []},
        style=INQUIRER_STYLE
    )).execute()

    if allow_cancel and val.lower() == 'c':
        raise GoBackException("Cancelled by user")
    return datetime.datetime.strptime(val, "%H:%M").time()

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
                        elif selected_choice == "7":
                            flow_add_tactical_execution()
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
                        elif selected_choice == "6":
                            scope_choice = inquirer.select(
                                message="Generate Reports >",
                                choices=[
                                    Choice("both", name="[1] Both (HTML + LLM Markdown)"),
                                    Choice("html", name="[2] HTML only (human)"),
                                    Choice("llm", name="[3] Markdown+JSON only (LLM)"),
                                    Choice("back", name="[4] Back to Main Menu")
                                ],
                                pointer=">",
                                qmark=""
                            ).execute()
                            if scope_choice != "back":
                                try:
                                    generate_reports(
                                        html_only=(scope_choice == "html"),
                                        llm_only=(scope_choice == "llm"),
                                    )
                                except Exception as e:
                                    console.print(f"[bold red]Error generating reports: {e}[/bold red]")
                                input("Press Enter to continue...")
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

def generate_reports(output_dir="jupyter", html_only=False, llm_only=False):
    """Genera los reportes de analisis (HTML humano + Markdown para LLM) sin abrir Jupyter.

    Reusa las mismas funciones de core/ que jupyter/data_analysis.ipynb; la
    carga/preparacion de datos vive en tools/report_data.py (compartida,
    no duplicada) — ver ese modulo para el detalle de que replica que celdas
    del notebook. Llamada tanto por el comando de terminal `report` como por
    la opcion "Generate Reports" del menu interactivo (start).
    """
    if html_only and llm_only:
        console.print("[bold red]--html-only y --llm-only son mutuamente excluyentes.[/bold red]")
        return

    import matplotlib
    matplotlib.use("Agg")

    from core.analytics_engine import compute_trade_kpis_extended
    from core.backtest_engine import (
        baseline_legacy_metrics, run_department_confluence_backtest,
        run_icd_backtest, sweep_icd_thresholds,
    )
    from core.edge_analysis import analisis_edge
    from core.llm_report_export import (
        MarkdownSection, build_trade_records, render_llm_markdown_report, save_markdown_report,
    )
    from core.plotting_engine import (
        plot_equity_curves, plot_mfe_vs_mae, plot_return_distribution, plot_segment_heatmaps,
    )
    from core.report_export import ReportSection, render_html_report, save_html_report
    from core.report_formatting import dict_to_metrics_frame, style_signed_column
    from tools.report_data import load_report_data

    engine = get_active_engine()
    console.print("[cyan]Cargando datos de la base de datos...[/cyan]")
    data = load_report_data(engine)

    if data.merged.empty:
        console.print("[yellow]No hay analisis cerrados en la base de datos activa. Nada que reportar.[/yellow]")
        return

    kpis, curve, segments = compute_trade_kpis_extended(data.tactical_df)
    edge_metrics = analisis_edge(data.merged)

    baseline = baseline_legacy_metrics(data.merged)
    thresholds = [0.20, 0.25, 0.26, 0.27, 0.28, 0.29, 0.30, 0.31, 0.32, 0.33, 0.34,
                  0.35, 0.36, 0.37, 0.38, 0.39, 0.40]
    sweep = sweep_icd_thresholds(data.merged, thresholds=thresholds)
    confluence_detail, confluence_summary = run_department_confluence_backtest(data.merged)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if not llm_only:
        console.print("[cyan]Generando reporte HTML...[/cyan]")
        fig_equity_usd, fig_drawdown_usd = plot_equity_curves(curve)
        fig_equity_r, fig_drawdown_r = plot_equity_curves(
            curve, equity_col="equity_r", drawdown_col="drawdown_r", label_suffix="(R)"
        )
        fig_dist_pnl = plot_return_distribution(
            data.tactical_df["pnl_and_cost"], "Distribución de PnL (neto) vs Normal teórica", "PnL neto"
        )
        fig_dist_r = plot_return_distribution(
            data.tactical_df["r_multiple"], "Distribución de R-multiples vs Normal teórica", "R multiple"
        )
        fig_mfe_mae = plot_mfe_vs_mae(data.tactical_df)
        fig_heatmap_count, fig_heatmap_avg_r = plot_segment_heatmaps(data.tactical_df) or (None, None)

        html_sections = [
            ReportSection(
                title="Fase 1 — KPIs de ejecución",
                narrative="Motor de KPIs sobre tactical_audit — usa r_multiple (realizado), no r_r (planeado).",
                tables=[dict_to_metrics_frame(kpis)] + [tbl for tbl in segments.values() if not tbl.empty],
                source_functions=[compute_trade_kpis_extended],
            ),
            ReportSection(
                title="Fase 2 — Validación del edge predictivo",
                narrative="Por cada parámetro P0-P4, ¿su score predijo la dirección real del mercado?",
                tables=[style_signed_column(edge_metrics, "Profit Factor", center=1.0)],
                source_functions=[analisis_edge],
            ),
            ReportSection(
                title="Fase 3 — Backtesting",
                narrative=(
                    "Baseline (sistema real, sin threshold) vs. grid-search de threshold sobre el ICD, "
                    "y sistema alternativo de confluencia por departamentos."
                ),
                tables=[
                    dict_to_metrics_frame(baseline),
                    style_signed_column(sweep, "profit_factor_escalado", center=1.0),
                    dict_to_metrics_frame(confluence_summary),
                ],
                source_functions=[
                    run_icd_backtest, sweep_icd_thresholds, baseline_legacy_metrics,
                    run_department_confluence_backtest,
                ],
            ),
            ReportSection(
                title="Fase 5 — Visualización integrada",
                narrative="Equity curve, drawdown, distribución de retornos, MFE vs MAE, heatmaps por segmento.",
                figures=[
                    f for f in [
                        fig_equity_usd, fig_drawdown_usd, fig_equity_r, fig_drawdown_r,
                        fig_dist_pnl, fig_dist_r, fig_mfe_mae, fig_heatmap_count, fig_heatmap_avg_r,
                    ] if f is not None
                ],
                source_functions=[plot_equity_curves, plot_return_distribution, plot_mfe_vs_mae, plot_segment_heatmaps],
            ),
        ]
        html = render_html_report(html_sections, title="Blast Master — Reporte de Análisis (XAUUSD)")
        html_path = save_html_report(html, output_path / "data_analysis_report.html")
        console.print(f"[green]Reporte HTML: {html_path.resolve()}[/green]")

    if not html_only:
        console.print("[cyan]Generando reporte para LLM...[/cyan]")
        llm_context = (
            "Journal de trading XAUUSD (blast_master). r_multiple es el R-multiple REALIZADO "
            "(usar siempre esto, nunca r_r, que es solo el R:R planeado). order_filled indica "
            "si la orden se llenó (binario); specific_bias_compliance mide el SESGO ESTRUCTURAL "
            "— son cosas distintas. Muestra chica: tratar hallazgos como hipotesis, no certezas."
        )
        llm_sections = [
            MarkdownSection(
                title="Fase 1 — KPIs de ejecución",
                narrative="KPIs de ejecucion sobre tactical_audit (r_multiple, no r_r).",
                tables=[dict_to_metrics_frame(kpis)] + [tbl for tbl in segments.values() if not tbl.empty],
                source_functions=[compute_trade_kpis_extended],
            ),
            MarkdownSection(
                title="Fase 2 — Validación del edge predictivo",
                narrative="Profit factor/win rate/correlacion por parametro P0-P4.",
                tables=[edge_metrics],
                source_functions=[analisis_edge],
            ),
            MarkdownSection(
                title="Fase 3 — Backtesting",
                narrative="Baseline vs. grid-search de threshold sobre el ICD y confluencia por departamentos.",
                tables=[dict_to_metrics_frame(baseline), sweep, dict_to_metrics_frame(confluence_summary)],
                source_functions=[
                    run_icd_backtest, sweep_icd_thresholds, baseline_legacy_metrics,
                    run_department_confluence_backtest,
                ],
            ),
        ]
        trade_records = build_trade_records(data.llm_df)
        llm_markdown = render_llm_markdown_report(
            llm_context, llm_sections, trade_records,
            title="Blast Master — Reporte para análisis por LLM (XAUUSD)",
        )
        llm_path = save_markdown_report(llm_markdown, output_path / "data_analysis_llm_report.md")
        console.print(f"[green]Reporte LLM: {llm_path.resolve()}[/green]")


@cli.command()
@click.option("--output-dir", default="jupyter", show_default=True,
              help="Carpeta donde guardar los reportes generados.")
@click.option("--html-only", is_flag=True, help="Generar solo el reporte HTML (para humanos).")
@click.option("--llm-only", is_flag=True, help="Generar solo el reporte Markdown+JSON (para LLM).")
def report(output_dir, html_only, llm_only):
    """Genera los reportes de analisis (HTML humano + Markdown para LLM) sin abrir Jupyter."""
    generate_reports(output_dir=output_dir, html_only=html_only, llm_only=llm_only)


# --- Reporte de comparación (spec 002, T33) -------------------------------------
# Solo presentación (O2): los datos y el Markdown los arma tools/resolution_report.py.
# Por defecto el .md va a `reports/` dentro de ACCOUNTS_DATA_DIR (`.data/reports/`):
# tiene datos del journal y `.data/` está fuera de git (O3, plan.md §4). Código de
# salida 1 si una cuenta no existe o le falta la DB, o si falta el banco de velas.
RESOLUTION_REPORT_EXIT_MISSING = 1


@cli.command("resolution-report")
@click.option("--output-dir", default=None,
              help="Folder for the Markdown report (default: reports/ inside ACCOUNTS_DATA_DIR).")
@click.option("--account", "accounts", multiple=True,
              help="Account id from REAL_ACCOUNTS, e.g. 000. Repeat it for several (default: all).")
@click.pass_context
def resolution_report_command(ctx, output_dir, accounts):
    """Compare the candle resolution with the manual Efficiency Audit. Never writes to a database.

    Exit code: 0 written, 1 unknown account, missing account database or missing candle bank.
    """
    import config.auto_resolution as auto_cfg
    from tools.resolution_report import build_report, render_markdown

    unknown = [account for account in accounts if account not in auto_cfg.REAL_ACCOUNTS]
    if unknown:
        console.print(f"Unknown account: {', '.join(unknown)} (known: {', '.join(auto_cfg.REAL_ACCOUNTS)})",
                      style="red", markup=False)
        ctx.exit(RESOLUTION_REPORT_EXIT_MISSING)
    selected = {account: auto_cfg.REAL_ACCOUNTS[account] for account in (accounts or auto_cfg.REAL_ACCOUNTS)}
    missing = [db_name for db_name in selected.values()
               if not os.path.exists(os.path.join(auto_cfg.ACCOUNTS_DATA_DIR, db_name))]
    if missing:
        console.print(f"Missing account database: {', '.join(missing)} in {auto_cfg.ACCOUNTS_DATA_DIR}. "
                      "Use --account to leave it out.", style="red", markup=False)
        ctx.exit(RESOLUTION_REPORT_EXIT_MISSING)
    if not os.path.isdir(auto_cfg.CANDLE_BANK_DIR):
        console.print(f"Missing candle bank: {auto_cfg.CANDLE_BANK_DIR}", style="red", markup=False)
        ctx.exit(RESOLUTION_REPORT_EXIT_MISSING)

    reports = build_report(auto_cfg.ACCOUNTS_DATA_DIR, auto_cfg.CANDLE_BANK_DIR, real_accounts=selected)
    now = datetime.datetime.now()
    output_dir = output_dir or os.path.join(auto_cfg.ACCOUNTS_DATA_DIR, "reports")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"resolution_report_{now:%Y%m%d_%H%M}.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(reports, generated_at=now))

    for report in reports:
        directional = report.s4_strict_directional
        console.print(
            f"{report.account} {report.db_name}: {report.resolved}/{report.total} resolved, "
            f"{len(report.differences)} with differences, strict S4 directional {directional.candles.wins}/"
            f"{directional.candles.n} (manual {directional.manual_wins}/{directional.candles.n})",
            markup=False, highlight=False, soft_wrap=True,
        )
    console.print(f"Report written to {path}", style="green", markup=False, soft_wrap=True)


# --- Backfill (spec 002, T51) -----------------------------------------------------
# Solo presentación y preguntas (O2): el plan, las puertas y la escritura viven en
# tools/auto_backfill.py; la vista, en cli/backfill_view.py. Sin --apply no escribe nada.
# Códigos de salida (plan.md §4): 0 bien, 1 cuenta desconocida, 3 falló el ensayo,
# 4 sin backup de las últimas 24 h, 5 cancelado.
BACKFILL_EXIT_UNKNOWN_ACCOUNT = 1


@cli.command("backfill")
@click.option("--apply", "do_apply", is_flag=True, help="Write the changes, after the gates (rehearsal, backup, APPLY).")
@click.option("--account", "accounts", multiple=True,
              help="Account id from REAL_ACCOUNTS, e.g. 000. Repeat it for several (default: all).")
@click.option("--hide-unchanged", is_flag=True, help="Do not list the fields that would not change.")
@click.pass_context
def backfill_command(ctx, do_apply, accounts, hide_unchanged):
    """Fill the audits with the candle values: a git-style preview, and --apply to write.

    Exit code: 0 done, 1 unknown account, 3 rehearsal failed, 4 no backup from the last 24 h, 5 cancelled.
    """
    import uuid as _uuid
    import config.auto_resolution as auto_cfg
    from cli.backfill_view import ViewRun, print_runs
    from tools.auto_backfill import KIND_CONFLICT, build_plan, read_history, run_apply

    unknown = [account for account in accounts if account not in auto_cfg.REAL_ACCOUNTS]
    if unknown:
        console.print(f"Unknown account: {', '.join(unknown)} (known: {', '.join(auto_cfg.REAL_ACCOUNTS)})",
                      style="red", markup=False)
        ctx.exit(BACKFILL_EXIT_UNKNOWN_ACCOUNT)
    selected = {account: auto_cfg.REAL_ACCOUNTS[account] for account in (accounts or auto_cfg.REAL_ACCOUNTS)}
    plans = build_plan(auto_cfg.ACCOUNTS_DATA_DIR, auto_cfg.CANDLE_BANK_DIR, real_accounts=selected)
    preview_at = datetime.datetime.now()
    for plan in plans:
        db_path = os.path.join(auto_cfg.ACCOUNTS_DATA_DIR, plan.db_name)
        runs = [ViewRun(str(_uuid.uuid4()), preview_at, plan.account, plan.changes, dry_run=True)]
        runs += [ViewRun(run.run_id, run.run_at, plan.account, run.changes, dry_run=False)
                 for run in read_history(db_path, plan.account)]
        console.print(f"{plan.account} {plan.db_name}", style="bold cyan", markup=False)
        print_runs(console, runs, show_unchanged=not hide_unchanged)

    if not do_apply:
        console.print("Dry run: nothing was written. Use --apply to write.", markup=False)
        return

    accepted = {}
    stop = False
    for plan in plans:
        for change in plan.changes:
            if change.kind != KIND_CONFLICT or stop:
                continue
            question = (f"Accept {plan.account} {change.record_id[:8]} {change.table_name}.{change.field}: "
                        f"{change.old_value} -> {change.new_value}? (y = accept, n = keep, q = keep all the rest)")
            answer = click.prompt(question, type=click.Choice(["y", "n", "q"]), default="n", show_default=False)
            if answer == "q":
                stop = True
            elif answer == "y":
                accepted.setdefault(plan.account, set()).add((change.table_name, change.record_id, change.field))
    total_accepted = sum(len(keys) for keys in accepted.values())
    console.print(f"{total_accepted} conflict{'s' if total_accepted != 1 else ''} accepted.", markup=False)

    code, _ = run_apply(
        plans, auto_cfg.ACCOUNTS_DATA_DIR, auto_cfg.CANDLE_BANK_DIR,
        os.path.join(auto_cfg.ACCOUNTS_DATA_DIR, "backups"),
        confirm=lambda: click.prompt("Type APPLY to write", default="", show_default=False),
        accepted=accepted, report=lambda message: console.print(message, markup=False, soft_wrap=True))
    if code:
        ctx.exit(code)


# --- Banco de velas (spec 002, T21) ---------------------------------------------
# Solo presentación: la lógica vive en tools/candle_bank.py y tools/candle_sync.py
# (constitución, principio 3). Todo lo que se muestra va en inglés (N30). Los códigos
# de salida son los de plan.md §4: 0 = ok, 2 = el reloj no se verificó, 6 = el export
# falló o se saltó (incluido "ya hay otro en curso").
CANDLES_EXIT_NOT_VERIFIED = 2
CANDLES_EXIT_SKIPPED = 6


def _candle_symbols():
    """Los símbolos MT5 del banco: los que mapean las cuentas reales (config)."""
    import config.auto_resolution as auto_cfg
    return sorted(set(auto_cfg.MT5_SYMBOL_MAP.values()))


@cli.group()
def candles():
    """Candle bank (spec 002): status, first import and manual export."""


@candles.command("status")
def candles_status():
    """Show the state of the candle bank for each MT5 symbol."""
    import config.auto_resolution as auto_cfg
    from tools.candle_bank import collect_bank_status

    table = Table(title="Candle bank", box=box.ROUNDED)
    for header in ("Symbol", "Clock", "Verified by", "Last export", "Timeframes", "Last error"):
        table.add_column(header, overflow="fold")
    for row in collect_bank_status(auto_cfg.CANDLE_BANK_DIR, _candle_symbols()):
        clock_style = {"verified": "green", None: "dim"}.get(row.clock, "yellow")
        last_export = f"{row.last_result} ({row.last_run_id})" if row.last_result else "-"
        table.add_row(
            Text(row.symbol, style="bold"),
            Text(row.clock or "never exported", style=clock_style),
            Text(row.verified_by or "-"),
            Text(last_export),
            Text(" ".join(row.timeframes) or "-"),
            Text(row.last_error or "-"),
        )
    console.print(table)


@candles.command("import-legacy")
@click.pass_context
def candles_import_legacy(ctx):
    """Fill the bank the first time from the existing MT5Exports CSVs.

    Each symbol is imported only if its clock verifies against the account
    fills. The source CSVs are never modified. XAUUSD 5M is always excluded.
    """
    import config.auto_resolution as auto_cfg
    from tools.candle_bank import CandleBankLockedError, import_legacy_with_status

    any_unverified = any_locked = False
    for symbol in _candle_symbols():
        legacy_dir = os.path.join(auto_cfg.LEGACY_EXPORTS_DIR, symbol)
        bank_dir = os.path.join(auto_cfg.CANDLE_BANK_DIR, symbol)
        try:
            result = import_legacy_with_status(symbol, legacy_dir, bank_dir, auto_cfg.ACCOUNTS_DATA_DIR)
        except CandleBankLockedError:
            any_locked = True
            console.print(f"{symbol}: skipped, an export is already running", style="yellow", markup=False)
            continue
        if result.aligned is None and not result.imported and not result.excluded:
            console.print(f"{symbol}: no legacy CSVs in {legacy_dir}", style="dim", markup=False)
        elif result.aligned is True:
            imported = ", ".join(f"{tf}: {n} candles" for tf, n in result.imported.items()) or "nothing new"
            excluded = "".join(f"; excluded {tf} ({why})" for tf, why in result.excluded.items())
            console.print(f"{symbol}: imported {imported}{excluded}", style="green", markup=False)
        else:
            any_unverified = True
            reason = "clock_misaligned" if result.aligned is False else "clock_unverified"
            console.print(
                f"{symbol}: not imported, {reason} (left out: {', '.join(result.excluded)})",
                style="yellow", markup=False,
            )
    if any_locked:
        ctx.exit(CANDLES_EXIT_SKIPPED)
    if any_unverified:
        ctx.exit(CANDLES_EXIT_NOT_VERIFIED)


@candles.command("export")
@click.option("--symbol", required=True, help="MT5 symbol to export, e.g. XAUUSD.")
@click.option("--wait", type=float, default=None,
              help="Seconds to wait for the MT5 exporter (default: EXPORT_TIMEOUT_S).")
@click.pass_context
def candles_export(ctx, symbol, wait):
    """Export new candles from MT5 for one symbol and merge them into the bank.

    Exit code: 0 merged, 2 clock not verified, 6 export failed or skipped.
    """
    from tools import candle_sync

    known = {s.upper(): s for s in _candle_symbols()}
    canonical = known.get(symbol.upper())
    if canonical is None:
        console.print(
            f"Candle export skipped: no_mt5_symbol ({symbol!r} is not one of {', '.join(known.values())})",
            style="yellow", markup=False,
        )
        ctx.exit(CANDLES_EXIT_SKIPPED)
    console.print(f"Exporting {canonical} from MT5 ...", style="dim", markup=False)
    result = candle_sync.sync_symbol(canonical, timeout_s=wait)
    console.print(candle_sync.format_result_line(result), markup=False, highlight=False)
    code = candle_sync.exit_code_for(result)
    if code:
        ctx.exit(code)


def format_percentage(value):
    return f"{value * 100:.3f}%"

def flow_review_analysis():
    import datetime
    import calendar
    from sqlalchemy.orm import Session
    
    # Unify Table Generation via LEFT JOINs
    # Rolling 10 items grid query
    # Fans out to one row per tactical_audit execution (an analysis can now have
    # several). Ordered by the most recent activity (either a new execution or
    # the analysis itself) so a trade added later to an older analysis resurfaces.
    rolling_query = """
    SELECT u.id, u.asset, u.market_bias, u.calc_edge, u.created_at,
           e.bias_a, e.real_bias_b, e.resolution_type,
           t.order_filled, u.is_backdated
    FROM unified_department u
    LEFT JOIN efficiency_audit e ON u.id = e.id
    LEFT JOIN tactical_audit t ON t.trade_id = u.id
    ORDER BY COALESCE(t.created_at, u.created_at) DESC LIMIT 10;
    """

    def format_row_value(val, is_bias=False, is_filled=False, is_edge=False):
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
        if is_filled:
            filled_bool = val_str.lower() in ("true", "1")
            filled_style = "bold green" if filled_bool else "bold red"
            filled_label = "Filled" if filled_bool else "No Fill"
            return f"[{filled_style}]{filled_label}[/{filled_style}]"
        return val_str

    def render_ledger_table(rows):
        SHORTHANDS = {
            "Choppy / Neutral": "Neutral",
            "Confirmed (A equal to B)": "Conf (A=B)",
            "Invalidated (B not equal to A)": "Inval (B!=A)",
            "Overlap Invalidated (New Bias before resolution)": "Overlap Inval",
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
        table.add_column("Order Filled", justify="center", width=13, no_wrap=True)

        for idx, row in enumerate(rows):
            r_id, asset, market_bias, calc_edge, created_at, bias_a, real_bias_b, resolution_type, order_filled, is_backdated = row

            market_bias = SHORTHANDS.get(market_bias, market_bias)
            bias_a = SHORTHANDS.get(bias_a, bias_a)
            real_bias_b = SHORTHANDS.get(real_bias_b, real_bias_b)
            resolution_type = SHORTHANDS.get(resolution_type, resolution_type)

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
                format_row_value(order_filled, is_filled=True),
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
            """Devuelve una lista de segmentos (char, style) lista para volcar a un Text.
            Threshold ┊: rojo (-0.26, umbral Bearish) / verde (+0.26, umbral Bullish).
            Punto ●: color según el resultado del I_CD (verde Bullish / rojo Bearish / amarillo Choppy)."""
            e = float(edge_val)
            norm = (e + 1.0) / 2.0
            pos = int(norm * (width - 1))
            pos = max(0, min(pos, width - 1))
            mark_low = int((-0.26 + 1.0) / 2.0 * (width - 1))
            mark_high = int((0.26 + 1.0) / 2.0 * (width - 1))
            gauge = list("─" * width)
            styles = ["dim"] * width
            gauge[mark_low] = "┊"
            styles[mark_low] = "bold red"
            gauge[mark_high] = "┊"
            styles[mark_high] = "bold green"
            gauge[width // 2] = "┃"
            gauge[pos] = "●"
            styles[pos] = "bold green" if e >= 0.26 else "bold red" if e <= -0.26 else "bold yellow"
            return list(zip(gauge, styles))

        def render_structural_gauge(low, high, mark_price, entry_price, mae_price=None, reached=None, width=44):
            """Barra estructural: extremos = validation/invalidation price (low/high),
            marca mark_price y structural_entry_price, y (si existe) el precio real
            structural_mae, para ver si habria alcanzado el structural_entry_price.
            `reached` (bool|None) decide el color del marcador de structural_mae; la
            direccion (Long/Short) que determina `reached` se calcula fuera, a partir
            de market_bias.
            Devuelve una lista de segmentos (char, style) lista para volcar a un Text."""
            low_f, high_f = float(low), float(high)
            span = high_f - low_f
            def pos_of(v):
                if span == 0:
                    return 0
                norm = (float(v) - low_f) / span
                return max(0, min(int(round(norm * (width - 1))), width - 1))

            gauge = list("─" * width)
            styles = ["blue"] * width
            mark_pos = pos_of(mark_price)
            entry_pos = pos_of(entry_price)
            gauge[mark_pos] = "┊"
            styles[mark_pos] = "bold cyan"
            gauge[entry_pos] = "┃"
            styles[entry_pos] = "bold white"

            if mae_price is not None:
                mae_pos = pos_of(mae_price)
                gauge[mae_pos] = "●"
                styles[mae_pos] = "bold green" if reached else "bold red"

            return list(zip(gauge, styles))

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
        SELECT u.id, u.asset, u.market_bias, u.calc_edge, u.created_at, u.updated_at, u.edge_description,
               u.p4_hierarchy, u.p1_timeframe, u.p1_type, u.nodes_l1, u.nodes_l2, u.tactical_classification,
               u.long_prob, u.short_prob, u.no_trade_prob, u.is_backdated, u.edge_validation_price, u.structural_invalidation, u.mark_price,
               e.bias_a, e.resolution_type, e.real_bias_b, e.structural_resolution, e.failure_reason,
               e.specific_bias_compliance, e.false_regime_rate, e.lesson_learned as e_lesson, e.efficiency_timeframe,
               e.structural_mae, e.structural_mfe,
               t.order_filled, t.entry_time, t.exit_time, t.tier_setup, t.market_state, t.exit_type,
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
               t.risk_usd, t.r_r, t.r_multiple, t.captured_mfe, t.trade_decision,
               t.size_source, t.size_match_confidence, t.size_migrated_at,
               al.department, al.layer_name, al.direction, al.strength, al.thesis
        FROM unified_department u
        LEFT JOIN efficiency_audit e ON u.id = e.id
        LEFT JOIN tactical_audit t ON t.id = (
            SELECT id FROM tactical_audit WHERE trade_id = u.id ORDER BY created_at DESC LIMIT 1
        )
        LEFT JOIN analysis_layer al ON u.id = al.trade_id
        WHERE u.id = :selected_id;
        """
        cursor = db_session.execute(text(detail_query), {"selected_id": selected_id})
        detail_rows = cursor.fetchall()

        if not detail_rows:
            console.print("[bold red]Analysis record not found![/bold red]")
            input("Press Enter to continue...")
            return

        # An analysis can now have several tactical_audit rows (executions); the
        # query above only joins the most recent one to keep this detail panel's
        # rendering logic (below) untouched. List the rest here so they're not
        # silently hidden -- edit a specific one via "Repair Analysis Audits".
        executions_cursor = db_session.execute(
            text("SELECT id, created_at, entry_time, exit_time, order_filled FROM tactical_audit WHERE trade_id = :selected_id ORDER BY created_at DESC"),
            {"selected_id": selected_id}
        )
        execution_rows = executions_cursor.fetchall()
            
        cols = [
            "id", "asset", "market_bias", "calc_edge", "created_at", "updated_at", "edge_description",
            "p4_hierarchy", "p1_timeframe", "p1_type", "nodes_l1", "nodes_l2", "tactical_classification",
            "long_prob", "short_prob", "no_trade_prob", "is_backdated", "edge_validation_price", "structural_invalidation", "mark_price",
            "bias_a", "resolution_type", "real_bias_b", "structural_resolution", "failure_reason",
            "specific_bias_compliance", "false_regime_rate", "e_lesson", "efficiency_timeframe",
            "structural_mae", "structural_mfe",
            "order_filled", "entry_time", "exit_time", "tier_setup", "market_state", "exit_type",
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
            "pre_trade_emotions", "mid_trade_emotions", "post_trade_emotions",
            "risk_usd", "r_r", "r_multiple", "captured_mfe", "trade_decision",
            "size_source", "size_match_confidence", "size_migrated_at"
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

        # ── Métricas monetarias: se MUESTRAN los valores YA PERSISTIDOS ──
        # risk_usd / notional_size / capital_at_risk / r_r / r_multiple / captured_* /
        # pnl_and_cost / trade_decision vienen de la fila (Fase 2a/2c ya aplicó
        # contract_size al guardarlos). Este panel NO los recalcula -> se eliminó la
        # 4ta copia de la fórmula que ignoraba contract_size (bug: mostraba risk_usd
        # 1/contract_size del valor real en trades migrados por Track B).
        from tools import pnl_calculator as _pnl
        try:
            _pnl.resolve_spec(record.get("asset"))
            record["_sym_verified"] = True
        except (_pnl.UnverifiedSymbolError, _pnl.UnknownSymbolError):
            record["_sym_verified"] = False

        ep = Decimal(str(record["entry_price"])) if record["entry_price"] else Decimal('0.0')
        cp = Decimal(str(record["closing_price"])) if record["closing_price"] else Decimal('0.0')
        sl = Decimal(str(record["stop_loss"])) if record["stop_loss"] else Decimal('0.0')
        tp = Decimal(str(record["take_profit"])) if record["take_profit"] else Decimal('0.0')
        size = Decimal(str(record["size"])) if record["size"] else Decimal('0.0')

        if not record.get("trade_decision") and ep != Decimal('0.0') and sl != Decimal('0.0'):
            record["trade_decision"] = "Long" if ep > sl else "Short"

        # notional_size_usd no es columna -> el panel muestra el notional_size persistido
        record["notional_size_usd"] = record.get("notional_size")

        # dist_to_sl / dist_to_tp / pnl / cost NO son columnas -> se derivan con la
        # función única (core.math_engine.calculate_algebraic_metrics), nunca una copia.
        record["dist_to_sl"] = None
        record["dist_to_tp"] = None
        record["pnl"] = None
        record["cost"] = Decimal('0.0')
        record["trade_duration"] = None
        if ep != Decimal('0.0') and sl != Decimal('0.0') and size > Decimal('0.0'):
            try:
                _cs = _pnl.resolve_spec(record["asset"])["contract_size"] if record["_sym_verified"] else None
            except (_pnl.UnverifiedSymbolError, _pnl.UnknownSymbolError):
                _cs = None
            try:
                _m = calculate_algebraic_metrics(
                    record.get("trade_decision") or ("Long" if ep > sl else "Short"),
                    float(ep), float(sl), float(cp), float(tp), float(size),
                    float(record["mae_adverse"] or 0.0), float(record["mfe_favorable"] or 0.0),
                    0.0, contract_size=_cs,
                )
                record["dist_to_sl"] = _m["dist_to_sl"]
                record["dist_to_tp"] = _m["dist_to_tp"]
                record["pnl"] = _m["pnl"]
                if record["pnl_and_cost"] is not None:
                    record["cost"] = _m["pnl"] - Decimal(str(record["pnl_and_cost"]))
            except (ValueError, ArithmeticError):
                pass

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
        struct_text.append("                              ", style="dim")
        for ch, seg_style in render_edge_gauge(edge_val, width=44):
            struct_text.append(ch, style=seg_style)
        struct_text.append(f"  [{'+' if edge_val >= 0 else ''}{edge_val:.2f}]\n", style="dim")
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
        mark_price_val = str(record['mark_price']) if record['mark_price'] is not None else "N/A"
        struct_text.append("  Mark Price: ", style="dim")
        struct_text.append(f"{mark_price_val}\n", style="bold white")
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

            # Mark Price / Structural MAE / Structural MFE
            mark_price_raw = record.get('mark_price')
            structural_mae_raw = record.get('structural_mae')
            structural_mfe_raw = record.get('structural_mfe')
            evp_raw = record.get('edge_validation_price')
            si_raw = record.get('structural_invalidation')

            eff_text.append("\n  Mark Price:     ", style="dim")
            eff_text.append(f"{mark_price_raw if mark_price_raw is not None else 'N/A'}", style="bold cyan")
            eff_text.append("     Structural MAE: ", style="dim")
            eff_text.append(f"{structural_mae_raw if structural_mae_raw is not None else 'N/A'}", style="bold red")
            eff_text.append("     Structural MFE: ", style="dim")
            eff_text.append(f"{structural_mfe_raw if structural_mfe_raw is not None else 'N/A'}\n", style="bold green")

            # Structural Entry Simulation: entrada calculada para un R:R fijo
            # (STRUCTURAL_TARGET_RR) usando SL=structural_invalidation, TP=edge_validation_price.
            if mark_price_raw is not None and evp_raw is not None and si_raw is not None:
                mark_price_dec = Decimal(str(mark_price_raw))
                evp_dec = Decimal(str(evp_raw))
                si_dec = Decimal(str(si_raw))
                try:
                    structural_entry_price = calculate_structural_entry(evp_dec, si_dec)
                except (ValueError, ZeroDivisionError):
                    structural_entry_price = None

                if structural_entry_price is not None:
                    target_rr = os.getenv("STRUCTURAL_TARGET_RR", "2.2")
                    eff_text.append(f"\n  ── Structural Entry Simulation (Target R:R 1:{target_rr}) ──\n", style="bold cyan")
                    eff_text.append("  Structural Entry Price: ", style="dim")
                    eff_text.append(f"{structural_entry_price:.2f}\n", style="bold white")

                    market_bias_val = record['market_bias']
                    direction = "Long" if market_bias_val == "Bullish" else "Short" if market_bias_val == "Bearish" else None
                    structural_mae_dec = Decimal(str(structural_mae_raw)) if structural_mae_raw is not None else None

                    reached = None
                    if direction is not None and structural_mae_dec is not None:
                        reached = (structural_mae_dec >= structural_entry_price) if direction == "Short" else (structural_mae_dec <= structural_entry_price)

                    low = min(evp_dec, si_dec)
                    high = max(evp_dec, si_dec)
                    low_label = "Validation" if low == evp_dec else "Invalidation"
                    high_label = "Invalidation" if high == si_dec else "Validation"

                    segments = render_structural_gauge(low, high, mark_price_dec, structural_entry_price, mae_price=structural_mae_dec, reached=reached)
                    eff_text.append("  ")
                    for ch, seg_style in segments:
                        eff_text.append(ch, style=seg_style)
                    eff_text.append(f"  [{structural_entry_price:.2f}]\n")
                    low_caption_style = "bold red" if low_label == "Invalidation" else "bold green"
                    high_caption_style = "bold red" if high_label == "Invalidation" else "bold green"
                    eff_text.append(f"  {low:.2f} ({low_label})", style=low_caption_style)
                    eff_text.append(" " * 20, style="dim")
                    eff_text.append(f"{high:.2f} ({high_label})\n", style=high_caption_style)

                    if direction is None:
                        status_text, status_style = "Pending (market_bias no es Bullish/Bearish)", "dim"
                    elif structural_mae_dec is None:
                        status_text, status_style = "Pending (sin Structural MAE registrado)", "dim"
                    elif reached:
                        status_text, status_style = "REACHED", "bold green"
                    else:
                        status_text, status_style = "NOT REACHED", "bold yellow"
                    eff_text.append("  Structural Entry: ", style="dim")
                    eff_text.append(f"{status_text}\n", style=status_style)

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
        if record["order_filled"] is None:
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
            tact_text.append("    │  Order Filled: ", style="dim")
            tact_text.append(Text.from_markup(f"{format_row_value(record['order_filled'], is_filled=True)}\n\n"))

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

            def fmt_money(val):
                # valor persistido -> "$X.XX"; NULL -> N/A con el motivo, mismo texto que el wizard
                if val is not None:
                    return f"${fmt_f(val)}"
                return "N/A (símbolo no verificado)" if not record.get("_sym_verified") else "N/A (no calculado)"

            # Position Sizing
            tact_text.append("  ── Position Sizing ──\n", style="bold white")
            tact_text.append(f"  Size: {record['size']}          Entry Price: {record['entry_price']}     Closing Price: {record['closing_price']}\n", style="dim")
            tact_text.append(f"  Stop Loss: {record['stop_loss']}    Take Profit: {record['take_profit']}    Could Hit TP: {record['could_hit_tp']}\n", style="dim")
            if record.get("size_source") == "migrated_mt5_report_v1":
                tact_text.append(f"  Origen del size: migrado de reporte MT5 ({record.get('size_match_confidence') or 'n/a'}, {str(record.get('size_migrated_at') or '')[:10]})\n", style="yellow")
            elif record.get("size_source"):
                tact_text.append(f"  Origen del size: {record['size_source']}\n", style="dim")
            tact_text.append("\n")

            # Risk Metrics
            tact_text.append("  ── Risk Metrics ──\n", style="bold white")
            tact_text.append(f"  Notional Size: {fmt_money(record['notional_size_usd'])}        Risk (USD): {fmt_money(record['risk_usd'])}\n", style="dim")
            tact_text.append(f"  Capital @ Risk: {fmt_money(record['capital_at_risk'])}        ", style="dim")
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
        if record["order_filled"] is None:
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
        if record["order_filled"] is None:
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
            try:
                emo_list = json.loads(raw_emo) if isinstance(raw_emo, str) else (raw_emo or [])
                emo_str = ", ".join([str(x).replace("Emotions.", "") for x in emo_list if x]) if emo_list else "N/A"
            except Exception: emo_str = "N/A"
            try:
                be_list = json.loads(raw_be) if isinstance(raw_be, str) else (raw_be or [])
                be_list_clean = [str(x).replace("BehavioralErrors.", "") for x in be_list if x]
                be_str = ", ".join(be_list_clean) if be_list_clean else "N/A"
            except Exception:
                be_list_clean = []
                be_str = "N/A"

            psych_text.append(f"  Emotions:       {format_indented_block(emo_str, indent_spaces=18, first_line_flush=True, wrap_width=60)}\n", style="white")
            psych_text.append(f"  Behav. Errors:  {format_indented_block(be_str, indent_spaces=18, first_line_flush=True, wrap_width=60)}\n", style="white")

            detected = compute_detected_patterns({
                "primary_emotion": record.get("primary_emotion"),
                "anxiety_level": record.get("anxiety_level"),
                "impatience_level": record.get("impatience_level"),
                "mental_clarity_level": record.get("mental_clarity_level"),
                "behavioral_errors": be_list_clean,
                "confirmation_status": record.get("confirmation_status"),
                "gates_failed": record.get("gates_failed"),
                "followed_plan": record.get("followed_plan"),
            })
            psych_text.append("  Detected Patterns:\n", style="dim")
            for label, color in detected:
                psych_text.append(f"    • {label}\n", style=color)

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

        console.print(psych_panel)
        console.print(fw_panel)
        console.print(tact_panel)
        console.print(eff_panel)
        console.print(dashboard)

        if len(execution_rows) > 1:
            exec_text = Text()
            exec_text.append(f"{len(execution_rows)} ejecuciones registradas para este análisis (arriba se muestra la más reciente):\n\n")
            for ex_id, ex_created, ex_entry, ex_exit, ex_filled in execution_rows:
                created_str = to_local_display(ex_created, '%Y-%m-%d %H:%M') if ex_created else "N/A"
                entry_str = to_local_display(ex_entry, '%Y-%m-%d %H:%M') if ex_entry else "sin entry"
                fill_str = "Filled" if ex_filled else "No Fill"
                exec_text.append(f"  • {ex_id[:8]} | creado {created_str} | entry {entry_str} | {fill_str}\n")
            exec_text.append("\nEdita una ejecución específica desde Configuration > Repair Analysis Audits.", style="dim")
            console.print(Panel(exec_text, title="[bold cyan]Ejecuciones (Tactical Audits)[/bold cyan]", border_style="cyan", box=box.ROUNDED))

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
                backdated_ts, started_at = ask_clone_timestamps()

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

            flow_new_analysis(backdated_timestamp=backdated_ts, cloned_state=macro_state, started_at=started_at)
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
                           t.order_filled, u.is_backdated
                    FROM unified_department u
                    LEFT JOIN efficiency_audit e ON u.id = e.id
                    LEFT JOIN tactical_audit t ON t.trade_id = u.id
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

def _preview(text, length=30):
    text = str(text)
    return text if len(text) <= length else text[:length] + "..."

def _dir_icon(d):
    return "🟢" if d == "Long" else "🔴" if d == "Short" else "🟡"

def _str_icon(s):
    return "●●●" if s == "Strong" else "●●○" if s == "Mid" else "●○○"

def warn_if_mark_price_off(asset, mark_price, mark_price_time):
    """RF-3: después de guardar un análisis, una línea si el Mark Price no cae en su vela del banco. Es solo un aviso:
    si el chequeo no se puede hacer o falla, no muestra nada y el análisis ya quedó guardado."""
    if mark_price is None or mark_price_time is None:
        return
    try:
        import config.auto_resolution as auto_cfg
        import tools.auto_resolution as auto_resolution
        check = auto_resolution.check_saved_mark_price(asset, float(mark_price), mark_price_time,
                                                       auto_cfg.CANDLE_BANK_DIR)
    except Exception as exc:
        logging.info("Mark Price check skipped: %s", exc)
        return
    if check is not None and check.fits is False:
        console.print(
            f"Warning: Mark Price {float(mark_price):g} is outside its {check.timeframe} candle at "
            f"{mark_price_time:%Y-%m-%d %H:%M} by {check.distance:.2f}. The analysis was saved.",
            style="yellow", markup=False, highlight=False, soft_wrap=True,
        )


def _export_line(text, style):
    console.print(text, style=style, markup=False, highlight=False, soft_wrap=True)


def auto_export_in_background(symbols_of):
    """Spec 002 (T52, RF-20, RF-20d): lanza el export de velas en un proceso aparte y sigue sin esperar. `symbols_of`
    recibe el módulo `tools.auto_export` y devuelve los símbolos MT5; solo se llama con `AUTO_EXPORT` prendido, así que
    apagado no hace nada, ni lee ninguna DB (N31). Nunca levanta (INV-2)."""
    try:
        import tools.auto_export as auto_export
        if not auto_export.enabled():
            return None
        launch = auto_export.start_export(symbols_of(auto_export))
    except Exception as exc:  # noqa: BLE001 -- INV-2: el export nunca frena el CLI
        _export_line(f"Candle export skipped: {type(exc).__name__}: {exc}", "yellow")
        return None
    if launch is None:
        return None
    if launch.launch_error:
        _export_line(f"Candle export skipped: {launch.launch_error}", "yellow")
    elif launch.launched:
        _export_line(f"Candle export started in the background: {', '.join(launch.launched)}", "dim")
    if launch.in_progress:
        _export_line(f"Candle export already running: {', '.join(launch.in_progress)}", "dim")
    return launch


def efficiency_proposal(trade_id):
    """Spec 002 (RF-7, RF-7f): la propuesta de las velas para el audit de un análisis de la cuenta activa, o `None`
    si no se pudo pedir. Es solo una ayuda: cualquier error se registra y el wizard sigue como siempre."""
    try:
        import config.auto_resolution as auto_cfg
        import tools.auto_resolution as auto_resolution
        db_path = get_active_engine().url.database
        return auto_resolution.propose_for_trade(db_path, trade_id, auto_cfg.CANDLE_BANK_DIR)
    except Exception as exc:
        logging.info("Candle proposal unavailable for %s: %s", trade_id, exc)
        return None


def auto_default(value):
    """`default=value` para un prompt, solo si las velas propusieron algo (RF-7). Sin propuesta no agrega nada, así el
    prompt queda idéntico al de antes (INV-1)."""
    return {} if value is None else {"default": value}


# La precisión de la hora del toque según la TF de la vela que tocó (N19).
TOUCH_PRECISION = {"1M": "±1 min", "5M": "±5 min", "15M": "±15 min", "30M": "±30 min", "1H": "±1 h"}


def touch_precision(proposal):
    """El texto de la precisión de la hora propuesta (`±1 min` con 1M), o `None` si no se sabe."""
    touch = proposal.resolution.first_touch if proposal is not None and proposal.resolution is not None else None
    return TOUCH_PRECISION.get(touch.timeframe) if touch is not None else None


def resolution_time_entry(value):
    """La respuesta del prompt "Resolution Time" como datetime o `None`. Al retomar un audit pausado llega como el
    texto `YYYY-MM-DD HH:MM` que guarda `AuditSession.save_state`."""
    if isinstance(value, str):
        return datetime.datetime.strptime(value.strip(), "%Y-%m-%d %H:%M") if value.strip() else None
    return value


def resolution_time_source(entered, proposal):
    """
    RF-14b: de dónde salió `resolution_time`, o por qué quedó vacía (aclaración de T43 en el plan §2.1):
      - `candles`: el operador aceptó la hora propuesta;
      - `corrected`: la hora la puso el operador, porque cambió la propuesta o porque no la había;
      - el motivo (`pending_candles`, `open`, `clock_unverified`...): quedó vacía y las velas no proponían nada;
      - `None`: el operador vació una hora propuesta, o la propuesta no se pudo pedir.
    """
    proposed = proposal.resolution_time if proposal is not None else None
    if entered is not None:
        if proposed is not None and entered == proposed.replace(second=0, microsecond=0):
            return RESOLUTION_TIME_SOURCE_CANDLES
        return RESOLUTION_TIME_SOURCE_CORRECTED
    if proposal is not None and proposed is None and proposal.reason in RESOLUTION_TIME_SOURCES:
        return proposal.reason
    return None


def auto_mark(value, proposed):
    """T44: " (auto)" si el valor sigue siendo el que propusieron las velas; vacío si no hay propuesta o se corrigió."""
    if value is None or proposed is None:
        return ""
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        try:
            return " (auto)" if abs(float(value) - float(proposed)) < 1e-9 else ""
        except (TypeError, ValueError):
            return ""
    text = value.value if hasattr(value, "value") else value
    return " (auto)" if text == proposed else ""


def resolution_time_display(value, source):
    """T44: la hora de resolución para el panel y el menú de edición; sin hora, el motivo (RF-14b)."""
    if value is None:
        return f"N/A ({source})" if source else "N/A"
    return f"{value:%Y-%m-%d %H:%M}" + (" (auto)" if source == RESOLUTION_TIME_SOURCE_CANDLES else "")


def proposal_status_line(proposal):
    """La línea que explica por qué no hay propuesta (RF-7f, RF-5b), en inglés (N30). `None` si hay propuesta."""
    if proposal is None:
        return "Candles: no proposal (unavailable)"
    if proposal.reason == "open":
        return "Still open according to candles"
    if proposal.reason:
        return f"Candles: no proposal ({proposal.reason})"
    return None


def tactical_proposal(asset, entry_time, exit_time, entry_price, stop_loss, take_profit):
    """Spec 002 (T45, T46): la propuesta de las velas para el Tactical Audit, o `None` si no se pudo pedir. Nunca
    bloquea el wizard."""
    try:
        import config.auto_resolution as auto_cfg
        import tools.auto_resolution as auto_resolution
        return auto_resolution.propose_tactical(asset, entry_time, exit_time, float(entry_price), float(stop_loss),
                                                None if take_profit is None else float(take_profit),
                                                auto_cfg.CANDLE_BANK_DIR)
    except Exception as exc:
        logging.info("Tactical candle proposal unavailable: %s", exc)
        return None


def tactical_reason_line(field, proposal, part):
    """Una línea con el motivo por el que no hay propuesta para `field` (RF-9b a RF-9d, RF-10b, RF-10d), o `None`."""
    if proposal is None:
        reason = "unavailable"
    elif proposal.reason:
        reason = proposal.reason
    elif part is None:
        return None
    elif part.reason:
        reason = part.reason
    else:
        return None
    return f"Candles: no proposal for {field} ({reason})"


def ask_clone_timestamps():
    """
    El modo de hora de un clon: `(hora tipeada, None)` con "[2] Enter Custom/Backdated Time", y `(None, hora de la
    elección)` con "[1] Use Current System Time", que es el inicio del análisis clonado (RF-13c, N6). Cancelar la hora
    tipeada levanta `GoBackException`.
    """
    ts_choice = inquirer.select(
        message="Select Timestamp mode for cloned trade >",
        choices=[
            Choice("current", name="[1] Use Current System Time"),
            Choice("custom", name="[2] Enter Custom/Backdated Time")
        ],
        pointer=">",
        qmark=""
    ).execute()
    if ts_choice == "custom":
        return get_mandatory_datetime("Enter Target Timestamp", allow_cancel=True), None
    return None, _now_gt()


def flow_new_analysis(backdated_timestamp=None, cloned_state: dict = None, started_at=None):
    trade_id = str(uuid.uuid4())
    console.print(f"\n[muted]Initialized new unified trade context: {trade_id}[/muted]")
    
    session = AnalysisSession(trade_id)
    if cloned_state:
        session.state.update(cloned_state)

    # Spec 002: el inicio del análisis. En un retroactivo o un clon [2] es la hora tipeada, que gana (RF-13b, N26);
    # en un clon [1], la hora de la elección (RF-13c); si no, se fija la primera vez que se confirma la fuerza de P0
    # (RF-13) y no cambia con los reinicios del wizard ni si se vuelve a editar P0.
    analysis_start_time = backdated_timestamp or started_at
    # RF-13d: cuándo se tipeó el Mark Price. Sobrevive a los reinicios; si el operador vuelve atrás y lo tipea de
    # nuevo, cuenta la última vez.
    typed_at = {}
        
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
                    keybindings={"skip": []},
                    style=INQUIRER_STYLE
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
            if analysis_start_time is None:
                analysis_start_time = _now_gt()
            
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

            # --- Edge Bias & Probabilities Preview (calculado apenas P0-P4 están completos,
            # antes de pedir Efficiency Timeframe / Mark Price / Validation / Invalidation) ---
            preview_x0 = get_dir_val(session.state.get("p0_dir")) * get_str_val(session.state.get("p0_str"))
            preview_x1 = get_dir_val(session.state.get("p1_dir")) * get_str_val(session.state.get("p1_str"))
            preview_x2 = get_dir_val(session.state.get("p2_dir")) * get_str_val(session.state.get("p2_str"))
            preview_x3 = get_dir_val(session.state.get("p3_dir")) * get_str_val(session.state.get("p3_str"))
            preview_x4 = get_dir_val(session.state.get("p4_dir")) * get_str_val(session.state.get("p4_str"))

            preview_i_cd = calculate_edge_score(preview_x0, preview_x1, preview_x2, preview_x3, preview_x4)
            preview_bias = determine_market_bias(preview_i_cd)
            preview_long_prob, preview_short_prob, preview_no_trade_prob = calculate_probabilities(preview_i_cd)

            preview_bias_color = "success" if preview_bias == "Bullish" else "danger" if preview_bias == "Bearish" else "warning"

            preview_text = Text()
            preview_text.append("--- Real-Time Metrics & Probability Dashboard ---\n", style="bold cyan")
            preview_text.append("Market Bias: ", style="dim")
            preview_text.append(f"{preview_bias}\n", style=preview_bias_color)
            preview_text.append("Directional Probabilities:\n", style="dim")
            preview_text.append("  Long Prob:      ", style="dim")
            preview_text.append(f"{preview_long_prob * 100:.1f}%\n", style="success")
            preview_text.append("  Short Prob:     ", style="dim")
            preview_text.append(f"{preview_short_prob * 100:.1f}%\n", style="danger")
            preview_text.append("  No-Trade Prob:  ", style="dim")
            preview_text.append(f"{preview_no_trade_prob * 100:.1f}%\n", style="warning")

            console.print(Panel(
                preview_text,
                title="[bold primary]Edge Bias & Probabilities Preview[/bold primary]",
                border_style="primary",
                box=box.ROUNDED
            ))
            console.print()

            efficiency_timeframe = session.prompt("efficiency_timeframe", lambda: bind_pause(inquirer.select(
                message="Select Efficiency Timeframe [15M/1H/4H] >",
                choices=[Choice("15M", name="15M"), Choice("1H", name="1H"), Choice("4H", name="4H")],
                pointer=">",
                qmark="",
                style=INQUIRER_STYLE
            )).execute())

            def prompt_mark_price():
                value = bind_pause(inquirer.text(message="Mark Price (Asset price at analysis completion) [Optional] >", style=INQUIRER_STYLE)).execute()
                typed_at["mark_price"] = _now_gt()
                return value

            mark_price_raw = session.prompt("mark_price_raw", prompt_mark_price)

            evp_raw = session.prompt("evp_raw", lambda: bind_pause(inquirer.text(message="Edge Validation Price (Target Convergence) [Optional] >", style=INQUIRER_STYLE)).execute())
            si_raw = session.prompt("si_raw", lambda: bind_pause(inquirer.text(message="Structural Invalidation Price (Nullification Threshold) [Optional] >", style=INQUIRER_STYLE)).execute())

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

                indented_prev_edge = format_indented_block(previous_edge, indent_spaces=11, first_line_flush=False, wrap_width=60)

                dashboard_text = Text()
                dashboard_text.append("--- Previous Session Edge Description Reference ---\n", style="bold cyan")
                dashboard_text.append(f"{indented_prev_edge}\n", style="italic white")

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
                x0 = get_dir_val(p0_dir) * get_str_val(p0_str)
                x1 = get_dir_val(p1_dir) * get_str_val(p1_str)
                x2 = get_dir_val(p2_dir) * get_str_val(p2_str)
                x3 = get_dir_val(p3_dir) * get_str_val(p3_str)
                x4 = get_dir_val(p4_dir) * get_str_val(p4_str)

                i_cd = calculate_edge_score(x0, x1, x2, x3, x4)
                market_bias = determine_market_bias(i_cd)

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
                        qmark="",
                        style=INQUIRER_STYLE
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
                mark_price_display = session.state.get("mark_price_raw", "")
                meta_text.append(f"Mark Price: ", style="dim")
                meta_text.append(f"{mark_price_display if mark_price_display else 'N/A'}\n", style="white")

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
                        Choice("save", name="[1] ✓ Confirm & Save"),
                        Choice("edit", name="[2] ✎ Edit a Field"),
                        Choice("discard", name="[3] ✕ Discard")
                    ],
                    pointer=">",
                    qmark="",
                    style=INQUIRER_STYLE
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
                                p4_hierarchy=tactical.p4_hierarchy.value,
                                p1_timeframe=tactical.p1_timeframe.value,
                                p1_type=tactical.p1_type.value,
                                nodes_l1=tactical.nodes_l1,
                                nodes_l2=tactical.nodes_l2,
                                tactical_classification=tactical.tactical_classification.value,
                                long_prob=tactical.long_prob,
                                short_prob=tactical.short_prob,
                                no_trade_prob=tactical.no_trade_prob,
                                is_backdated=backdated_timestamp is not None,
                                analysis_start_time=analysis_start_time,
                            )
                            
                            evp_val = session.state.get("evp_raw", "")
                            si_val = session.state.get("si_raw", "")
                            mark_price_val = session.state.get("mark_price_raw", "")
                            try:
                                new_record.edge_validation_price = Decimal(str(evp_val)) if evp_val.strip() else None
                                new_record.structural_invalidation = Decimal(str(si_val)) if si_val.strip() else None
                            except Exception:
                                console.print("[bold red]Invalid decimal input for price metrics. Setting to None.[/bold red]")
                                new_record.edge_validation_price = None
                                new_record.structural_invalidation = None
                            try:
                                new_record.mark_price = Decimal(str(mark_price_val)) if mark_price_val.strip() else None
                            except Exception:
                                console.print("[bold red]Invalid decimal input for Mark Price. Setting to None.[/bold red]")
                                new_record.mark_price = None
                            # RF-13d: en un retroactivo o un clon [2], la hora tipeada del análisis. RF-13e: `saved_at`
                            # es siempre la hora real, también en un retroactivo (N33).
                            if new_record.mark_price is not None:
                                new_record.mark_price_time = backdated_timestamp or typed_at.get("mark_price")
                            new_record.saved_at = _now_gt()
                            saved_mark_price = (new_record.mark_price, new_record.mark_price_time)
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

                    # Fuera del bloque de la transacción: el análisis ya está guardado y el aviso nunca lo deshace.
                    warn_if_mark_price_off(asset, *saved_mark_price)
                    # T52 (RF-20): el export del símbolo, de fondo; el wizard sigue sin esperarlo.
                    auto_export_in_background(lambda auto_export: auto_export.symbols_for_assets([asset]))

                    feed_now = inquirer.select(
                        message="¿Deseas alimentar un Tactical Audit ahora para este análisis?",
                        choices=[
                            Choice("yes", name="Sí, alimentar Tactical Audit ahora"),
                            Choice("no", name="No, volver al menú"),
                        ],
                        pointer=">",
                        qmark="",
                        style=INQUIRER_STYLE
                    ).execute()

                    if feed_now == "yes":
                        preselected_payload = {
                            "asset": asset,
                            "efficiency": {"Market_Bias": market_bias, "Calc_edge": i_cd},
                            "tactical": {"tactical_classification": tactical.tactical_classification.value, "calc_edge": i_cd},
                        }
                        flow_pending_audits(
                            preselected_trade_id=trade_id,
                            preselected_payload=preselected_payload,
                            preselected_choice="tac",
                            state_rule="promote",
                            force_new_tactical=True,
                        )
                        return

                    input("Press Enter to continue...")
                    return

                elif action_choice == "discard":
                    raise PauseAuditException("Discard requested")

                elif action_choice == "edit":
                    edit_choices = [
                        Choice("asset", name=f"Asset: {asset}"),
                        Separator("── P0 · Macro Vector ──"),
                        Choice("p0_thesis", name=f"P0 Thesis: {_preview(p0_thesis)}"),
                        Choice("p0_dir", name=f"P0 Direction: {p0_dir.value}"),
                        Choice("p0_str", name=f"P0 Strength: {p0_str.value}"),
                        Separator("── P1 · Tactical Timing ──"),
                        Choice("p1_thesis", name=f"P1 Thesis: {_preview(p1_thesis)}"),
                        Choice("p1_dir", name=f"P1 Direction: {p1_dir.value}"),
                        Choice("p1_str", name=f"P1 Strength: {p1_str.value}"),
                        Choice("p1_tf", name=f"P1 Timeframe: {p1_tf.value}"),
                        Choice("p1_type", name=f"P1 Fractal Type: {p1_type.value}"),
                        Choice("nodes_l1", name=f"Nodes L1: {nodes_l1}"),
                        Choice("nodes_l2", name=f"Nodes L2: {nodes_l2}"),
                        Separator("── P2 · Structure ──"),
                        Choice("p2_thesis", name=f"P2 Thesis: {_preview(p2_thesis)}"),
                        Choice("p2_dir", name=f"P2 Direction: {p2_dir.value}"),
                        Choice("p2_str", name=f"P2 Strength: {p2_str.value}"),
                        Separator("── P3 · Trend ──"),
                        Choice("p3_thesis", name=f"P3 Thesis: {_preview(p3_thesis)}"),
                        Choice("p3_dir", name=f"P3 Direction: {p3_dir.value}"),
                        Choice("p3_str", name=f"P3 Strength: {p3_str.value}"),
                        Separator("── P4 · Hierarchy ──"),
                        Choice("p4_thesis", name=f"P4 Thesis: {_preview(p4_thesis)}"),
                        Choice("p4_dir", name=f"P4 Direction: {p4_dir.value}"),
                        Choice("p4_str", name=f"P4 Strength: {p4_str.value}"),
                        Choice("p4_hier", name=f"P4 Hierarchy: {p4_hier.value}"),
                        Separator("── Edge / Meta ──"),
                        Choice("edge_desc", name=f"Edge Description: {_preview(edge_desc)}"),
                        Choice("efficiency_timeframe", name=f"Efficiency Timeframe: {efficiency_timeframe}"),
                        Choice("bias_a", name=f"Bias A: {bias_a.value}"),
                        Choice("tact_class", name=f"Tactical Classification: {tact_class.value}"),
                        Separator(),
                        Choice("back", name="[<] Back to Review")
                    ]

                    field_to_edit = inquirer.select(
                        message="Select Field to Edit >",
                        choices=edit_choices,
                        pointer=">",
                        qmark="",
                        style=INQUIRER_STYLE
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
                                qmark="",
                                style=INQUIRER_STYLE
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
                            new_val = inquirer.select(message="Edit Efficiency Timeframe >", choices=[Choice("1H", name="1H"), Choice("4H", name="4H")], pointer=">", qmark="", style=INQUIRER_STYLE).execute()
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

def flow_pending_audits(preselected_trade_id: str = None, preselected_payload: dict = None, preselected_choice: str = None, state_rule: str = "promote", force_new_tactical: bool = False):
    # preselected_* lets a caller (e.g. the post-save prompt in flow_new_analysis(),
    # or flow_add_tactical_execution()) drop straight into the eff/tac wizard below
    # for a specific trade_id, bypassing the PENDING_AUDITS-only picker. state_rule
    # controls how the record's state is finalized: "promote" keeps the original
    # eff+tac-complete -> READY_FOR_NOTION rule; "preserve" leaves whatever state
    # the record already has untouched (used when adding an execution to an
    # analysis that's already READY_FOR_NOTION/SYNCED/COMPLETED). force_new_tactical
    # forces a brand-new tactical_audit row instead of resuming the most recent draft.
    if preselected_trade_id is not None:
        trade_id = preselected_trade_id
        payload = preselected_payload or {}
    else:
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

        record_num = get_mandatory_text("Enter Record # to audit (or 'c' to cancel)")
        if record_num.strip().lower() == 'c':
            return

        try:
            record_idx = int(record_num.strip())
            if record_idx < 1 or record_idx > len(records):
                raise ValueError
        except ValueError:
            console.print("[red]Invalid record #.[/red]")
            input("Press Enter to continue...")
            return

        target_record = records[record_idx - 1]

        trade_id = target_record["id"]
        payload = target_record["payload"]

    existing_tactical_row_id = None if force_new_tactical else payload.get("audit_tactical", {}).get("id")

    if preselected_choice is not None:
        audit_choice = preselected_choice
    else:
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

        evp_val = payload.get("efficiency", {}).get("Edge_Validation_Price")
        si_val = payload.get("efficiency", {}).get("Structural_Invalidation")
        mark_price_val = payload.get("efficiency", {}).get("Mark_Price")
        evp_display = f"{evp_val:.2f}" if evp_val is not None else "N/A"
        si_display = f"{si_val:.2f}" if si_val is not None else "N/A"
        mark_price_display = f"{mark_price_val:.2f}" if mark_price_val is not None else "N/A"
        console.print(f"[bold green]Edge Validation Price:[/bold green] {evp_display}   [bold red]Structural Invalidation:[/bold red] {si_display}   [bold cyan]Mark Price:[/bold cyan] {mark_price_display}")

        edge_desc_val = payload.get("edge_description")
        if edge_desc_val:
            console.print(f"[bold cyan]Edge Description:[/bold cyan] {edge_desc_val}")
        
        try:
            bias_a = StructuralBias(bias_a_val)
        except ValueError:
            bias_a = StructuralBias.NO_BIAS_CHOPPY

        # Spec 002 (T41): la propuesta de las velas se pide una sola vez, antes de los prompts; si no la hay, una línea
        # con el motivo y los prompts quedan como siempre.
        proposal = efficiency_proposal(trade_id)
        status_line = proposal_status_line(proposal)
        if status_line:
            console.print(status_line, style="yellow", markup=False, highlight=False, soft_wrap=True)

        # T42 (RF-7, RF-8): lo que propusieron las velas va como default `(auto)` de cada prompt; el operador lo acepta con
        # Enter o lo corrige (RF-7e). Real Bias B y la lección siguen siendo solo del operador.
        auto = proposal  # cada campo es None cuando las velas no lo proponen (open, pending, sin toque...)

        def optional_price(label, value):
            message = f"{label} [Optional] >"
            extra = {}
            if value is not None:
                extra["default"] = _plain_number(float(value))
                message = f"{label} (auto: {extra['default']}) [Optional] >"
            return bind_pause(inquirer.text(message=message, style=INQUIRER_STYLE, **extra)).execute()

        session = AuditSession(trade_id, "eff")
        while True:
            try:
                real_bias_b = session.prompt("real_bias_b", get_enum_choice, "Real Bias B", StructuralBias)
                res_type = session.prompt("res_type", get_enum_choice, "Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN],
                                          **auto_default(auto and auto.resolution_type))
                struct_res = session.prompt("struct_res", get_enum_choice, "Structural Resolution", StructuralResolution,
                                            **auto_default(auto and auto.structural_resolution))
                fail_reason = session.prompt("fail_reason", get_enum_choice, "Failure Reason", FailureReason,
                                             **auto_default(auto and auto.failure_reason))
                lesson_eff = session.prompt("lesson_eff", get_optional_text, "Efficiency Lesson Learned")

                structural_mae_raw = session.prompt("structural_mae_raw", lambda: optional_price(
                    "Structural MAE (peor precio alcanzado en contra de la tesis)", auto and auto.structural_mae))
                structural_mfe_raw = session.prompt("structural_mfe_raw", lambda: optional_price(
                    "Structural MFE (mejor precio alcanzado a favor de la tesis)", auto and auto.structural_mfe))
                # T43 (RF-7c, N1): la hora del primer toque, al final y opcional; la del guardado va aparte (RF-14).
                resolution_time_val = resolution_time_entry(session.prompt(
                    "resolution_time", get_optional_datetime, "Resolution Time",
                    precision=touch_precision(auto), **auto_default(auto and auto.resolution_time)))
                try:
                    structural_mae_val = Decimal(str(structural_mae_raw)) if structural_mae_raw.strip() else None
                    structural_mfe_val = Decimal(str(structural_mfe_raw)) if structural_mfe_raw.strip() else None
                except Exception:
                    console.print("[bold red]Invalid decimal input for Structural MAE/MFE. Setting to None.[/bold red]")
                    structural_mae_val = None
                    structural_mfe_val = None

                audit_eff = EfficiencyAudit(
                    efficiency_id=trade_id,
                    bias_a=bias_a,
                    resolution_type=res_type,
                    real_bias_b=real_bias_b,
                    structural_resolution=struct_res,
                    failure_reason=fail_reason,
                    resolution_time=resolution_time_val,
                    resolution_time_source=resolution_time_source(resolution_time_val, auto),
                    lesson_learned=lesson_eff,
                    structural_mae=structural_mae_val,
                    structural_mfe=structural_mfe_val
                )

                # Review Panel. T44: "(auto)" marca lo que sigue siendo la propuesta de las velas.
                res_type_mark = auto_mark(res_type, auto and auto.resolution_type)
                struct_res_mark = auto_mark(struct_res, auto and auto.structural_resolution)
                fail_reason_mark = auto_mark(fail_reason, auto and auto.failure_reason)
                mae_mark = auto_mark(structural_mae_val, auto and auto.structural_mae)
                mfe_mark = auto_mark(structural_mfe_val, auto and auto.structural_mfe)
                resolution_time_text = resolution_time_display(resolution_time_val, audit_eff.resolution_time_source)
                rev_text = Text()
                rev_text.append(f"Original Bias (Bias A): {bias_a.value if hasattr(bias_a, 'value') else bias_a}\n")
                rev_text.append(f"Real Bias B: {real_bias_b.value if hasattr(real_bias_b, 'value') else real_bias_b}\n")
                rev_text.append(f"Efficiency Timeframe: {eff_tf_val}\n")
                rev_text.append(f"Resolution Type: {res_type.value if hasattr(res_type, 'value') else res_type}{res_type_mark}\n")
                rev_text.append(f"Structural Resolution: {struct_res.value if hasattr(struct_res, 'value') else struct_res}{struct_res_mark}\n")
                rev_text.append(f"Failure Reason: {fail_reason.value if hasattr(fail_reason, 'value') else fail_reason}{fail_reason_mark}\n")
                rev_text.append(f"Lesson Learned: {lesson_eff or ''}\n")
                rev_text.append(f"Structural MAE: {structural_mae_val if structural_mae_val is not None else 'N/A'}{mae_mark}\n")
                rev_text.append(f"Structural MFE: {structural_mfe_val if structural_mfe_val is not None else 'N/A'}{mfe_mark}\n")
                rev_text.append(f"Resolution Time: {resolution_time_text}\n")

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
                    new_payload["audit_efficiency"]["audit_registration_time"] = _now_gt()  # RF-14
                    console.print("[green]Efficiency Audit saved.[/green]")
                    session.clear_state()
                    break
                elif action_choice == "discard":
                    raise PauseAuditException("Discard requested")
                elif action_choice == "edit":
                    display_bias_b = real_bias_b.value if hasattr(real_bias_b, 'value') else real_bias_b
                    edit_choices = [
                        Choice("real_bias_b", name=f"Real Bias B: {display_bias_b}"),
                        Choice("res_type", name=f"Resolution Type: {res_type.value}{res_type_mark}"),
                        Choice("struct_res", name=f"Structural Resolution: {struct_res.value}{struct_res_mark}"),
                        Choice("fail_reason", name=f"Failure Reason: {fail_reason.value}{fail_reason_mark}"),
                        Choice("lesson_eff", name=f"Lesson Learned: {lesson_eff or ''}"),
                        Choice("structural_mae_raw", name=f"Structural MAE: {structural_mae_val if structural_mae_val is not None else 'N/A'}{mae_mark}"),
                        Choice("structural_mfe_raw", name=f"Structural MFE: {structural_mfe_val if structural_mfe_val is not None else 'N/A'}{mfe_mark}"),
                        Choice("resolution_time", name=f"Resolution Time: {resolution_time_text}"),
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
                        session.state["res_type"] = get_enum_choice("Edit Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN],
                                                                    **auto_default(auto and auto.resolution_type))
                    elif field_to_edit == "struct_res":
                        session.state["struct_res"] = get_enum_choice("Edit Structural Resolution", StructuralResolution,
                                                                      **auto_default(auto and auto.structural_resolution))
                    elif field_to_edit == "fail_reason":
                        session.state["fail_reason"] = get_enum_choice("Edit Failure Reason", FailureReason,
                                                                       **auto_default(auto and auto.failure_reason))
                    elif field_to_edit == "lesson_eff":
                        session.state["lesson_eff"] = get_optional_text("Edit Efficiency Lesson Learned")
                    elif field_to_edit == "structural_mae_raw":
                        session.state["structural_mae_raw"] = optional_price(
                            "Structural MAE (peor precio alcanzado en contra de la tesis)", auto and auto.structural_mae)
                    elif field_to_edit == "structural_mfe_raw":
                        session.state["structural_mfe_raw"] = optional_price(
                            "Structural MFE (mejor precio alcanzado a favor de la tesis)", auto and auto.structural_mfe)
                    elif field_to_edit == "resolution_time":
                        session.state["resolution_time"] = get_optional_datetime(
                            "Edit Resolution Time", precision=touch_precision(auto),
                            **auto_default(auto and auto.resolution_time))
            except RestartFlowException:
                continue
            except PauseAuditException:
                console.print("\n[bold yellow]Audit Paused. Progress saved.[/bold yellow]")
                input("Press Enter to continue...")
                return

    elif audit_choice == "tac":
        console.print(Panel("Tactical Audit", style="bold magenta"))
        market_bias_val = payload.get("efficiency", {}).get("Market_Bias", "Unknown")
        console.print(f"[bold cyan]Original Market Bias:[/bold cyan] {market_bias_val}")
        session = AuditSession(trade_id, f"tac:{existing_tactical_row_id or 'new'}")
        tactical_proposals = {}  # T45, T46: propuestas de las velas por datos de la orden, para no recalcular
        while True:
            tac_auto = tp_check = excursion = None  # existen aunque esta vuelta no pase por la orden llenada
            try:
                # --- Bifurcación temprana: ¿hubo trade esta sesión? (agnóstica al bias) ---
                def ask_tac_mode():
                    return bind_pause(inquirer.select(
                        message="¿Se evaluó/tomó algún trade en esta sesión?",
                        choices=[
                            Choice("gates", name="Sí — Evaluar Gates (Motor B)"),
                            Choice("no_trade", name="No — Ningún trade tomado (Skip)")
                        ],
                        pointer=">",
                        qmark=""
                    )).execute()

                tac_mode = session.prompt("tac_mode", ask_tac_mode)

                if tac_mode == "no_trade":
                    # --- Camino corto: ningún setup fue evaluado esta sesión ---
                    def ask_no_trade_skip_reason():
                        ordered_choices = [SkipReason.SIN_SETUP_IDENTIFICADO] + [
                            c for c in SkipReason if c not in (SkipReason.SKIP, SkipReason.SIN_SETUP_IDENTIFICADO)
                        ]
                        return bind_pause(inquirer.select(
                            message="Skip Reason? >",
                            choices=ordered_choices,
                            pointer=">",
                            qmark=""
                        )).execute()

                    while True:
                        skip_reason = session.prompt("no_trade_skip_reason", ask_no_trade_skip_reason)
                        lesson_tact = session.prompt("no_trade_lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                        visual_path = session.prompt("no_trade_visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))

                        audit_tactical = TacticalAudit(
                            tactical_id=trade_id,
                            tactical_row_id=existing_tactical_row_id,
                            asset=payload.get("asset"),
                            order_filled=False,
                            skip_reason=skip_reason,
                            tier_setup=TierSetup.SKIP,
                            lesson_learned=lesson_tact,
                            visual_lesson_path=visual_path,
                            stop_loss=0.0, entry_price=0.0, size=0.0, take_profit=0.0, cost=0.0, mae=0.0, mfe=0.0,
                        )

                        rev_text = Text()
                        rev_text.append("Order Filled: No (Ningún trade tomado)\n")
                        rev_text.append(f"Skip Reason: {skip_reason.value if hasattr(skip_reason, 'value') else skip_reason}\n", style="yellow")
                        rev_text.append(f"Market Bias (context): {market_bias_val}\n")
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
                            at_dump = audit_tactical.model_dump()
                            string_enum_keys = {
                                "tier_setup", "market_state", "session", "exit_type",
                                "followed_plan", "primary_emotion", "setup_type",
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
                                Choice("no_trade_skip_reason", name=f"Skip Reason: {skip_reason.value if hasattr(skip_reason, 'value') else skip_reason}"),
                                Choice("no_trade_lesson_tact", name=f"Lesson: {lesson_tact[:30]}..."),
                                Choice("no_trade_visual_lesson_path", name=f"Visual Lesson Path: {session.state.get('no_trade_visual_lesson_path', 'nan')}"),
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
                            if field_to_edit == "no_trade_skip_reason":
                                session.state["no_trade_skip_reason"] = get_enum_choice("Edit Skip Reason", SkipReason)
                            elif field_to_edit == "no_trade_lesson_tact":
                                session.state["no_trade_lesson_tact"] = get_mandatory_text("Edit Tactical Lesson Learned", multiline=True)
                            elif field_to_edit == "no_trade_visual_lesson_path":
                                visual_path = handle_visual_lesson_assignment(trade_id, payload.get("asset", "Unknown"), session.state.get("no_trade_visual_lesson_path", "nan"))
                                session.state["no_trade_visual_lesson_path"] = visual_path
                    break

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

                # --- BLOQUE 1: Motor B — Gates + HTF/LTF Trend (siempre primero) ---
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

                htf_trend = session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                ltf_trend = session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)

                abort_trade = False
                if gates_failed_cnt > 0:
                    gate_action = session.prompt("gate_action", lambda: bind_pause(inquirer.select(
                        message="[GATES FAILED] Operación inválida estructuralmente:",
                        choices=["Abortar Trade", "Forzar Entrada (Revenge)"]
                    )).execute())
                    if gate_action == "Abortar Trade":
                        abort_trade = True

                def ask_could_hit_tp(default=None):
                    # T46 (RF-10): con propuesta de las velas, "yes"/"no" queda preseleccionado y marcado (auto).
                    return bind_pause(inquirer.select(
                        message="Could hit TP? >" if default is None else f"Could hit TP? (auto: {default}) >",
                        choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                        pointer=">",
                        qmark="",
                        keybindings={"skip": []},
                        **auto_default(default)
                    )).execute()

                def ask_order_filled():
                    return bind_pause(inquirer.select(
                        message="Order Filled? >",
                        choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                        pointer=">",
                        qmark=""
                    )).execute() == "yes"

                def ask_skip_reason():
                    return bind_pause(inquirer.select(
                        message="Skip Reason? >",
                        choices=[c for c in SkipReason if c != SkipReason.SKIP],
                        pointer=">",
                        qmark=""
                    )).execute()

                if abort_trade:
                    # --- Camino corto: nunca se intentó la entrada ---
                    while True:
                        skip_reason = session.prompt("skip_reason", ask_skip_reason)
                        lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                        visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))

                        audit_tactical = TacticalAudit(
                            tactical_id=trade_id,
                            tactical_row_id=existing_tactical_row_id,
                            asset=payload.get("asset"),
                            order_filled=False,
                            skip_reason=skip_reason,
                            htf_trend_context=htf_trend,
                            ltf_trend_context=ltf_trend,
                            lesson_learned=lesson_tact,
                            visual_lesson_path=visual_path,
                            gates_failed=gates_failed_cnt,
                            confirmations_count=0,
                            stop_loss=0.0, entry_price=0.0, size=0.0, take_profit=0.0, cost=0.0, mae=0.0, mfe=0.0,
                            g1_trend_15m=g1_trend_15m, g2_fractal_trend=g2_fractal_trend, g3_limit_order=g3_limit_order,
                            g4_breathing=g4_breathing, g5_manual_cooldown=g5_manual_cooldown,
                            g6_sl_validated=g6_sl_validated, g7_tp_validated=g7_tp_validated,
                        )

                        rev_text = Text()
                        rev_text.append("Order Filled: No (Abortado por Gates)\n")
                        rev_text.append(f"Skip Reason: {skip_reason.value if hasattr(skip_reason, 'value') else skip_reason}\n", style="yellow")
                        rev_text.append(f"Gates Failed: {gates_failed_cnt}\n", style="red")
                        rev_text.append(f"HTF Trend Context: {htf_trend.value if isinstance(htf_trend, Enum) else htf_trend}\n")
                        rev_text.append(f"LTF Trend Context: {ltf_trend.value if isinstance(ltf_trend, Enum) else ltf_trend}\n")
                        rev_text.append(f"Tactical Lesson Learned: {lesson_tact}\n")
                        if visual_path and visual_path != "nan":
                            rev_text.append(f"Visual Lesson: {visual_path}\n")

                        try:
                            console.clear(home=True)
                        except TypeError:
                            console.clear()
                        console.print(Panel(rev_text, title="Review: Tactical Audit (Aborted by Gates)", border_style="cyan"))

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
                            at_dump = audit_tactical.model_dump()
                            string_enum_keys = {
                                "tier_setup", "market_state", "session", "exit_type",
                                "followed_plan", "primary_emotion", "setup_type",
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
                                Choice("skip_reason", name=f"Skip Reason: {skip_reason.value if hasattr(skip_reason, 'value') else skip_reason}"),
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
                            if field_to_edit == "skip_reason":
                                session.state["skip_reason"] = get_enum_choice("Edit Skip Reason", SkipReason)
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
                    # --- Camino principal: se intenta la entrada (gates limpios o forzada) ---
                    while True:
                        # BLOQUE 2: Confirmations (Motor B 2ª mitad) + tier_setup
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

                        # --- TIER GATE: display + bloqueo duro D/F (absoluto, sin override) ---
                        tier_gate_error = None
                        try:
                            tier_display_style = {
                                TierSetup.A: "success", TierSetup.B: "success",
                                TierSetup.C: "warning",
                                TierSetup.D: "danger", TierSetup.F: "danger",
                            }.get(tier_setup)
                            if tier_display_style is None:
                                raise ValueError(f"Tier Setup fuera del set esperado A-F: {tier_setup!r}")
                            console.print(f"[{tier_display_style}]Tier Setup: {tier_setup.value}[/{tier_display_style}]")
                            tier_gate_blocked = tier_setup in (TierSetup.D, TierSetup.F)
                        except Exception as _tier_exc:
                            tier_gate_error = str(_tier_exc)
                            tier_gate_blocked = True

                        if tier_gate_blocked:
                            # --- Camino corto: Tier D/F (o fallo de cálculo) — nunca se llega a BLOQUE 3 ---
                            if tier_gate_error:
                                console.print(f"[bold red]ERROR: No se pudo determinar el Tier Setup: {tier_gate_error}[/bold red]")
                            else:
                                console.print(Panel(
                                    f"[bold red]TIER {tier_setup.value} — Gate duro activado.[/bold red]\n"
                                    "Setup de baja calidad estructural: el wizard NO pedirá datos de entrada de orden.",
                                    border_style="red"
                                ))
                            while True:
                                lesson_tact = session.prompt("tier_gate_lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                                visual_path = session.prompt("tier_gate_visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))

                                audit_tactical = TacticalAudit(
                                    tactical_id=trade_id,
                                    tactical_row_id=existing_tactical_row_id,
                                    asset=payload.get("asset"),
                                    order_filled=False,
                                    skip_reason=SkipReason.INVALIDADA_ANTES_DE_LLENAR,
                                    tier_setup=tier_setup if not tier_gate_error else None,
                                    htf_trend_context=htf_trend,
                                    ltf_trend_context=ltf_trend,
                                    lesson_learned=lesson_tact,
                                    visual_lesson_path=visual_path,
                                    gates_failed=gates_failed_cnt,
                                    confirmations_count=confirmations_count,
                                    mfe_potencial_estimado=mfe_potencial,
                                    stop_loss=0.0, entry_price=0.0, size=0.0, take_profit=0.0, cost=0.0, mae=0.0, mfe=0.0,
                                    g1_trend_15m=g1_trend_15m, g2_fractal_trend=g2_fractal_trend, g3_limit_order=g3_limit_order,
                                    g4_breathing=g4_breathing, g5_manual_cooldown=g5_manual_cooldown,
                                    g6_sl_validated=g6_sl_validated, g7_tp_validated=g7_tp_validated,
                                    c1_kl_support=c1_kl_support, c2_fractal_std=c2_fractal_std, c3_fractal_1m=c3_fractal_1m,
                                    c4_fractal_1h=c4_fractal_1h, c5_kl_target=c5_kl_target, c6_liquidity=c6_liquidity,
                                    c7_retracement=c7_retracement, c8_convergence_15m=c8_convergence_15m,
                                )

                                rev_text = Text()
                                rev_text.append("Order Filled: No (Bloqueado por Tier Gate)\n")
                                if tier_gate_error:
                                    rev_text.append(f"Tier Setup: ERROR ({tier_gate_error})\n", style="bold red")
                                else:
                                    rev_text.append(f"Tier Setup: {tier_setup.value}\n", style="bold red")
                                rev_text.append(f"Gates Failed: {gates_failed_cnt}\n", style="red" if gates_failed_cnt > 0 else "green")
                                rev_text.append(f"Confirmations Count: {confirmations_count}\n", style="white")
                                rev_text.append(f"HTF Trend Context: {htf_trend.value if isinstance(htf_trend, Enum) else htf_trend}\n")
                                rev_text.append(f"LTF Trend Context: {ltf_trend.value if isinstance(ltf_trend, Enum) else ltf_trend}\n")
                                rev_text.append(f"Tactical Lesson Learned: {lesson_tact}\n")
                                if visual_path and visual_path != "nan":
                                    rev_text.append(f"Visual Lesson: {visual_path}\n")

                                try:
                                    console.clear(home=True)
                                except TypeError:
                                    console.clear()
                                console.print(Panel(rev_text, title="Review: Tactical Audit (Blocked by Tier Gate D/F)", border_style="red"))

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
                                    at_dump = audit_tactical.model_dump()
                                    string_enum_keys = {
                                        "tier_setup", "market_state", "session", "exit_type",
                                        "followed_plan", "primary_emotion", "setup_type",
                                        "htf_trend_context", "confirmation_status", "ltf_trend_context",
                                        "pre_trade_emotions", "mid_trade_emotions", "post_trade_emotions",
                                        "could_hit_tp", "lesson_learned", "trade_decision", "trade_duration",
                                        "visual_lesson_path"
                                    }
                                    for k, v in at_dump.items():
                                        if v is None and k in string_enum_keys:
                                            at_dump[k] = "nan"
                                    new_payload["audit_tactical"] = at_dump
                                    console.print("[green]Tactical Audit saved (Blocked by Tier Gate).[/green]")
                                    session.clear_state()
                                    break
                                elif action_choice == "discard":
                                    raise PauseAuditException("Discard requested")
                                elif action_choice == "edit":
                                    edit_choices = [
                                        Choice("tier_gate_htf_trend", name=f"HTF Trend: {htf_trend.value if isinstance(htf_trend, Enum) else htf_trend}"),
                                        Choice("tier_gate_ltf_trend", name=f"LTF Trend: {ltf_trend.value if isinstance(ltf_trend, Enum) else ltf_trend}"),
                                        Choice("tier_gate_lesson_tact", name=f"Lesson: {lesson_tact[:30]}..."),
                                        Choice("tier_gate_visual_lesson_path", name=f"Visual Lesson Path: {session.state.get('tier_gate_visual_lesson_path', 'nan')}"),
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
                                    if field_to_edit == "tier_gate_htf_trend":
                                        session.state["htf_trend"] = get_enum_choice("Edit HTF Trend Context", HTFTrendContext)
                                    elif field_to_edit == "tier_gate_ltf_trend":
                                        session.state["ltf_trend"] = get_enum_choice("Edit LTF Trend Context", TrendContext)
                                    elif field_to_edit == "tier_gate_lesson_tact":
                                        session.state["tier_gate_lesson_tact"] = get_mandatory_text("Edit Tactical Lesson Learned", multiline=True)
                                    elif field_to_edit == "tier_gate_visual_lesson_path":
                                        visual_path = handle_visual_lesson_assignment(trade_id, payload.get("asset", "Unknown"), session.state.get("tier_gate_visual_lesson_path", "nan"))
                                        session.state["tier_gate_visual_lesson_path"] = visual_path
                            break

                        # BLOQUE 3: Datos de entrada
                        sl = session.prompt("sl", get_mandatory_float, "Stop Loss")
                        entry_p = session.prompt("entry_p", get_mandatory_float, "Entry Price")
                        size = session.prompt("size", get_mandatory_float, "Size")
                        tp = session.prompt("tp", get_mandatory_float, "Take Profit")

                        # --- Cuadro de P&L potencial (efímero, NO se persiste en journal.db) ---
                        # Solo (re)aparece si sl/entry_p/size/tp cambiaron desde el último
                        # "Aceptar y continuar" -- evita reaparecer en cada edit del panel
                        # final, que re-atraviesa este while True (:3513). Lista (no tupla)
                        # para que un round-trip JSON de session.state no rompa la comparación.
                        _pnl_inputs = [sl, entry_p, size, tp]
                        if session.state.get("_pnl_box_snapshot") != _pnl_inputs:
                            _pnl_choice = render_pnl_box(payload.get("asset"), entry_p, size, sl, tp)
                            if _pnl_choice == "accept":
                                session.state["_pnl_box_snapshot"] = list(_pnl_inputs)
                            else:
                                session.state.pop(_pnl_choice, None)          # se re-pregunta en :3623-3626
                                session.state.pop("_pnl_box_snapshot", None)
                                continue

                        # --- Stop Deviation Journaling: nunca bloquea, solo audita ---
                        # Recalculado en cada vuelta de este while True (:3569), igual que
                        # el gate emocional (:3801) -- si el operador edita SL/Entry Price
                        # desde el panel de Review, se recalcula fresh sin código adicional.
                        _structural_invalidation = get_unified_structural_invalidation(
                            trade_id, engine=get_active_engine()
                        )
                        _dir = get_dir_val("Long" if entry_p > sl else "Short")
                        if _structural_invalidation is None:
                            stop_slippage_r = None
                        else:
                            _riesgo_teorico = _dir * (entry_p - _structural_invalidation)
                            _riesgo_real = _dir * (entry_p - sl)
                            if _riesgo_teorico == 0:
                                stop_slippage_r = None
                                logging.getLogger(__name__).warning(
                                    "stop_slippage_r sin calcular (trade_id=%s): "
                                    "entry_price == structural_invalidation (%.8f)",
                                    trade_id, entry_p,
                                )
                            else:
                                stop_slippage_r = (_riesgo_teorico - _riesgo_real) / _riesgo_teorico

                        if stop_slippage_r is not None and stop_slippage_r > 0:
                            console.print(Panel(
                                f"[bold yellow]Stop Loss más ajustado que la invalidación estructural "
                                f"(stop_slippage_r={stop_slippage_r:.4f}).[/bold yellow]\n"
                                "Registrá por qué el stop táctico se desvió del nivel estructural "
                                "(no bloquea el avance).",
                                border_style="yellow"
                            ))
                            stop_deviation_reason = session.prompt(
                                "stop_deviation_reason", ask_stop_deviation_reason
                            )
                            stop_deviation_note = session.prompt(
                                "stop_deviation_note", get_optional_text, "Stop Deviation Note"
                            )
                        else:
                            stop_deviation_reason = None
                            stop_deviation_note = None

                        def ask_entry_time():
                            # Año/mes/día se auto-derivan del created_at del unified analysis
                            # de este trade_id; el usuario solo ingresa la hora (HH:MM).
                            created_at = get_unified_created_at(trade_id, engine=get_active_engine())
                            if created_at is None:
                                raise RuntimeError(
                                    f"No se encontró created_at en UnifiedDepartment para "
                                    f"trade_id={trade_id!r}; no se puede auto-derivar la fecha "
                                    "de Entry Time."
                                )
                            entry_hour = get_mandatory_time("Entry Time")
                            return datetime.datetime.combine(created_at.date(), entry_hour)

                        entry_time = session.prompt("entry_time", ask_entry_time)

                        # BLOQUE 4: Estado pre-trade
                        pre_trade_emotions = session.prompt("pre_trade_emotions", get_mandatory_text, "Pre Trade Emotions")
                        emotions = session.prompt("emotions", get_multi_enum_choice, "Emotions", Emotions, choices=ACTIVE_EMOTIONS)
                        p_emotion = session.prompt("p_emotion", get_enum_choice, "Primary Emotion", PrimaryEmotion)
                        mental_clarity = session.prompt("mental_clarity", get_mandatory_int, "Mental Clarity Level", 1, 5)
                        impatience = session.prompt("impatience", get_mandatory_int, "Impatience Level", 1, 5)
                        anxiety = session.prompt("anxiety", get_mandatory_int, "Anxiety Level", 1, 5)

                        # --- Gate emocional: anxiety_level >= 4, override auditable ---
                        # Independiente del gate de Tier D/F (que ya cortó el wizard más
                        # arriba, sin override, si aplicaba) -- ver ARCHITECTURE.md §15.
                        # Recalculado en cada vuelta de este while True (:3569): si el
                        # usuario baja el nivel vía "Edit a Field" antes de guardar, el
                        # override deja de exigirse solo, sin código adicional.
                        if anxiety is not None and anxiety >= ANXIETY_GATE_THRESHOLD:
                            console.print(Panel(
                                f"[bold red]Anxiety Level {anxiety}/5 — gate emocional activado.[/bold red]\n"
                                "Se requiere una justificación explícita para guardar este registro.",
                                border_style="red"
                            ))
                            emotional_override_reason = session.prompt(
                                "emotional_gate_override_reason", get_mandatory_text,
                                "Emotional Gate Override — Justificación obligatoria", multiline=True
                            )
                        else:
                            emotional_override_reason = None

                        market_state = session.prompt("market_state", get_enum_choice, "Market State", MarketState)
                        setup_t = session.prompt("setup_t", get_enum_choice, "Setup Type", SetupType)
                        cost = session.prompt("cost", get_mandatory_float, "Cost (Fees/Funding)")

                        # --- ¿Se llenó la orden? ---
                        order_filled = session.prompt("order_filled", ask_order_filled)

                        if not order_filled:
                            # BLOQUE FINAL (no llenada): skip_reason + lección + visual
                            skip_reason = session.prompt("skip_reason", ask_skip_reason)
                            lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                            visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))

                            audit_tactical = TacticalAudit(
                                tactical_id=trade_id,
                                tactical_row_id=existing_tactical_row_id,
                                asset=payload.get("asset"),
                                order_filled=False,
                                skip_reason=skip_reason,
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
                                confirmation_status=conf_status,
                                tier_setup=tier_setup,
                                market_state=market_state,
                                setup_type=setup_t,
                                cost=cost,
                                lesson_learned=lesson_tact,
                                visual_lesson_path=visual_path,
                                g1_trend_15m=g1_trend_15m, g2_fractal_trend=g2_fractal_trend, g3_limit_order=g3_limit_order,
                                g4_breathing=g4_breathing, g5_manual_cooldown=g5_manual_cooldown,
                                g6_sl_validated=g6_sl_validated, g7_tp_validated=g7_tp_validated,
                                c1_kl_support=c1_kl_support, c2_fractal_std=c2_fractal_std, c3_fractal_1m=c3_fractal_1m,
                                c4_fractal_1h=c4_fractal_1h, c5_kl_target=c5_kl_target, c6_liquidity=c6_liquidity,
                                c7_retracement=c7_retracement, c8_convergence_15m=c8_convergence_15m,
                                gates_failed=gates_failed_cnt,
                                confirmations_count=confirmations_count,
                                mfe_potencial_estimado=mfe_potencial,
                                emotional_gate_override_reason=emotional_override_reason,
                                stop_slippage_r=stop_slippage_r,
                                stop_deviation_reason=stop_deviation_reason,
                                stop_deviation_note=stop_deviation_note,
                            )
                        else:
                            # BLOQUE 5 (llenada): datos post-fill
                            mid_trade_emotions = session.prompt("mid_trade_emotions", get_mandatory_text, "Mid Trade Emotions")
                            post_trade_emotions = session.prompt("post_trade_emotions", get_mandatory_text, "Post Trade Emotions")
                            exit_time = session.prompt("exit_time", get_mandatory_datetime, "Exit Time")
                            exit_type = session.prompt("exit_type", get_enum_choice, "Exit Type", ExitType)
                            close_p = session.prompt("close_p", get_mandatory_float, "Closing Price")
                            # T45, T46: la propuesta de las velas, una vez por cada combinación de datos de la orden.
                            proposal_key = (payload.get("asset"), entry_time, exit_time, entry_p, sl, tp)
                            if proposal_key not in tactical_proposals:
                                tactical_proposals[proposal_key] = tactical_proposal(*proposal_key)
                            tac_auto = tactical_proposals[proposal_key]
                            tp_check = tac_auto.tp if tac_auto is not None else None
                            excursion = tac_auto.excursion if tac_auto is not None else None
                            if "could_hit_tp" not in session.state:
                                line = tactical_reason_line("Could hit TP?", tac_auto, tp_check)
                                if line:
                                    console.print(line, style="yellow", markup=False, highlight=False, soft_wrap=True)
                            could_hit_tp = session.prompt("could_hit_tp", ask_could_hit_tp,
                                                          **auto_default(tp_check and tp_check.answer))
                            f_plan = session.prompt("f_plan", get_enum_choice, "Followed Plan", FollowedPlan)
                            behav_errors = session.prompt("behav_errors", get_multi_enum_choice, "Behavioral Errors", BehavioralErrors)
                            if "mae" not in session.state:
                                line = tactical_reason_line("MAE/MFE", tac_auto, excursion)
                                if line:
                                    console.print(line, style="yellow", markup=False, highlight=False, soft_wrap=True)
                            mae = session.prompt("mae", get_mandatory_float, "MAE (0 <= MAE <= 10)", min_val=0, max_val=10,
                                                 **auto_default(excursion and excursion.mae_r))
                            mfe = session.prompt("mfe", get_mandatory_float, "MFE (0 <= MFE <= 10)", min_val=0, max_val=10,
                                                 **auto_default(excursion and excursion.mfe_r))
                            lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                            visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))

                            audit_tactical = TacticalAudit(
                                tactical_id=trade_id,
                                tactical_row_id=existing_tactical_row_id,
                                asset=payload.get("asset"),
                                order_filled=True,
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
                                tier_setup=tier_setup,
                                market_state=market_state,
                                followed_plan=f_plan,
                                setup_type=setup_t,
                                behavioral_errors=behav_errors,
                                cost=cost,
                                mae=mae,
                                mfe=mfe,
                                lesson_learned=lesson_tact,
                                visual_lesson_path=visual_path,
                                g1_trend_15m=g1_trend_15m, g2_fractal_trend=g2_fractal_trend, g3_limit_order=g3_limit_order,
                                g4_breathing=g4_breathing, g5_manual_cooldown=g5_manual_cooldown,
                                g6_sl_validated=g6_sl_validated, g7_tp_validated=g7_tp_validated,
                                c1_kl_support=c1_kl_support, c2_fractal_std=c2_fractal_std, c3_fractal_1m=c3_fractal_1m,
                                c4_fractal_1h=c4_fractal_1h, c5_kl_target=c5_kl_target, c6_liquidity=c6_liquidity,
                                c7_retracement=c7_retracement, c8_convergence_15m=c8_convergence_15m,
                                gates_failed=gates_failed_cnt,
                                confirmations_count=confirmations_count,
                                mfe_potencial_estimado=mfe_potencial,
                                emotional_gate_override_reason=emotional_override_reason,
                                stop_slippage_r=stop_slippage_r,
                                stop_deviation_reason=stop_deviation_reason,
                                stop_deviation_note=stop_deviation_note,
                            )

                        # --- Review Panel (común a ambos desenlaces) ---
                        ep = audit_tactical.entry_price or 0.0
                        sl_disp = audit_tactical.stop_loss or 0.0
                        tp_disp = audit_tactical.take_profit or 0.0
                        dist_to_sl = abs(ep - sl_disp) / ep if ep != 0.0 else 0.0
                        dist_to_tp = abs(ep - tp_disp) / ep if ep != 0.0 else 0.0

                        rev_text = Text()

                        # --- Core Inputs ---
                        rev_text.append("--- Core Inputs ---\n", style="bold green")
                        rev_text.append(f"Order Filled:         {order_filled}\n", style="white")
                        if not order_filled:
                            rev_text.append(f"Skip Reason:          {skip_reason.value if hasattr(skip_reason, 'value') else skip_reason}\n", style="yellow")
                        rev_text.append(f"Entry Price:          {ep}\n", style="white")
                        rev_text.append(f"Closing Price:        {audit_tactical.closing_price}\n", style="white")
                        rev_text.append(f"Size:                 {audit_tactical.size}\n", style="white")
                        rev_text.append(f"Stop Loss:            {sl_disp}\n", style="white")
                        rev_text.append(f"Take Profit:          {tp_disp}\n", style="white")
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
                        _nsu = audit_tactical.notional_size_usd
                        rev_text.append(f"Notional Size USD:    {f'{_nsu:.2f}' if _nsu is not None else 'N/A (símbolo no verificado)'}\n", style="white")
                        rev_text.append(f"Capital At Risk:      {audit_tactical.capital_at_risk:.2f}\n", style="white")
                        _rusd = audit_tactical.risk_usd
                        rev_text.append(f"Risk USD:             {f'{_rusd:.2f}' if _rusd is not None else 'N/A (símbolo no verificado)'}\n", style="white" if _rusd is not None else "yellow")
                        rev_text.append(f"PnL:                  {audit_tactical.pnl if audit_tactical.pnl is not None else 'N/A'}\n", style="white")
                        rev_text.append(f"PnL and Cost:         {audit_tactical.pnl_and_cost if audit_tactical.pnl_and_cost is not None else 'N/A'}\n", style="white")
                        rev_text.append(f"R:R:                  {audit_tactical.r_r if audit_tactical.r_r is not None else 'N/A'}\n", style="white")
                        rev_text.append(f"R Multiple:           {audit_tactical.r_multiple if audit_tactical.r_multiple is not None else 'N/A'}\n", style="white")
                        rev_text.append(f"Captured MFE:         {audit_tactical.captured_mfe if audit_tactical.captured_mfe is not None else 'N/A'}\n\n", style="white")

                        # --- Execution Framework Context ---
                        rev_text.append("--- Execution Framework Context ---\n", style="bold magenta")
                        rev_text.append(f"Tier Setup:           {tier_setup.value if hasattr(tier_setup, 'value') else tier_setup}\n", style="white")
                        rev_text.append(f"Setup Type:           {setup_t.value if hasattr(setup_t, 'value') else setup_t}\n", style="white")
                        rev_text.append(f"Market State:         {market_state.value if hasattr(market_state, 'value') else market_state}\n", style="white")
                        rev_text.append(f"HTF Trend Context:    {htf_trend.value if hasattr(htf_trend, 'value') else htf_trend}\n", style="white")
                        rev_text.append(f"LTF Trend Context:    {ltf_trend.value if hasattr(ltf_trend, 'value') else ltf_trend}\n", style="white")
                        rev_text.append(f"Confirmation Status:  {conf_status.value if hasattr(conf_status, 'value') else conf_status}\n", style="white")
                        if order_filled:
                            rev_text.append(f"Followed Plan:        {f_plan.value if hasattr(f_plan, 'value') else f_plan}\n", style="white")

                        # --- Motor B (Gates & Confirmations) ---
                        rev_text.append("--- Motor B (Gates & Confirmations) ---\n", style="bold magenta")
                        rev_text.append(f"Gates Failed:         {gates_failed_cnt}\n", style="red" if gates_failed_cnt > 0 else "green")
                        rev_text.append(f"Confirmations Count:  {confirmations_count}\n", style="white")
                        if mfe_potencial is not None:
                            rev_text.append(f"MFE Potencial (S6):   {mfe_potencial}\n", style="yellow")

                        # --- Psychological & Cognitive Logging ---
                        rev_text.append("--- Psychological & Cognitive Logging ---\n", style="bold blue")
                        rev_text.append(f"Primary Emotion:      {p_emotion.value if hasattr(p_emotion, 'value') else p_emotion}\n", style="white")
                        emotions_str = ", ".join([e.value if hasattr(e, 'value') else str(e) for e in emotions]) if isinstance(emotions, list) else str(emotions)
                        rev_text.append(f"Emotions:             {format_indented_block(emotions_str, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")
                        if order_filled:
                            be_list_clean = [b.value if hasattr(b, 'value') else str(b) for b in behav_errors] if isinstance(behav_errors, list) else []
                            be_str = ", ".join(be_list_clean) if be_list_clean else "N/A"
                            rev_text.append(f"Behav. Errors:        {format_indented_block(be_str, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")

                            detected = compute_detected_patterns({
                                "primary_emotion": p_emotion.value if hasattr(p_emotion, 'value') else p_emotion,
                                "anxiety_level": anxiety,
                                "impatience_level": impatience,
                                "mental_clarity_level": mental_clarity,
                                "behavioral_errors": be_list_clean,
                                "confirmation_status": conf_status.value if hasattr(conf_status, 'value') else conf_status,
                                "gates_failed": gates_failed_cnt,
                                "followed_plan": f_plan.value if hasattr(f_plan, 'value') else f_plan,
                            })
                            rev_text.append("Detected Patterns:\n", style="dim")
                            for label, color in detected:
                                rev_text.append(f"    • {label}\n", style=color)
                        rev_text.append(f"Pre-Trade Emotions:   {format_indented_block(pre_trade_emotions, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")
                        if order_filled:
                            rev_text.append(f"Mid-Trade Emotions:   {format_indented_block(mid_trade_emotions, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n", style="white")
                            rev_text.append(f"Post-Trade Emotions:  {format_indented_block(post_trade_emotions, indent_spaces=22, first_line_flush=True, wrap_width=60)}\n\n", style="white")
                        else:
                            rev_text.append("\n")

                        # --- Internal State Thresholds ---
                        rev_text.append("--- Internal State Thresholds ---\n", style="bold orange1")
                        rev_text.append(f"Anxiety Level:        {anxiety}\n", style="white")
                        rev_text.append(f"Impatience Level:     {impatience}\n", style="white")
                        rev_text.append(f"Mental Clarity Level: {mental_clarity}\n", style="white")
                        if emotional_override_reason:
                            rev_text.append(
                                f"Emotional Gate Override:\n  {format_indented_block(emotional_override_reason, indent_spaces=2, first_line_flush=False, wrap_width=60)}\n",
                                style="bold red"
                            )
                        if stop_slippage_r is not None and stop_slippage_r > 0:
                            rev_text.append(f"Stop Slippage R:      {stop_slippage_r:.4f}\n", style="yellow")
                            rev_text.append(
                                f"Stop Deviation Reason: {stop_deviation_reason.value if stop_deviation_reason else 'N/A'}\n",
                                style="yellow"
                            )
                            if stop_deviation_note:
                                rev_text.append(
                                    f"Stop Deviation Note:\n  {format_indented_block(stop_deviation_note, indent_spaces=2, first_line_flush=False, wrap_width=60)}\n",
                                    style="dim italic"
                                )
                        rev_text.append("\n")

                        # --- Qualitative Notes ---
                        rev_text.append("--- Qualitative Notes ---\n", style="bold cyan")
                        if lesson_tact and lesson_tact != "nan":
                            rev_text.append(f"Lesson Learned:\n  {format_indented_block(lesson_tact, indent_spaces=2, first_line_flush=False, wrap_width=60)}\n", style="dim italic")
                        else:
                            rev_text.append("Lesson Learned:       N/A\n", style="dim italic")

                        v_path = session.state.get("visual_lesson_path", "nan")
                        if v_path and v_path != "nan":
                            rev_text.append(f"Visual Lesson Path: {v_path}\n", style="dim cyan")

                        # --- Red de seguridad: Order Filled=Sí pero entry==exit (tiempo o precio) ---
                        inconsistent_fill = order_filled and (
                            (audit_tactical.entry_time is not None and audit_tactical.exit_time is not None
                             and audit_tactical.entry_time == audit_tactical.exit_time)
                            or
                            (audit_tactical.entry_price is not None and audit_tactical.closing_price is not None
                             and audit_tactical.entry_price == audit_tactical.closing_price)
                        )
                        if inconsistent_fill:
                            rev_text.append(
                                "\n⚠ ADVERTENCIA: entry_time == exit_time y/o entry_price == closing_price, "
                                "pero Order Filled = Sí. ¿Seguro que la orden se llenó?\n",
                                style="bold red"
                            )

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
                            if inconsistent_fill:
                                confirm_fill = bind_pause(inquirer.select(
                                    message="¿Confirmás que la orden SÍ se llenó pese a la advertencia? >",
                                    choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                    pointer=">",
                                    qmark=""
                                )).execute()
                                if confirm_fill == "no":
                                    continue
                            at_dump = audit_tactical.model_dump()
                            if not order_filled:
                                string_enum_keys = {
                                    "tier_setup", "market_state", "session", "exit_type",
                                    "followed_plan", "primary_emotion", "setup_type",
                                    "htf_trend_context", "confirmation_status", "ltf_trend_context",
                                    "pre_trade_emotions", "mid_trade_emotions", "post_trade_emotions",
                                    "could_hit_tp", "lesson_learned", "trade_decision", "trade_duration",
                                    "visual_lesson_path"
                                }
                                for k, v in at_dump.items():
                                    if v is None and k in string_enum_keys:
                                        at_dump[k] = "nan"
                            new_payload["audit_tactical"] = at_dump
                            console.print("[green]Tactical Audit saved.[/green]")
                            session.clear_state()
                            break
                        elif action_choice == "discard":
                            raise PauseAuditException("Discard requested")
                        elif action_choice == "edit":
                            edit_choices = [
                                Choice("htf_trend", name=f"HTF Trend: {htf_trend.value if hasattr(htf_trend, 'value') else htf_trend}"),
                                Choice("ltf_trend", name=f"LTF Trend: {ltf_trend.value if hasattr(ltf_trend, 'value') else ltf_trend}"),
                                Choice("sl", name=f"Stop Loss: {sl}"),
                                Choice("entry_p", name=f"Entry Price: {entry_p}"),
                                Choice("size", name=f"Size: {size}"),
                                Choice("tp", name=f"Take Profit: {tp}"),
                                Choice("entry_time", name=f"Entry Time: {entry_time}"),
                                Choice("conf_status", name=f"Confirmation Status: {conf_status.value if hasattr(conf_status, 'value') else conf_status}"),
                                Choice("tier_setup", name=f"Tier Setup: {tier_setup.value if hasattr(tier_setup, 'value') else tier_setup}"),
                                Choice("market_state", name=f"Market State: {market_state.value if hasattr(market_state, 'value') else market_state}"),
                                Choice("setup_t", name=f"Setup Type: {setup_t.value if hasattr(setup_t, 'value') else setup_t}"),
                                Choice("cost", name=f"Cost: {session.state.get('cost', 0.0)}"),
                                Choice("primary_emotion", name=f"Primary Emotion: {p_emotion.value if hasattr(p_emotion, 'value') else p_emotion}"),
                                Choice("emotions", name=f"Emotions: {len(emotions) if isinstance(emotions, list) else 0} chosen"),
                                Choice("anxiety", name=f"Anxiety Level: {anxiety}"),
                                Choice("impatience", name=f"Impatience Level: {impatience}"),
                                Choice("mental_clarity", name=f"Mental Clarity Level: {mental_clarity}"),
                                Choice("pre_trade_emotions", name=f"Pre Trade Emotions: {pre_trade_emotions[:25] if pre_trade_emotions else 'N/A'}..."),
                            ]
                            if order_filled:
                                edit_choices += [
                                    Choice("exit_time", name=f"Exit Time: {exit_time}"),
                                    Choice("exit_type", name=f"Exit Type: {exit_type.value if hasattr(exit_type, 'value') else exit_type}"),
                                    Choice("close_p", name=f"Closing Price: {close_p}"),
                                    Choice("could_hit_tp", name=f"Could hit TP: {could_hit_tp}"),
                                    Choice("f_plan", name=f"Followed Plan: {f_plan.value if hasattr(f_plan, 'value') else f_plan}"),
                                    Choice("behav_errors", name=f"Behavioral Errors: {len(behav_errors) if isinstance(behav_errors, list) else 0} chosen"),
                                    Choice("mae", name=f"MAE: {mae}"),
                                    Choice("mfe", name=f"MFE: {mfe}"),
                                    Choice("mid_trade_emotions", name=f"Mid Trade Emotions: {mid_trade_emotions[:25] if mid_trade_emotions else 'N/A'}..."),
                                    Choice("post_trade_emotions", name=f"Post Trade Emotions: {post_trade_emotions[:25] if post_trade_emotions else 'N/A'}..."),
                                ]
                            else:
                                edit_choices += [
                                    Choice("skip_reason", name=f"Skip Reason: {skip_reason.value if hasattr(skip_reason, 'value') else skip_reason}"),
                                ]
                            edit_choices += [
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
                            if field_to_edit == "skip_reason":
                                session.state["skip_reason"] = get_enum_choice("Edit Skip Reason", SkipReason)
                            elif field_to_edit == "htf_trend":
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
                            elif field_to_edit == "conf_status":
                                session.state["conf_status"] = get_enum_choice("Edit Confirmation Status", ConfirmationStatus)
                            elif field_to_edit == "tier_setup":
                                session.state["tier_setup"] = get_enum_choice("Edit Tier Setup", TierSetup)
                            elif field_to_edit == "market_state":
                                session.state["market_state"] = get_enum_choice("Edit Market State", MarketState)
                            elif field_to_edit == "setup_t":
                                session.state["setup_t"] = get_enum_choice("Edit Setup Type", SetupType)
                            elif field_to_edit == "cost":
                                session.state["cost"] = get_mandatory_float("Edit Cost")
                            elif field_to_edit == "primary_emotion":
                                session.state["p_emotion"] = get_enum_choice("Edit Primary Emotion", PrimaryEmotion)
                            elif field_to_edit == "emotions":
                                session.state["emotions"] = get_multi_enum_choice("Edit Emotions", Emotions, choices=ACTIVE_EMOTIONS, preselected=session.state.get("emotions"))
                            elif field_to_edit == "anxiety":
                                session.state["anxiety"] = get_mandatory_int("Edit Anxiety Level (1 to 5)", 1, 5)
                            elif field_to_edit == "impatience":
                                session.state["impatience"] = get_mandatory_int("Edit Impatience Level (1 to 5)", 1, 5)
                            elif field_to_edit == "mental_clarity":
                                session.state["mental_clarity"] = get_mandatory_int("Edit Mental Clarity Level (1 to 5)", 1, 5)
                            elif field_to_edit == "pre_trade_emotions":
                                session.state["pre_trade_emotions"] = get_mandatory_text("Edit Pre Trade Emotions")
                            elif field_to_edit == "exit_time":
                                session.state["exit_time"] = get_mandatory_datetime("Edit Exit Time")
                            elif field_to_edit == "exit_type":
                                session.state["exit_type"] = get_enum_choice("Edit Exit Type", ExitType)
                            elif field_to_edit == "close_p":
                                session.state["close_p"] = get_mandatory_float("Edit Closing Price")
                            elif field_to_edit == "could_hit_tp":
                                session.state["could_hit_tp"] = ask_could_hit_tp(**auto_default(tp_check and tp_check.answer))
                            elif field_to_edit == "f_plan":
                                session.state["f_plan"] = get_enum_choice("Edit Followed Plan", FollowedPlan)
                            elif field_to_edit == "behav_errors":
                                session.state["behav_errors"] = get_multi_enum_choice("Edit Behavioral Errors", BehavioralErrors)
                            elif field_to_edit == "mae":
                                session.state["mae"] = get_mandatory_float("Edit MAE (0 <= MAE <= 10)", min_val=0, max_val=10,
                                                                           **auto_default(excursion and excursion.mae_r))
                            elif field_to_edit == "mfe":
                                session.state["mfe"] = get_mandatory_float("Edit MFE (0 <= MFE <= 10)", min_val=0, max_val=10,
                                                                           **auto_default(excursion and excursion.mfe_r))
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
    if state_rule == "preserve":
        # Adding an execution to an analysis that's already past PENDING_AUDITS
        # (READY_FOR_NOTION / SYNCED / COMPLETED) must not downgrade its state --
        # the new tactical_audit row gets picked up by Notion sync independently.
        from tools.database import UnifiedDepartment
        from sqlalchemy.orm import Session
        with Session(get_active_engine()) as _state_session:
            _current = _state_session.get(UnifiedDepartment, trade_id)
            final_state = LifecycleState(_current.state) if _current else LifecycleState.PENDING_AUDITS
        console.print(f"[bold cyan]Tactical execution added. Record stays in {final_state.value}.[/bold cyan]")
    else:
        has_eff_final = new_payload.get("audit_efficiency", {}).get("real_bias_b") is not None
        has_tac_final = "audit_tactical" in new_payload
        if has_eff_final and has_tac_final:
            final_state = LifecycleState.READY_FOR_NOTION
            console.print("[bold cyan]Both audits complete! Record transitioning to READY_FOR_NOTION.[/bold cyan]")
        else:
            final_state = LifecycleState.PENDING_AUDITS
            console.print("[yellow]Record remains in PENDING_AUDITS until both components are complete.[/yellow]")

    update_record_state(trade_id, final_state, append_payload=new_payload, tactical_audit_id=existing_tactical_row_id, engine=get_active_engine())
    input("Press Enter to continue...")

def flow_add_tactical_execution():
    """Add a new tactical_audit execution to ANY analysis, regardless of its
    current state -- covers scaling into the same setup, or retrying it on a
    later day, after the analysis already reached READY_FOR_NOTION/SYNCED."""
    from tools.database import UnifiedDepartment
    from sqlalchemy.orm import Session
    from sqlalchemy import select

    with Session(get_active_engine()) as db_session:
        stmt = select(UnifiedDepartment).order_by(UnifiedDepartment.created_at.desc())
        records = db_session.scalars(stmt).all()

        if not records:
            console.print("[yellow]No analyses logged in the database yet.[/yellow]")
            input("Press Enter to continue...")
            return

        try:
            console.clear(home=True)
        except TypeError:
            console.clear()

        console.rule("[bold cyan]Add Tactical Audit to an Existing Analysis[/bold cyan]")
        console.print()

        BIAS_SHORTHANDS = {"Choppy / Neutral": "Choppy"}

        table = Table(box=box.ROUNDED, border_style="magenta", expand=False)
        table.add_column("#", justify="center", width=4)
        table.add_column("Short ID", justify="center", style="cyan", width=14)
        table.add_column("Asset", justify="center", width=12)
        table.add_column("Market Bias", justify="center", width=13, no_wrap=True)
        table.add_column("State", justify="center", width=16)
        table.add_column("Created At", justify="center", style="dim cyan", width=16)
        table.add_column("Executions", justify="center", width=10)

        for idx, r in enumerate(records):
            bias_display = BIAS_SHORTHANDS.get(r.market_bias, r.market_bias or "N/A")
            table.add_row(
                str(idx + 1), r.id[:8], r.asset, bias_display,
                r.state, to_local_display(r.created_at), str(len(r.tactical_audits))
            )
        console.print(table)
        console.print()

        record_num = get_mandatory_text("Enter Record # to add a Tactical Audit to (or 'c' to cancel)")
        if record_num.strip().lower() == 'c':
            return

        try:
            record_idx = int(record_num.strip())
            if record_idx < 1 or record_idx > len(records):
                raise ValueError
        except ValueError:
            console.print("[red]Invalid record #.[/red]")
            input("Press Enter to continue...")
            return

        record = records[record_idx - 1]
        preselected_payload = {
            "asset": record.asset,
            "efficiency": {"Market_Bias": record.market_bias, "Calc_edge": record.calc_edge},
            "tactical": {"tactical_classification": record.tactical_classification, "calc_edge": record.calc_edge},
        }
        trade_id = record.id

    flow_pending_audits(
        preselected_trade_id=trade_id,
        preselected_payload=preselected_payload,
        preselected_choice="tac",
        state_rule="preserve",
        force_new_tactical=True,
    )

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
    ta = max(record.tactical_audits, key=lambda t: t.created_at) if record.tactical_audits else None
    if ta:
        order_filled = workspace.get("order_filled") if (workspace and "order_filled" in workspace) else (ta.order_filled if ta else None)
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
        gf = workspace.get("gates_failed") if (workspace and "gates_failed" in workspace) else (ta.gates_failed if ta else None)

        em_list = workspace.get("emotions") if (workspace and "emotions" in workspace) else (ta.emotions if ta else None)
        be_list = workspace.get("behavioral_errors") if (workspace and "behavioral_errors" in workspace) else (ta.behavioral_errors if ta else None)
        
        pre_emo = workspace.get("pre_trade_emotions") if (workspace and "pre_trade_emotions" in workspace) else (ta.pre_trade_emotions if ta else None)
        mid_emo = workspace.get("mid_trade_emotions") if (workspace and "mid_trade_emotions" in workspace) else (ta.mid_trade_emotions if ta else None)
        post_emo = workspace.get("post_trade_emotions") if (workspace and "post_trade_emotions" in workspace) else (ta.post_trade_emotions if ta else None)

        tact_text.append("Order Filled: ", style="dim")
        tact_text.append(f"{order_filled}\n", style="white")
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
        be_list_clean = [str(b.value if hasattr(b, 'value') else b) for b in be_list] if isinstance(be_list, list) else []
        be_str = ", ".join(be_list_clean) if be_list_clean else "N/A"

        tact_text.append(f"  Conf. Params:         {format_indented_block(conf_str, indent_spaces=24, first_line_flush=True, wrap_width=60)}\n", style="white")
        tact_text.append(f"  Emotions:             {format_indented_block(emotions_str, indent_spaces=24, first_line_flush=True, wrap_width=60)}\n", style="white")
        tact_text.append(f"  Behav. Errors:        {format_indented_block(be_str, indent_spaces=24, first_line_flush=True, wrap_width=60)}\n", style="white")

        detected = compute_detected_patterns({
            "primary_emotion": p_emo.value if hasattr(p_emo, 'value') else p_emo,
            "anxiety_level": anx,
            "impatience_level": imp,
            "mental_clarity_level": clar,
            "behavioral_errors": be_list_clean,
            "confirmation_status": c_status.value if hasattr(c_status, 'value') else c_status,
            "gates_failed": gf,
            "followed_plan": f_plan.value if hasattr(f_plan, 'value') else f_plan,
        })
        tact_text.append("  Detected Patterns:\n", style="dim")
        for label, color in detected:
            tact_text.append(f"    • {label}\n", style=color)

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
                count2 = session.scalar(text("SELECT COUNT(t.id) FROM tactical_audit t JOIN unified_department u ON t.trade_id = u.id WHERE u.asset = :asset"), {"asset": del_target})
                
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

            for idx, r in enumerate(records):
                ts_str = to_local_display(r.created_at)
                bias_val = r.market_bias or "Neutral"
                edge_val = f"{r.calc_edge:.3f}" if r.calc_edge is not None else "N/A"
                
                is_completed = r.efficiency_audit is not None and len(r.tactical_audits) > 0
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
                
            console.print(table)
            console.print()

            record_num = get_mandatory_text("Enter Record # to Inspect and Repair (or 'b' to go back)")
            if record_num.strip().lower() in ("b", "back"):
                return

            try:
                record_idx = int(record_num.strip())
                if record_idx < 1 or record_idx > len(records):
                    raise ValueError
            except ValueError:
                console.print("[red]Invalid record #.[/red]")
                input("Press Enter to continue...")
                continue

            selected_id = records[record_idx - 1].id

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
            
            # An analysis can now have several tactical_audit rows (executions) --
            # pick which one this repair pass targets (or start a brand-new one)
            # before extracting defaults below.
            selected_ta = None
            if len(record.tactical_audits) > 1:
                ta_choices = []
                for t_idx, cand in enumerate(record.tactical_audits):
                    entry_str = to_local_display(cand.entry_time) if cand.entry_time else "sin entry"
                    fill_str = "Filled" if cand.order_filled else "No Fill"
                    ta_choices.append(Choice(cand.id, name=f"[{t_idx+1}] {to_local_display(cand.created_at)} | entry {entry_str} | {fill_str}"))
                ta_choices.append(Choice("__new__", name="[+] Crear una fila nueva de Tactical Audit"))
                chosen_ta_id = inquirer.select(
                    message="Este análisis tiene varias ejecuciones (Tactical Audits) — ¿cuál deseas reparar?",
                    choices=ta_choices,
                    pointer=">",
                    qmark=""
                ).execute()
                if chosen_ta_id != "__new__":
                    selected_ta = next((t for t in record.tactical_audits if t.id == chosen_ta_id), None)
            elif len(record.tactical_audits) == 1:
                selected_ta = record.tactical_audits[0]

            # Initialize defaults extracting attributes directly from related ORM model instances
            ea = getattr(record, "efficiency_audit", None)
            ta = selected_ta

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
                "efficiency_timeframe": ea.efficiency_timeframe if (ea and hasattr(ea, "efficiency_timeframe") and ea.efficiency_timeframe) else "1H",
                "structural_mae": ea.structural_mae if (ea and hasattr(ea, "structural_mae") and ea.structural_mae is not None) else None,
                "structural_mfe": ea.structural_mfe if (ea and hasattr(ea, "structural_mfe") and ea.structural_mfe is not None) else None
            }
            
            ta_defaults = {
                "order_filled": ta.order_filled if (ta and hasattr(ta, "order_filled") and ta.order_filled is not None) else True,
                "skip_reason": ta.skip_reason if (ta and hasattr(ta, "skip_reason") and ta.skip_reason) else None,
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
                "confirmation_params": ta.confirmation_params if (ta and hasattr(ta, "confirmation_params") and ta.confirmation_params) else [],
                # Motor B (Gates)
                "g1_trend_15m": bool(ta.g1_trend_15m) if (ta and hasattr(ta, "g1_trend_15m")) else False,
                "g2_fractal_trend": bool(ta.g2_fractal_trend) if (ta and hasattr(ta, "g2_fractal_trend")) else False,
                "g3_limit_order": bool(ta.g3_limit_order) if (ta and hasattr(ta, "g3_limit_order")) else False,
                "g4_breathing": bool(ta.g4_breathing) if (ta and hasattr(ta, "g4_breathing")) else False,
                "g5_manual_cooldown": bool(ta.g5_manual_cooldown) if (ta and hasattr(ta, "g5_manual_cooldown")) else False,
                "g6_sl_validated": bool(ta.g6_sl_validated) if (ta and hasattr(ta, "g6_sl_validated")) else False,
                "g7_tp_validated": bool(ta.g7_tp_validated) if (ta and hasattr(ta, "g7_tp_validated")) else False,
                # Motor B (Confirmations)
                "c1_kl_support": bool(ta.c1_kl_support) if (ta and hasattr(ta, "c1_kl_support")) else False,
                "c2_fractal_std": bool(ta.c2_fractal_std) if (ta and hasattr(ta, "c2_fractal_std")) else False,
                "c3_fractal_1m": bool(ta.c3_fractal_1m) if (ta and hasattr(ta, "c3_fractal_1m")) else False,
                "c4_fractal_1h": bool(ta.c4_fractal_1h) if (ta and hasattr(ta, "c4_fractal_1h")) else False,
                "c5_kl_target": bool(ta.c5_kl_target) if (ta and hasattr(ta, "c5_kl_target")) else False,
                "c6_liquidity": bool(ta.c6_liquidity) if (ta and hasattr(ta, "c6_liquidity")) else False,
                "c7_retracement": bool(ta.c7_retracement) if (ta and hasattr(ta, "c7_retracement")) else False,
                "c8_convergence_15m": bool(ta.c8_convergence_15m) if (ta and hasattr(ta, "c8_convergence_15m")) else False,
                # Motor B (Metrics)
                "gates_failed": ta.gates_failed if (ta and hasattr(ta, "gates_failed") and ta.gates_failed is not None) else 0,
                "confirmations_count": ta.confirmations_count if (ta and hasattr(ta, "confirmations_count") and ta.confirmations_count is not None) else 0,
                "mfe_potencial_estimado": ta.mfe_potencial_estimado if (ta and hasattr(ta, "mfe_potencial_estimado") and ta.mfe_potencial_estimado is not None) else None
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
                "edge_validation_price": record.edge_validation_price,
                "structural_invalidation": record.structural_invalidation,
                "mark_price": record.mark_price,

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
                        # Multi-Layer Recalculation Pipeline — consolidada con los Sitios 1/2
                        # de flow_new_analysis() (antes "Defect 3": duplicaba la aritmética y
                        # comparaba por string en vez de enum; ambas formas son equivalentes
                        # porque Direction/Strength son str+Enum, confirmado empíricamente
                        # contra un golden-master de 32 casos antes de esta consolidación).
                        x0 = get_dir_val(w["p0_dir"]) * get_str_val(w["p0_str"])
                        x1 = get_dir_val(w["p1_dir"]) * get_str_val(w["p1_str"])
                        x2 = get_dir_val(w["p2_dir"]) * get_str_val(w["p2_str"])
                        x3 = get_dir_val(w["p3_dir"]) * get_str_val(w["p3_str"])
                        x4 = get_dir_val(w["p4_dir"]) * get_str_val(w["p4_str"])

                        val = calculate_edge_score(x0, x1, x2, x3, x4)
                        w["calc_edge"] = val
                        w["market_bias"] = determine_market_bias(val)

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
                                Choice("asset", name=f"⚙️  Asset: {workspace['asset']}"),
                                Separator("── 🔵 P0 · Macro Vector ──"),
                                Choice("p0_thesis", name=f"   P0 Thesis: {_preview(workspace['p0_thesis'])}"),
                                Choice("p0_dir", name=f"   P0 Direction: {_dir_icon(workspace['p0_dir'])} {workspace['p0_dir']}"),
                                Choice("p0_str", name=f"   P0 Strength: {_str_icon(workspace['p0_str'])} {workspace['p0_str']}"),
                                Separator("── 🟣 P1 · Tactical Timing ──"),
                                Choice("p1_thesis", name=f"   P1 Fractals -> Normal: {nf_name} | Inverted: {if_name}"),
                                Choice("p1_dir", name=f"   P1 Direction: {_dir_icon(workspace['p1_dir'])} {workspace['p1_dir']}"),
                                Choice("p1_str", name=f"   P1 Strength: {_str_icon(workspace['p1_str'])} {workspace['p1_str']}"),
                                Choice("p1_timeframe", name=f"   P1 Timeframe: {workspace['p1_timeframe']}"),
                                Choice("p1_type", name=f"   P1 Fractal Type: {workspace['p1_type']}"),
                                Choice("nodes_l1", name=f"   Nodes L1: {workspace['nodes_l1']}"),
                                Choice("nodes_l2", name=f"   Nodes L2: {workspace['nodes_l2']}"),
                                Separator("── 🔵 P2 · Structure ──"),
                                Choice("p2_thesis", name=f"   P2 Thesis: {_preview(workspace['p2_thesis'])}"),
                                Choice("p2_dir", name=f"   P2 Direction: {_dir_icon(workspace['p2_dir'])} {workspace['p2_dir']}"),
                                Choice("p2_str", name=f"   P2 Strength: {_str_icon(workspace['p2_str'])} {workspace['p2_str']}"),
                                Separator("── 🔵 P3 · Trend ──"),
                                Choice("p3_thesis", name=f"   P3 Thesis: {_preview(workspace['p3_thesis'])}"),
                                Choice("p3_dir", name=f"   P3 Direction: {_dir_icon(workspace['p3_dir'])} {workspace['p3_dir']}"),
                                Choice("p3_str", name=f"   P3 Strength: {_str_icon(workspace['p3_str'])} {workspace['p3_str']}"),
                                Separator("── 🟣 P4 · Hierarchy ──"),
                                Choice("p4_thesis", name=f"   P4 Thesis: {_preview(workspace['p4_thesis'])}"),
                                Choice("p4_dir", name=f"   P4 Direction: {_dir_icon(workspace['p4_dir'])} {workspace['p4_dir']}"),
                                Choice("p4_str", name=f"   P4 Strength: {_str_icon(workspace['p4_str'])} {workspace['p4_str']}"),
                                Choice("p4_hierarchy", name=f"   P4 Hierarchy: {workspace['p4_hierarchy']}"),
                                Separator("── 🟡 Edge / Meta ──"),
                                Choice("edge_description", name=f"   Edge Description: {_preview(workspace['edge_description'])}"),
                                Choice("mark_price", name=f"   🔵 Mark Price: {workspace.get('mark_price', 'N/A')}"),
                                Choice("edge_validation_price", name=f"   🟢 Edge Validation Price: {workspace.get('edge_validation_price', 'N/A')}"),
                                Choice("structural_invalidation", name=f"   🔴 Structural Invalidation Price: {workspace.get('structural_invalidation', 'N/A')}"),
                                Choice("efficiency_timeframe", name=f"   Efficiency Timeframe: {workspace.get('efficiency_timeframe', '1H')}"),
                                Choice("tactical_classification", name=f"   Tactical Classification: {workspace['tactical_classification']}"),
                                Separator(),
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
                            elif field == "mark_price":
                                try:
                                    val = get_mandatory_text("Enter Mark Price [Leave empty for None]")
                                    workspace["mark_price"] = Decimal(val) if val.strip() else None
                                except Exception:
                                    console.print("[bold red]Invalid decimal input. Aborting modification.[/bold red]")
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
                                record.mark_price = workspace.get("mark_price")
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
                        rev_text.append(f"Structural MAE: {workspace['structural_mae'] if workspace['structural_mae'] is not None else 'N/A'}\n", style="white")
                        rev_text.append(f"Structural MFE: {workspace['structural_mfe'] if workspace['structural_mfe'] is not None else 'N/A'}\n", style="white")
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
                                Separator("── 🔵 Structural Bias ──"),
                                Choice("bias_a", name=f"   Bias A (Original): {workspace['bias_a']}"),
                                Choice("real_bias_b", name=f"   Real Bias B: {workspace['real_bias_b']}"),
                                Separator("── 🟣 Resolution ──"),
                                Choice("resolution_type", name=f"   Resolution Type: {workspace['resolution_type']}"),
                                Choice("structural_resolution", name=f"   Structural Resolution: {workspace['structural_resolution']}"),
                                Choice("failure_reason", name=f"   Failure Reason: {workspace['failure_reason']}"),
                                Separator("── 🟡 Compliance & Metrics ──"),
                                Choice("specific_bias_compliance", name=f"   Specific Bias Compliance: {workspace['specific_bias_compliance']}"),
                                Choice("false_regime_rate", name=f"   False Regime Rate: {workspace['false_regime_rate']}"),
                                Choice("efficiency_timeframe", name=f"   Efficiency Timeframe: {workspace['efficiency_timeframe']}"),
                                Choice("structural_mae", name=f"   Structural MAE: {workspace['structural_mae'] if workspace['structural_mae'] is not None else 'N/A'}"),
                                Choice("structural_mfe", name=f"   Structural MFE: {workspace['structural_mfe'] if workspace['structural_mfe'] is not None else 'N/A'}"),
                                Separator("── ⚪ Notes ──"),
                                Choice("lesson_eff", name=f"   Lesson Learned: {_preview(workspace['lesson_eff'])}"),
                                Separator(),
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
                            elif field == "structural_mae":
                                raw = get_optional_text("Edit Structural MAE (peor precio alcanzado en contra de la tesis)")
                                try:
                                    workspace["structural_mae"] = Decimal(str(raw)) if raw else None
                                except Exception:
                                    console.print("[bold red]Invalid decimal input for Structural MAE. Setting to None.[/bold red]")
                                    workspace["structural_mae"] = None
                            elif field == "structural_mfe":
                                raw = get_optional_text("Edit Structural MFE (mejor precio alcanzado a favor de la tesis)")
                                try:
                                    workspace["structural_mfe"] = Decimal(str(raw)) if raw else None
                                except Exception:
                                    console.print("[bold red]Invalid decimal input for Structural MFE. Setting to None.[/bold red]")
                                    workspace["structural_mfe"] = None
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
                                            structural_mae = ?,
                                            structural_mfe = ?,
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
                                        float(workspace["structural_mae"]) if workspace.get("structural_mae") is not None else None,
                                        float(workspace["structural_mfe"]) if workspace.get("structural_mfe") is not None else None,
                                        workspace["lesson_eff"],
                                        now_ts,
                                        record.id
                                    ))
                                else:
                                    raw_conn.execute("""
                                        INSERT INTO efficiency_audit (id, bias_a, resolution_type, real_bias_b, structural_resolution, failure_reason, specific_bias_compliance, false_regime_rate, efficiency_timeframe, structural_mae, structural_mfe, lesson_learned, created_at, updated_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                        float(workspace["structural_mae"]) if workspace.get("structural_mae") is not None else None,
                                        float(workspace["structural_mfe"]) if workspace.get("structural_mfe") is not None else None,
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
                        
                        # Resolve the instrument's contract_size (config/contract_specs.py).
                        # NO VERIFICADO / desconocido / sin asset -> None (calculate_algebraic_metrics
                        # trata None como multiplicador 1); nunca truena el repair flow.
                        _cs = None
                        try:
                            from tools import pnl_calculator as _pnl
                            _cs = _pnl.resolve_spec(w.get("asset"))["contract_size"]
                        except (_pnl.UnverifiedSymbolError, _pnl.UnknownSymbolError) as _e:
                            logging.getLogger(__name__).warning(
                                "contract_size sin resolver en repair flow (asset=%r): %s", w.get("asset"), _e)

                        # Pass 2: Calculated Math passing the resolved direction as an input parameter
                        try:
                            metrics = calculate_algebraic_metrics(direction, ep, sl, cp, tp, size, mae, mfe, cost, contract_size=_cs)
                            w.update(metrics)
                            if _cs is None:
                                # Símbolo NO VERIFICADO: no persistir ningún campo escalado por
                                # contract_size sin el multiplicador (sería 1/contract_size del
                                # valor real). None -- mismo criterio que el validator de audit_tactical.
                                for _f in MONEY_FIELDS_REQUIRING_CONTRACT_SIZE:
                                    w[_f] = None
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

                    def recalculate_session(w):
                        et = w.get("entry_time")
                        if not et:
                            return
                        utc_check_time = et + datetime.timedelta(hours=6)
                        hr = utc_check_time.hour
                        if 13 <= hr < 16:
                            w["session"] = "London/NY Overlap"
                        elif 16 <= hr < 21:
                            w["session"] = "New York"
                        elif 8 <= hr < 13:
                            w["session"] = "London"
                        else:
                            w["session"] = "Asia/Off"

                    GATE_EDIT_LABELS = {
                        "g1_trend_15m": "G1: P0 Trend 15m",
                        "g2_fractal_trend": "G2: Trend Fractal (5m or 15m)",
                        "g3_limit_order": "G3: Limit Order",
                        "g4_breathing": "G4: Breathing - Mindfulness",
                        "g5_manual_cooldown": "G5: Manual Cooldown",
                        "g6_sl_validated": "G6: SL Validated",
                        "g7_tp_validated": "G7: TP Validated",
                    }
                    CONF_EDIT_LABELS = {
                        "c1_kl_support": "C1: KL as Support/Resistance",
                        "c2_fractal_std": "C2: Standard Fractal Confirmation (5-15m)",
                        "c3_fractal_1m": "C3: 1m Fractal Confirmation/Assistance",
                        "c4_fractal_1h": "C4: 1h Fractal Continuation or Inflection",
                        "c5_kl_target": "C5: KL as target",
                        "c6_liquidity": "C6: Liquidity grabbed or to be grabbed",
                        "c7_retracement": "C7: 0.4-0.6 Retracement in P015m",
                        "c8_convergence_15m": "C8: Convergence with P015m",
                    }

                    def edit_gates(w):
                        choices = [Choice(k, name=lbl, enabled=bool(w.get(k))) for k, lbl in GATE_EDIT_LABELS.items()]
                        selected = bind_pause(inquirer.checkbox(message="Select fulfilled Gates >", choices=choices)).execute()
                        for k in GATE_EDIT_LABELS:
                            w[k] = k in selected
                        w["gates_failed"] = 7 - len(selected)

                    def edit_confirmations(w):
                        choices = [Choice(k, name=lbl, enabled=bool(w.get(k))) for k, lbl in CONF_EDIT_LABELS.items()]
                        selected = bind_pause(inquirer.checkbox(message="Select fulfilled Confirmations >", choices=choices)).execute()
                        for k in CONF_EDIT_LABELS:
                            w[k] = k in selected
                        w["confirmations_count"] = len(selected)

                    # Run initial recalculation pass
                    recalculate_tactical_math(workspace, workspace["p0_dir"], workspace["p2_dir"], workspace["p4_dir"])
                    recalculate_session(workspace)
                    
                    while True:
                        rev_text = Text()
                        # --- Core Inputs ---
                        rev_text.append("--- Core Inputs ---\n", style="bold green")
                        rev_text.append(f"Order Filled: {workspace['order_filled']}\n", style="white")
                        if not workspace['order_filled']:
                            rev_text.append(f"Skip Reason: {workspace.get('skip_reason') or 'N/A'}\n", style="yellow")
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
                        _ns = workspace.get('notional_size')
                        rev_text.append(f"Notional Size: {f'{_ns:.2f}' if _ns is not None else 'N/A (símbolo no verificado)'}\n", style="white")
                        _nsu = workspace.get('notional_size_usd')
                        rev_text.append(f"Notional Size USD: {f'{_nsu:.2f}' if _nsu is not None else 'N/A (símbolo no verificado)'}\n", style="white")
                        _car = workspace.get('capital_at_risk')
                        rev_text.append(f"Capital At Risk: {f'{_car:.2f}' if _car is not None else 'N/A (símbolo no verificado)'}\n", style="white")
                        _rusd = workspace.get('risk_usd')
                        rev_text.append(f"Risk USD: {f'{_rusd:.2f}' if _rusd is not None else 'N/A (símbolo no verificado)'}\n", style="white" if _rusd is not None else "yellow")
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
                        rev_text.append(f"Exit Type: {workspace.get('exit_type')}   Session: {workspace.get('session')}\n", style="white")

                        # --- Motor B (Gates & Confirmations) ---
                        rev_text.append("\n--- Motor B (Gates & Confirmations) ---\n", style="bold red")
                        gates_failed_val = workspace.get("gates_failed", 0) or 0
                        gf_style = "bold red" if gates_failed_val > 0 else "bold green"
                        rev_text.append("  Gates Failed: ", style="dim")
                        rev_text.append(f"{gates_failed_val}/7\n", style=gf_style)
                        for k, lbl in GATE_EDIT_LABELS.items():
                            icon = "✓" if workspace.get(k) else "✗"
                            icon_style = "bold green" if workspace.get(k) else "bold red"
                            rev_text.append("  [", style="white")
                            rev_text.append(icon, style=icon_style)
                            rev_text.append(f"] {lbl}\n", style="white")

                        confs_count_val = workspace.get("confirmations_count", 0) or 0
                        rev_text.append("  Confirmations: ", style="dim")
                        rev_text.append(f"{confs_count_val}/8\n", style="bold cyan")
                        for k, lbl in CONF_EDIT_LABELS.items():
                            icon = "✓" if workspace.get(k) else "✗"
                            icon_style = "bold green" if workspace.get(k) else "bold red"
                            rev_text.append("  [", style="white")
                            rev_text.append(icon, style=icon_style)
                            rev_text.append(f"] {lbl}\n", style="white")

                        if workspace.get("mfe_potencial_estimado") is not None:
                            rev_text.append("  MFE Potencial Estimado (S6): ", style="dim")
                            rev_text.append(f"{workspace.get('mfe_potencial_estimado')}R\n", style="bold yellow")

                        # --- Psychological & Cognitive Logging ---
                        rev_text.append("\n--- Psychological & Cognitive Logging ---\n", style="bold blue")
                        rev_text.append(f"Primary Emotion: {workspace.get('primary_emotion')}\n", style="white")
                        
                        emotions_list = workspace.get("emotions") or []
                        emotions_str = ", ".join([str(e.value if hasattr(e, 'value') else e) for e in emotions_list]) if isinstance(emotions_list, list) else str(emotions_list)
                        rev_text.append(f"  Emotions:             {format_indented_block(emotions_str, indent_spaces=24, first_line_flush=True)}\n", style="white")
                        
                        be_list = workspace.get("behavioral_errors") or []
                        be_list_clean = [str(b.value if hasattr(b, 'value') else b) for b in be_list] if isinstance(be_list, list) else []
                        be_str = ", ".join(be_list_clean) if be_list_clean else "N/A"
                        wrapped_be = format_indented_block(be_str, indent_spaces=24, first_line_flush=True)
                        rev_text.append(f"  Behavioral Errors:    {wrapped_be}\n", style="white")

                        detected = compute_detected_patterns({
                            "primary_emotion": workspace.get("primary_emotion"),
                            "anxiety_level": workspace.get("anxiety_level"),
                            "impatience_level": workspace.get("impatience_level"),
                            "mental_clarity_level": workspace.get("mental_clarity_level"),
                            "behavioral_errors": be_list_clean,
                            "confirmation_status": workspace.get("confirmation_status"),
                            "gates_failed": workspace.get("gates_failed"),
                            "followed_plan": workspace.get("followed_plan"),
                        })
                        rev_text.append("  Detected Patterns:\n", style="dim")
                        for label, color in detected:
                            rev_text.append(f"    • {label}\n", style=color)
                        
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
                            gates_failed_disp = workspace.get("gates_failed", 0) or 0
                            confs_count_disp = workspace.get("confirmations_count", 0) or 0
                            edit_choices = [
                                Separator("── 🟢 Core Inputs ──"),
                                Choice("order_filled", name=f"   Order Filled: {workspace['order_filled']}"),
                                Choice("skip_reason", name=f"   Skip Reason: {workspace.get('skip_reason') or 'N/A'}"),
                                Choice("entry_price", name=f"   Entry Price: {workspace['entry_price']}"),
                                Choice("closing_price", name=f"   Closing Price: {workspace['closing_price']}"),
                                Choice("size", name=f"   Size: {workspace['size']}"),
                                Choice("stop_loss", name=f"   Stop Loss: {workspace['stop_loss']}"),
                                Choice("take_profit", name=f"   Take Profit: {workspace['take_profit']}"),
                                Choice("mae", name=f"   MAE: {workspace['mae']}"),
                                Choice("mfe", name=f"   MFE: {workspace['mfe']}"),
                                Choice("cost", name=f"   Cost: {workspace.get('cost', 0.0)}"),
                                Choice("could_hit_tp", name=f"   Could Hit TP: {workspace['could_hit_tp']}"),
                                Choice("entry_time", name=f"   Entry Time: {workspace['entry_time']}"),
                                Choice("exit_time", name=f"   Exit Time: {workspace['exit_time']}"),
                                Choice("exit_type", name=f"   Exit Type: {workspace.get('exit_type')}"),
                                Separator("── 🟣 Execution Framework / Motor B ──"),
                                Choice("tier_setup", name=f"   Tier Setup: {workspace['tier_setup']}"),
                                Choice("market_state", name=f"   Market State: {workspace['market_state']}"),
                                Choice("followed_plan", name=f"   Followed Plan: {workspace['followed_plan']}"),
                                Choice("setup_type", name=f"   Setup Type: {workspace['setup_type']}"),
                                Choice("htf_trend_context", name=f"   HTF Trend: {workspace['htf_trend_context']}"),
                                Choice("ltf_trend_context", name=f"   LTF Trend: {workspace['ltf_trend_context']}"),
                                Choice("confirmation_5m_15m", name=f"   5m/15m Confirmation: {workspace['confirmation_5m_15m']}"),
                                Choice("confirmation_status", name=f"   Confirmation Status: {workspace['confirmation_status']}"),
                                Choice("confirmation_params", name=f"   Confirmation Params: {len(workspace['confirmation_params'])} chosen"),
                                Choice("gates", name=f"   🚦 Gates: {7 - gates_failed_disp}/7 passed ({gates_failed_disp} failed)"),
                                Choice("confirmations", name=f"   🚦 Confirmations: {confs_count_disp}/8 chosen"),
                                Choice("mfe_potencial_estimado", name=f"   MFE Potencial Estimado: {workspace.get('mfe_potencial_estimado', 'N/A')}"),
                                Separator("── 🔵 Psychological & Cognitive ──"),
                                Choice("primary_emotion", name=f"   Primary Emotion: {workspace['primary_emotion']}"),
                                Choice("emotions", name=f"   Emotions List: {len(workspace['emotions'])} chosen"),
                                Choice("behav_errors", name=f"   Behavioral Errors List: {len(workspace['behavioral_errors'])} chosen"),
                                Choice("pre_trade_emotions", name=f"   Pre Trade Emotions: {_preview(workspace['pre_trade_emotions']) if workspace['pre_trade_emotions'] else 'N/A'}"),
                                Choice("mid_trade_emotions", name=f"   Mid Trade Emotions: {_preview(workspace['mid_trade_emotions']) if workspace['mid_trade_emotions'] else 'N/A'}"),
                                Choice("post_trade_emotions", name=f"   Post Trade Emotions: {_preview(workspace['post_trade_emotions']) if workspace['post_trade_emotions'] else 'N/A'}"),
                                Separator("── 🟠 Internal State Thresholds ──"),
                                Choice("anxiety_level", name=f"   Anxiety Level: {workspace['anxiety_level']}"),
                                Choice("impatience_level", name=f"   Impatience Level: {workspace['impatience_level']}"),
                                Choice("mental_clarity_level", name=f"   Mental Clarity Level: {workspace['mental_clarity_level']}"),
                                Separator("── ⚪ Notes ──"),
                                Choice("lesson_tact", name=f"   Lesson Learned: {_preview(workspace['lesson_tact'])}"),
                                Choice("visual_lesson_path", name=f"   Visual Lesson: {workspace.get('visual_lesson_path', 'nan')}"),
                                Separator(),
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
                                
                            if field == "order_filled":
                                workspace["order_filled"] = bind_pause(inquirer.select(
                                    message="Edit Order Filled >",
                                    choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                    pointer=">",
                                    qmark=""
                                )).execute() == "yes"
                            elif field == "skip_reason":
                                workspace["skip_reason"] = get_enum_choice("Edit Skip Reason", SkipReason)
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
                                recalculate_session(workspace)
                            elif field == "exit_time":
                                workspace["exit_time"] = get_mandatory_datetime("Edit Exit Time")
                            elif field == "exit_type":
                                workspace["exit_type"] = get_enum_choice("Edit Exit Type", ExitType).value
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
                            elif field == "confirmation_params":
                                workspace["confirmation_params"] = [p.value if hasattr(p, 'value') else p for p in get_multi_enum_choice("Edit Confirmation Params", ConfirmationParams)]
                            elif field == "gates":
                                edit_gates(workspace)
                            elif field == "confirmations":
                                edit_confirmations(workspace)
                            elif field == "mfe_potencial_estimado":
                                workspace["mfe_potencial_estimado"] = get_mandatory_float("Edit MFE Potencial Estimado")
                            elif field == "emotions":
                                current_emotions = workspace.get("emotions") or []
                                active_values = {e.value for e in ACTIVE_EMOTIONS}
                                legacy_members = [e for e in Emotions if e.name != "SKIP" and e.value not in active_values and e.value in current_emotions]
                                emotion_choices = list(ACTIVE_EMOTIONS) + legacy_members
                                workspace["emotions"] = [e.value if hasattr(e, 'value') else e for e in get_multi_enum_choice("Edit Emotions", Emotions, choices=emotion_choices, preselected=current_emotions)]
                            elif field == "behav_errors":
                                workspace["behavioral_errors"] = [b.value if hasattr(b, 'value') else b for b in get_multi_enum_choice("Edit Behavioral Errors", BehavioralErrors)]
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
                                target_ta_id = selected_ta.id if selected_ta else None
                                exists = raw_conn.execute("SELECT 1 FROM tactical_audit WHERE id = ?", (target_ta_id,)).fetchone() if target_ta_id else None

                                if exists:
                                    raw_conn.execute("""
                                        UPDATE tactical_audit SET
                                            order_filled = ?,
                                            skip_reason = ?,
                                            confirmation_5m_15m = ?,
                                            entry_price = ?,
                                            closing_price = ?,
                                            size = ?,
                                            stop_loss = ?,
                                            take_profit = ?,
                                            mae_adverse = ?,
                                            mfe_favorable = ?,
                                            captured_mae = ?,
                                            r_multiple = ?,
                                            captured_mfe = ?,
                                            could_hit_tp = ?,
                                            lesson_learned = ?,
                                            tier_setup = ?,
                                            market_state = ?,
                                            session = ?,
                                            exit_type = ?,
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
                                            exit_time = ?,
                                            g1_trend_15m = ?,
                                            g2_fractal_trend = ?,
                                            g3_limit_order = ?,
                                            g4_breathing = ?,
                                            g5_manual_cooldown = ?,
                                            g6_sl_validated = ?,
                                            g7_tp_validated = ?,
                                            c1_kl_support = ?,
                                            c2_fractal_std = ?,
                                            c3_fractal_1m = ?,
                                            c4_fractal_1h = ?,
                                            c5_kl_target = ?,
                                            c6_liquidity = ?,
                                            c7_retracement = ?,
                                            c8_convergence_15m = ?,
                                            gates_failed = ?,
                                            confirmations_count = ?,
                                            mfe_potencial_estimado = ?
                                        WHERE id = ?
                                    """, (
                                        bool(workspace["order_filled"]),
                                        workspace.get("skip_reason"),
                                        workspace["confirmation_5m_15m"] or "no",
                                        float(workspace["entry_price"]),
                                        float(workspace["closing_price"]),
                                        float(workspace["size"]),
                                        float(workspace["stop_loss"]),
                                        float(workspace["take_profit"]),
                                        float(workspace["mae"]),
                                        float(workspace["mfe"]),
                                        float(workspace.get("captured_mae", 0.0)),
                                        float(workspace.get("r_multiple", 0.0)),
                                        float(workspace.get("captured_mfe", 0.0)),
                                        workspace["could_hit_tp"],
                                        workspace["lesson_tact"],
                                        workspace["tier_setup"],
                                        workspace["market_state"],
                                        workspace.get("session"),
                                        workspace.get("exit_type"),
                                        workspace["followed_plan"],
                                        workspace["primary_emotion"],
                                        workspace["setup_type"],
                                        workspace["htf_trend_context"],
                                        workspace["ltf_trend_context"],
                                        workspace["confirmation_status"],
                                        int(workspace["anxiety_level"]),
                                        int(workspace["impatience_level"]),
                                        int(workspace["mental_clarity_level"]),
                                        float(workspace["risk_usd"]) if workspace.get("risk_usd") is not None else None,
                                        float(workspace["r_r"]),
                                        float(workspace["pnl_and_cost"]),
                                        float(workspace["notional_size"]) if workspace.get("notional_size") is not None else None,
                                        float(workspace["capital_at_risk"]) if workspace.get("capital_at_risk") is not None else None,
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
                                        bool(workspace.get("g1_trend_15m")),
                                        bool(workspace.get("g2_fractal_trend")),
                                        bool(workspace.get("g3_limit_order")),
                                        bool(workspace.get("g4_breathing")),
                                        bool(workspace.get("g5_manual_cooldown")),
                                        bool(workspace.get("g6_sl_validated")),
                                        bool(workspace.get("g7_tp_validated")),
                                        bool(workspace.get("c1_kl_support")),
                                        bool(workspace.get("c2_fractal_std")),
                                        bool(workspace.get("c3_fractal_1m")),
                                        bool(workspace.get("c4_fractal_1h")),
                                        bool(workspace.get("c5_kl_target")),
                                        bool(workspace.get("c6_liquidity")),
                                        bool(workspace.get("c7_retracement")),
                                        bool(workspace.get("c8_convergence_15m")),
                                        int(workspace.get("gates_failed", 0) or 0),
                                        int(workspace.get("confirmations_count", 0) or 0),
                                        float(workspace["mfe_potencial_estimado"]) if workspace.get("mfe_potencial_estimado") is not None else None,
                                        target_ta_id
                                    ))
                                else:
                                    new_ta_id = str(uuid.uuid4())
                                    raw_conn.execute("""
                                        INSERT INTO tactical_audit (
                                            id, trade_id, order_filled, skip_reason, confirmation_5m_15m, entry_price, closing_price, size, stop_loss, take_profit, mae_adverse, mfe_favorable, captured_mae, r_multiple, captured_mfe, could_hit_tp, lesson_learned,
                                            tier_setup, market_state, session, exit_type, followed_plan, primary_emotion, setup_type, htf_trend_context, ltf_trend_context, confirmation_status, anxiety_level, impatience_level, mental_clarity_level,
                                            risk_usd, r_r, pnl_and_cost, notional_size, capital_at_risk, trade_decision, emotions, behavioral_errors, cognitive_patterns, visual_lesson_path,
                                            pre_trade_emotions, mid_trade_emotions, post_trade_emotions, confirmation_params, entry_time, exit_time,
                                            g1_trend_15m, g2_fractal_trend, g3_limit_order, g4_breathing, g5_manual_cooldown, g6_sl_validated, g7_tp_validated,
                                            c1_kl_support, c2_fractal_std, c3_fractal_1m, c4_fractal_1h, c5_kl_target, c6_liquidity, c7_retracement, c8_convergence_15m,
                                            gates_failed, confirmations_count, mfe_potencial_estimado, created_at, updated_at
                                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                                  ?, ?, ?, ?, ?, ?, ?,
                                                  ?, ?, ?, ?, ?, ?, ?, ?,
                                                  ?, ?, ?, ?, ?)
                                    """, (
                                        new_ta_id,
                                        record.id,
                                        bool(workspace["order_filled"]),
                                        workspace.get("skip_reason"),
                                        workspace["confirmation_5m_15m"] or "no",
                                        float(workspace["entry_price"]),
                                        float(workspace["closing_price"]),
                                        float(workspace["size"]),
                                        float(workspace["stop_loss"]),
                                        float(workspace["take_profit"]),
                                        float(workspace["mae"]),
                                        float(workspace["mfe"]),
                                        float(workspace.get("captured_mae", 0.0)),
                                        float(workspace.get("r_multiple", 0.0)),
                                        float(workspace.get("captured_mfe", 0.0)),
                                        workspace["could_hit_tp"],
                                        workspace["lesson_tact"],
                                        workspace["tier_setup"],
                                        workspace["market_state"],
                                        workspace.get("session"),
                                        workspace.get("exit_type"),
                                        workspace["followed_plan"],
                                        workspace["primary_emotion"],
                                        workspace["setup_type"],
                                        workspace["htf_trend_context"],
                                        workspace["ltf_trend_context"],
                                        workspace["confirmation_status"],
                                        int(workspace["anxiety_level"]),
                                        int(workspace["impatience_level"]),
                                        int(workspace["mental_clarity_level"]),
                                        float(workspace["risk_usd"]) if workspace.get("risk_usd") is not None else None,
                                        float(workspace["r_r"]),
                                        float(workspace["pnl_and_cost"]),
                                        float(workspace["notional_size"]) if workspace.get("notional_size") is not None else None,
                                        float(workspace["capital_at_risk"]) if workspace.get("capital_at_risk") is not None else None,
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
                                        bool(workspace.get("g1_trend_15m")),
                                        bool(workspace.get("g2_fractal_trend")),
                                        bool(workspace.get("g3_limit_order")),
                                        bool(workspace.get("g4_breathing")),
                                        bool(workspace.get("g5_manual_cooldown")),
                                        bool(workspace.get("g6_sl_validated")),
                                        bool(workspace.get("g7_tp_validated")),
                                        bool(workspace.get("c1_kl_support")),
                                        bool(workspace.get("c2_fractal_std")),
                                        bool(workspace.get("c3_fractal_1m")),
                                        bool(workspace.get("c4_fractal_1h")),
                                        bool(workspace.get("c5_kl_target")),
                                        bool(workspace.get("c6_liquidity")),
                                        bool(workspace.get("c7_retracement")),
                                        bool(workspace.get("c8_convergence_15m")),
                                        int(workspace.get("gates_failed", 0) or 0),
                                        int(workspace.get("confirmations_count", 0) or 0),
                                        float(workspace["mfe_potencial_estimado"]) if workspace.get("mfe_potencial_estimado") is not None else None,
                                        datetime.datetime.now().isoformat(),
                                        datetime.datetime.now().isoformat()
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
                        dt_choices.append(Separator("── 🔵 Unified Department ──"))
                        dt_choices.append(Choice("u_created_at", name=f"   Created At: {to_local_display(record.created_at)}"))
                        dt_choices.append(Choice("u_updated_at", name=f"   Updated At: {to_local_display(record.updated_at)}"))

                        if record.efficiency_audit:
                            dt_choices.append(Separator("── 🟡 Efficiency Audit ──"))
                            dt_choices.append(Choice("ea_created_at", name=f"   Created At: {to_local_display(record.efficiency_audit.created_at)}"))
                            dt_choices.append(Choice("ea_updated_at", name=f"   Updated At: {to_local_display(record.efficiency_audit.updated_at)}"))
                            dt_choices.append(Choice("ea_res_time", name=f"   Resolution Time: {to_local_display(record.efficiency_audit.resolution_time)}"))

                        if selected_ta:
                            dt_choices.append(Separator(f"── 🟣 Tactical Audit ({selected_ta.id[:8]}) ──"))
                            dt_choices.append(Choice("ta_entry", name=f"   Entry Time: {to_local_display(selected_ta.entry_time)}"))
                            dt_choices.append(Choice("ta_exit", name=f"   Exit Time: {to_local_display(selected_ta.exit_time)}"))

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
                                    # Spec 002 (RF-14b): una hora puesta a mano es `corrected`.
                                    record.efficiency_audit.resolution_time_source = RESOLUTION_TIME_SOURCE_CORRECTED
                                elif dt_choice == "ta_entry":
                                    selected_ta.entry_time = new_dt
                                elif dt_choice == "ta_exit":
                                    selected_ta.exit_time = new_dt
                            except GoBackException:
                                pass

if __name__ == "__main__":
    cli()
