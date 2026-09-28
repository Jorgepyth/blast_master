# Constitución — blast_master

Principios innegociables. Toda spec, plan y tarea debe cumplirlos. Aprobada por el usuario el 2026-09-25.

1. **Stack**: Python del entorno conda `blast_master`; dependencias solo las de `requirements.txt`. No se agrega ninguna sin preguntar. `MetaTrader5` nunca entra en `requirements.txt`.
2. **La spec manda**: nada se implementa si no está en la spec activa. Si falta una decisión, se detiene el trabajo y se pregunta.
3. **Lógica separada de la interfaz**: el código nuevo de cálculo (`core/`, `tools/`) no importa `InquirerPy` ni `rich` y se testea sin consola; `cli/` solo pregunta y muestra.
4. **Tests como puerta**: cada tarea termina con la suite en verde y la salida mostrada. Los tests no tocan `.data/`, `/mnt/c`, MT5 ni la red (solo `:memory:`, `tmp_path` y fixtures), y se corren en un worktree sin `.data/` ni `.env` reales.
5. **Datos reales**: una SQLite por cuenta en `.data/`. Los cambios de esquema son solo aditivos, vía `init_db`. Toda escritura en una DB real pasa por copia de prueba, backup y aprobación explícita. El código de análisis lee con `mode=ro` y columnas explícitas.
6. **Idioma**: todo lo que el sistema muestra o guarda va en inglés: código, identificadores, mensajes del CLI, reportes que genera, códigos guardados en la base y claves de configuración. Las specs, la documentación, los cuadernos y la comunicación van en español. (Actualizado el 2026-09-27, spec 002 F3 [4][1].)
7. **Git y archivos protegidos**: commits solo cuando el usuario lo pide, con la lista explícita de archivos (nunca `git add -A`/`.`/`commit -a`). `CLAUDE.md`/`AGENTS.md` no se editan sin pedido explícito.
8. **Nivel de compromiso**: `spec-anchored`, o sea que la spec se mantiene sincronizada con el código.
