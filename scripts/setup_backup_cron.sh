#!/usr/bin/env bash
# setup_backup_cron.sh — Instala/actualiza la línea de cron para backup.py
#
# Idempotente: se puede correr múltiples veces sin duplicar la entrada.
# Usa el env conda blast_master para que backup.py tenga b2sdk, tenacity, etc.
#
# Uso:
#   bash scripts/setup_backup_cron.sh [HORA] [MINUTO]
#   bash scripts/setup_backup_cron.sh          # default: 08:00
#   bash scripts/setup_backup_cron.sh 15 30    # 15:30

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_SCRIPT="$PROJECT_ROOT/tools/backup.py"
ENV_FILE="$PROJECT_ROOT/.env"
LOG_DIR="$PROJECT_ROOT/.data/archives"

HOUR="${1:-8}"
MINUTE="${2:-0}"
CONDA_ENV="blast_master"

# Validar que backup.py existe
if [ ! -f "$BACKUP_SCRIPT" ]; then
    echo "ERROR: $BACKUP_SCRIPT no encontrado"
    exit 1
fi

# Validar que .env existe
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE no encontrado"
    exit 1
fi

# Verificar variables críticas de .env
echo "=== Verificando configuración de .env ==="
check_var() {
    local var_name=$1
    local val
    val=$(grep -E "^${var_name}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | sed 's/^[[:space:]]*//' || true)
    if [ -z "$val" ] || [[ "$val" == *"your_"* ]] || [[ "$val" == *"/path/to/"* ]]; then
        echo "  ⚠ $var_name: NO CONFIGURADO (valor='$val')"
        return 1
    else
        echo "  ✓ $var_name: configurado"
        return 0
    fi
}

warnings=0
check_var "USB_BACKUP_PATH" || ((warnings++))
check_var "B2_KEY_ID" || ((warnings++))
check_var "B2_APP_KEY" || ((warnings++))
check_var "B2_BUCKET_NAME" || ((warnings++))

if [ "$warnings" -gt 0 ]; then
    echo ""
    echo "⚠ $warnings variable(s) no configuradas. El backup reportará esos medios como fallidos."
    echo "  Puedes continuar instalando el cron; los medios faltantes no impedirán el snapshot local."
    read -p "  ¿Continuar? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Abortado."
        exit 0
    fi
fi

CONDA_BIN="$(which conda 2>/dev/null || echo "$HOME/miniconda3/bin/conda")"

# Construir la línea de cron
# - Usa conda run para activar el env correcto
# - Redirige stdout/stderr al log dir (además del log propio de backup.py)
CRON_LINE="$MINUTE $HOUR * * * $CONDA_BIN run -n $CONDA_ENV python $BACKUP_SCRIPT backup >> $LOG_DIR/cron_backup.log 2>&1"

echo ""
echo "=== Instalando cron ==="
echo "  Línea: $CRON_LINE"
echo "  Horario: ${HOUR}:$(printf '%02d' $MINUTE) diario"

# Idempotente: eliminar líneas anteriores de backup.py, luego agregar la nueva
(crontab -l 2>/dev/null | grep -v "tools/backup.py" || true; echo "$CRON_LINE") | crontab -

echo ""
echo "=== Verificación ==="
echo "Crontab actual para $(whoami):"
crontab -l
echo ""
echo "✓ Cron instalado exitosamente."
echo ""
echo "Para verificar que funciona sin esperar al horario:"
echo "  conda run -n $CONDA_ENV python $BACKUP_SCRIPT backup --dry-run"
