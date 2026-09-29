# Spike de interoperabilidad (T22) — resultado

- **Fecha:** 2026-09-29.
- **Qué se probó:** el camino completo del export automático, con MT5 abierto: `candles export --symbol XAUUSD` →
  `candle_sync` → `powershell.exe` → Python de Windows → `export_p2_ohlc.py` → MT5 → `_incoming/XAUUSD/<run_id>/` →
  verificación del reloj → fusión al banco → `status.json`.
- **Veredicto:** **funciona**, después de arreglar un defecto que el propio spike encontró (ver "Hallazgos", punto 1).
  `AUTO_EXPORT` puede activarse cuando cargues las variables de abajo en el `.env`.

## Entorno medido

| Cosa | Valor |
|---|---|
| MT5 | terminal build 6182, conectado, `terminal64.exe` corriendo |
| Cuenta / servidor | **`ICMarketsSC-Demo`** (Raw Trading Ltd) — una cuenta **demo** |
| Python de Windows | 3.12.10, de la Microsoft Store, con `MetaTrader5 5.0.6180`, `pandas 3.0.6`, `numpy 2.5.3` |
| Offset del servidor | **UTC+3,00 h** en los 4 símbolos (XAUUSD, BTCUSD, USTEC, US500), en horario de verano |
| Símbolos | los 4 visibles en Market Watch con esos nombres exactos |

## Valores para el `.env` del checkout principal

```
WINDOWS_PYTHON=C:\Users\jcifu\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe
EXPORTER_WIN_PATH=\\wsl.localhost\Ubuntu\home\jorgecg\projects\trading\blast_master\windows_export\export_p2_ohlc.py
BROKER_DST_RULE=us
# AUTO_EXPORT=true        <- solo cuando quieras activar los 4 disparadores (RF-20)
```

- `EXPORTER_WIN_PATH` es la ruta del **checkout principal** vista desde Windows (por UNC de WSL; Python de Windows ejecuta
  scripts desde ahí sin problema). Solo existe con el código nuevo después de integrar la rama (T57). En el spike se
  usó la del worktree de esta sesión.
- `BROKER_DST_RULE=us` es la regla **consistente con lo medido, no probada** (ver "Regla de horario de verano").

## Comando y resultado

```
cd <carpeta vacía>            # `cli()` abre el engine de cuenta para cualquier subcomando: en una carpeta vacía crea un .data/ descartable
PYTHONPATH=<worktree> WINDOWS_PYTHON=… EXPORTER_WIN_PATH=… BROKER_DST_RULE=us \
CANDLE_BANK_DIR=<temporal> ACCOUNTS_DATA_DIR=<copia de las 4 DBs> \
conda run -n blast_master python cli/main.py candles export --symbol XAUUSD --wait 900
```

- El banco y las DBs de referencia fueron **copias** en una carpeta temporal (las DBs, con `sqlite3` en `mode=ro&immutable=1`,
  sin escribir nada en `.data/`). Lo único que se escribió fuera de esa carpeta fueron dos carpetas de corrida en
  `C:\Users\jcifu\MT5Exports\_incoming\XAUUSD\`.
- **1.ª corrida (13 s): `export_failed`**, con el motivo bien reportado: `exporter_exit_1: … copy_rates_range devolvió
  None para XAUUSD/1M … (-2, 'Terminal: Invalid params')`. Las otras 8 TF sí habían salido.
- **2.ª corrida, con el arreglo (16 s): `merged`.** Reloj verificado por **referencias**; `bars_added`:
  1W +1484, 1D +1228, 12H +1329, 4H +1730, 1H +3338, 30M +5693, 15M +10217, 5M +28694, 1M +99999.
- `candles status` mostró XAUUSD `verified / references / merged (20260929T141832)` con las 9 TF.
- **Duración: ~16 s** por símbolo. `AUTO_EXPORT_WAIT_S = 30` y `EXPORT_TIMEOUT_S = 180` (los valores iniciales de la
  spec) sobran; se dejan.
- Quedaron **2 carpetas de corrida** (2,8 MB y 7,8 MB) en `_incoming\XAUUSD\`: no se borraron (ver la pregunta abierta
  sobre la retención de corridas).

## Hallazgos

1. **MT5 rechaza rangos grandes (defecto real, ya arreglado — T22a).** `copy_rates_range` devuelve `None` con
   `Invalid params` cuando el rango implica ~60–70 mil velas o más. Medido en XAUUSD: 1M a 60 días = 57 715 barras (bien) y a 90
   días falla; 5M a 180 días = 34 865 (bien) y a 365 días falla; 15M a 730 días = 47 216 (bien) y a 1500 días falla. El exportador
   pide desde 800 velas antes del análisis más viejo, o sea ~142 días de 1M (~140 mil barras): fallaba **siempre** en 1M, y
   esa falla abortaba el export entero. Arreglo: `split_range()` parte el pedido en tramos de a lo sumo `MAX_BARS_PER_CALL =
   30 000` velas, y una TF que falla se saltea (`SKIPPED_TF:`) sin tirar abajo las demás (RF-15).
2. **El 1M solo llega a ~100 mil velas: empieza el 18 de junio.** El terminal tiene `maxbars = 100000` ("Max bars in
   chart") y no entrega más de eso por TF. Los análisis de XAU empiezan el 18 de mayo, así que ~1 mes no tiene 1M (para eso
   existe la cascada 1M → 5M → 15M del resolvedor; el 5M llega hasta el 5 de mayo). **El banco nunca pierde historia, pero
   tampoco puede recuperar 1M que el terminal no entregó.** Acción tuya: subir ese límite en MT5 antes de crear el banco
   real (ver la lista de tareas manuales).
3. **PowerShell (T20b), verificado con Windows real:** `exit $LASTEXITCODE` conserva el código de salida; las comillas
   simples con `''` entregan bien los argumentos; matar `powershell.exe` desde WSL **no** mata al programa de Windows (por
   eso `WINPID` + `taskkill /F /T`); `cmd.exe` rechaza el directorio actual de WSL (por eso `cwd=/mnt/c`).
4. **Tildes rotas en los mensajes de Windows.** El error del 1M salió con `�` porque el Python de Windows escribía en
   cp1252. Arreglo: `candle_sync` fija `PYTHONIOENCODING=utf-8`; verificado con el Python real (`Válidas` sale bien).
5. **`pandas 3.0.6` en Windows** (más nuevo que el del entorno de los tests) **no dio ningún problema**: las 9 TF se
   exportaron y se convirtieron bien.
6. **El filtro de estación (RF-15c) actuó como se esperaba:** el 1H exportado empieza el 5 de marzo; en el banco empieza el
   8 de marzo (las 23 velas de invierno anteriores al cambio de EE.UU. se descartaron).

## Regla de horario de verano (`BROKER_DST_RULE`)

- **Lo que se midió:** el servidor está en UTC+3 hoy (EE.UU. en horario de verano), coherente con la regla `us`.
- **Prueba de desplazamiento** (perfil de volatilidad intradiario del oro, perfil de cada ventana contra ventanas de control,
  cuánto hay que desplazar el reloj para que calce): control contra control da 0 min con correlación 0,83–0,91 (el método
  funciona). En las ventanas donde EE.UU. y Europa difieren, el resultado es **mezclado y ruidoso** (correlación ~0,6): 27–31
  oct 2025 da 0 min; 9–27 mar 2026 da 0 min contra el control de verano y +60 contra los de invierno. Los jueves (dato de las
  8:30 ET) de esas ventanas caen en el mismo grupo horario que los de control, sin desplazamiento sistemático de 1 h.
- **Conclusión honesta:** los datos son **consistentes con `us`** y no muestran un salto de 1 h, pero no lo prueban. No hay
  fills tuyos en las ventanas que distinguen `us` de `eu` (25 oct – 1 nov y 8 – 29 mar), y desde MT5 no se puede leer el
  calendario de cambios del servidor. **La confirmación definitiva:** correr `calibrate_clock_offset` con fills de la semana del
  25 de octubre al 1 de noviembre de 2026 (ver la lista de tareas manuales).
- **`DST_TRANSITION_HOUR = 2`:** para XAUUSD es irrelevante: en el 1H exportado hay 60 velas en domingo UTC y **ninguna**
  antes de las 21:00 UTC (las 30 semanas abren a las 22:00 UTC), o sea que la hora del cambio cae con el mercado cerrado y no
  se descarta ninguna vela. Para BTCUSD (24/7) sí importaría: 1 hora, dos veces al año.

## Lo que este spike no cubrió

- Los otros 3 símbolos (BTCUSD, USTEC, US500): están visibles y con el mismo offset, pero no se exportaron (eso es la
  creación del banco real, T59). USTEC/US100 tiene 9 referencias y se espera `clock_unverified` hasta llegar a 10.
- El export con MT5 **cerrado** (RF-20e en la práctica): lo cubre la demo T62.
- El offset se detectó sin problemas porque el mercado estaba abierto; con el mercado cerrado el exportador falla a
  propósito (y `candle_sync` lo reporta como `export_failed`).
