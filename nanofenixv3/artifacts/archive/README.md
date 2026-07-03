# Artefactos archivados — NanoFenix v3

Archivados el 2026-07-01 durante la limpieza pre-live. Ninguno estaba
referenciado por código; eran backups por intervalo de sesiones antiguas
(`*_backup.pkl` los regenera `core.py` automáticamente para el runtime activo),
temporales huérfanos de escritura atómica (`.tmp`), snapshots de evaluación
puntuales y runtimes de sesiones paper fechadas ya cerradas.

Regla: los `.pkl` activos viven en `nanofenixv3/` (pretrained_<symbol>.pkl y
runtime_<symbol>*.pkl de la sesión en curso). Todo lo demás se archiva aquí y
puede borrarse cuando lleve >30 días sin usarse.
