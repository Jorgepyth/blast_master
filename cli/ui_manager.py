import datetime
import subprocess
import os
import json
from enum import Enum
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.align import Align
from rich.text import Text
from rich import box
from rich.theme import Theme

from tools.database import (
    get_records_by_state,
    LifecycleState,
    get_assets,
    engine_default,
    UnifiedDepartment
)

CACHE_FILE = ".data/paused_audits.json"

def has_paused_state(trade_id, audit_type):
    if not os.path.exists(CACHE_FILE): return False
    try:
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
            return bool(cache.get(trade_id, {}).get(audit_type))
    except Exception:
        return False

# Semantic styling theme
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

def check_daemon_status():
    try:
        result = subprocess.run(["pgrep", "-f", "notion_sync.py"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return "[bold green]ONLINE[/bold green]"
    except Exception:
        pass
    return "[bold red]OFFLINE[/bold red]"

def get_total_records_count(engine=None):
    from sqlalchemy.orm import Session
    from sqlalchemy import func
    try:
        eng = engine or engine_default
        with Session(eng) as db_session:
            return db_session.query(func.count(UnifiedDepartment.id)).scalar()
    except Exception:
        return 0

class CLIState:
    def __init__(self):
        self.active_idx = 0
        self.active_session = None  # None or dict: {"id": str, "name": str}
        self.pending_records = []
        self.daemon_status = "OFFLINE"
        self.total_records = 0
        self.tracked_assets = []
        
    def refresh_metrics(self, engine=None):
        try:
            self.pending_records = get_records_by_state(LifecycleState.PENDING_AUDITS, engine=engine)
        except Exception:
            self.pending_records = []
            
        self.daemon_status = check_daemon_status()
        self.total_records = get_total_records_count(engine=engine)
        
        try:
            self.tracked_assets = get_assets(engine=engine)
        except Exception:
            self.tracked_assets = []

def build_persistent_layout(state: CLIState = None, footer_type="menu", active_session_name=None, pending_count=0, sync_status="[bold red]OFFLINE[/bold red]") -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", minimum_size=10),
        Layout(name="footer", size=3)
    )
    
    # Header
    if state is not None:
        active_sess = state.active_session
        p_count = len(state.pending_records)
        daemon_stat = state.daemon_status
    else:
        active_sess = {"name": active_session_name} if active_session_name else None
        p_count = pending_count
        daemon_stat = sync_status

    if active_sess:
        session_str = f"[bold warning]TEST FLIGHT: {active_sess['name']}[/bold warning]"
    else:
        session_str = f"[bold primary]B.L.A.S.T. ENGINE v1.1[/bold primary]"
        
    status_str = f"Audits: [bold warning]{p_count}[/bold warning] | Notion: {daemon_stat}"
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

def get_welcome_options(state: CLIState):
    options = [
        ("1", "New Unified Analysis", "primary", "core"),
        ("2", "Execute Audits", "primary", "core"),
        ("3", "Review History (Last 10)", "primary", "core"),
        ("4", "Force Notion Sync", "primary", "system"),
        ("5", "Configuration", "primary", "system"),
    ]
    if state.active_session:
        options.append(("t", f"Test Drive ({state.active_session['name']})", "warning", "test_drive"))
        options.append(("e", f"Exit Test Flight - {state.active_session['name']}", "warning", "exit_session"))
    else:
        options.append(("t", "Test Drive (Q3-BTC-SCALPING)", "primary", "system"))
    options.append(("x", "Exit", "danger", "exit"))
    return options

def build_welcome_body(state: CLIState) -> Panel:
    outer_layout = Layout()
    outer_layout.split_row(
        Layout(name="menu_col", ratio=1),
        Layout(name="telemetry_col", ratio=1)
    )
    
    options = get_welcome_options(state)
    
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
                if current_category not in ["test_drive", "exit_session", "exit"]:
                    menu_text.append("\n   --\n", style="muted")
            current_category = category
            
        is_active = (idx == state.active_idx)
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
    
    # 1. Engine State
    state_table = Table(box=box.SIMPLE, border_style="muted", expand=True)
    state_table.add_column("Metric", style="cyan")
    state_table.add_column("Value", style="bold white")
    
    db_name = f"{state.active_session['id'][:8]}.db" if state.active_session else "journal.db"
    state_table.add_row("Active DB", f"{db_name} ({'Test Drive' if state.active_session else 'Production'})")
    state_table.add_row("Total records", f"{state.total_records:,}")
    state_table.add_row("Pending Audits", f"{len(state.pending_records)} [bold warning]")
    
    daemon_on = "ONLINE" in state.daemon_status
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
    
    assets_str = ", ".join(state.tracked_assets[:4])
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

def get_latest_analysis_record(engine=None):
    from tools.database import engine_default, UnifiedDepartment
    from sqlalchemy.orm import Session
    from sqlalchemy import select, desc
    try:
        eng = engine or engine_default
        with Session(eng) as db_session:
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

def render_wizard_layout(step_num, step_title, session, active_key, state: CLIState, engine=None):
    try:
        audit_records = get_records_by_state(LifecycleState.PENDING_AUDITS, engine=engine)
    except Exception:
        audit_records = []
    pending_audits = len(audit_records)
    
    sync_status = check_daemon_status()
        
    layout = build_persistent_layout(state, footer_type="wizard")
    
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
                if "\n" in val:
                    display_val = "\n" + format_indented_block(val, indent_spaces=10, first_line_flush=False)
                else:
                    display_val = val
            else:
                display_val = str(val)
            form_text.append("    [✓] ", style="success")
            form_text.append(f"{label}: ", style="success")
            form_text.append(f"{display_val}\n", style="success")
        else:
            form_text.append(f"    [ ] {label}: ...\n", style="muted")
    
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
    
    prev = get_latest_analysis_record(engine=engine)
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
        def scrub_text(val):
            if not val:
                return ""
            s = str(val)
            for tag in ["[bold white]", "[/bold white]", "[bold cyan]", "[/bold cyan]", "[bold]", "[/bold]"]:
                s = s.replace(tag, "")
            return s

        short_id = scrub_text(prev["id"][:8])
        asset = scrub_text(prev["asset"])
        bias = scrub_text(prev["market_bias"])
        edge = prev["calc_edge"]
        created = prev["created_at"].strftime("%m/%d %H:%M")
        classification = scrub_text(prev["tactical_classification"])
        
        bias_style = "bold green" if bias == "Bullish" else "bold red" if bias == "Bearish" else "bold yellow"
        edge_style = "bold green" if edge >= 0.26 else "bold red" if edge <= -0.26 else "bold yellow"
        
        body_text = Text()
        body_text.append("Asset: ", style="muted")
        body_text.append(asset, style="bold white")
        body_text.append(" | ID: ", style="muted")
        body_text.append(short_id, style="bold cyan")
        body_text.append(f" | {created}\n", style="muted")
        
        body_text.append("Bias: ", style="muted")
        body_text.append(bias, style=bias_style)
        
        body_text.append(" | Edge (I_CD): ", style="muted")
        body_text.append(f"{edge:.4f}\n", style=edge_style)
        
        body_text.append("Classification: ", style="muted")
        body_text.append(classification, style="bold cyan")
        body_text.append("\n", style="muted")
        body_text.append("─" * 40 + "\n", style="muted")
        
        layers = prev["layers"]
        active_layer = "P0"
        if active_key in ["p0_thesis", "p0_dir", "p0_str", "asset"]:
            active_layer = "P0"
        elif active_key in ["p2_thesis", "p2_dir", "p2_str"]:
            active_layer = "P2"
        elif active_key in ["p3_thesis", "p3_dir", "p3_str"]:
            active_layer = "P3"
        elif active_key in ["p1_thesis", "p1_dir", "p1_str", "p1_tf", "p1_type", "nodes_l1", "nodes_l2"]:
            active_layer = "P1"
        elif active_key in ["p4_thesis", "p4_dir", "p4_str", "p4_hier"]:
            active_layer = "P4"

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
                    import textwrap
                    wrapped_lines = []
                    for line in str(t).splitlines():
                        if line.strip():
                            wrapped_lines.extend(textwrap.wrap(line, width=32))
                        else:
                            wrapped_lines.append("")
                    wrapped_t = "\n".join(wrapped_lines)
                    
                    if "\n" in wrapped_t:
                        indented_thesis = "\n" + format_indented_block(wrapped_t, indent_spaces=11, first_line_flush=False)
                    else:
                        indented_thesis = wrapped_t
                    res.append(f"\n   Thesis: {indented_thesis}", style="dim italic")
            else:
                res.append("N/A", style="dim")
            res.append("\n")
            return res
            
        if active_layer == "P0":
            body_text.append(fmt_layer("P0 (Macro)", p0, "cyan"))
        elif active_layer == "P2":
            body_text.append(fmt_layer("P2 (Struc)", p2, "cyan"))
        elif active_layer == "P3":
            body_text.append(fmt_layer("P3 (Trend)", p3, "cyan"))
        elif active_layer == "P4":
            body_text.append(fmt_layer("P4 (Hier )", p4, "magenta"))
        elif active_layer == "P1":
            body_text.append(fmt_layer("P1 (Timef)", p1, "magenta"))
        
        if active_layer in ["P1", "P4"]:
            p1_timeframe = scrub_text(prev["p1_timeframe"])
            p4_hierarchy = scrub_text(prev["p4_hierarchy"])
            p1_type = scrub_text(prev["p1_type"])
            
            body_text.append("─" * 40 + "\n", style="muted")
            body_text.append("Tactical Metrics:\n", style="bold white")
            
            body_text.append("  Timeframe: ", style="muted")
            body_text.append(p1_timeframe, style="bold")
            body_text.append(" | Hierarchy: ", style="muted")
            body_text.append(p4_hierarchy, style="bold")
            body_text.append("\n", style="muted")
            
            body_text.append("  Fractal Type: ", style="muted")
            body_text.append(p1_type, style="bold")
            body_text.append("\n", style="muted")
            
            body_text.append("  Nodes L1/L2: ", style="muted")
            body_text.append(f"{prev['nodes_l1']} / {prev['nodes_l2']}", style="bold")
            body_text.append("\n", style="muted")
        
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
