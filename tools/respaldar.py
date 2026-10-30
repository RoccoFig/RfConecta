"""Copia de seguridad consistente de la base (aunque la app esté en uso).

    python tools/respaldar.py [carpeta] [cuántas copias conservar]

Usa DB_PATH (o puntos.db). Verifica la copia antes de darla por buena y borra las más viejas.
"""
import glob, os, sqlite3, sys, time

origen = os.environ.get("DB_PATH", "puntos.db")
carpeta = sys.argv[1] if len(sys.argv) > 1 else "respaldos"
conservar = int(sys.argv[2]) if len(sys.argv) > 2 else 14
if not os.path.exists(origen):
    sys.exit(f"No existe la base en {origen}")
os.makedirs(carpeta, exist_ok=True)
destino = os.path.join(carpeta, time.strftime("respaldo-%Y%m%d-%H%M%S.db"))
a, b = sqlite3.connect(origen), sqlite3.connect(destino)
a.backup(b)
a.close()
ok = b.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
b.close()
if not ok:
    os.remove(destino)
    sys.exit("La copia salió dañada y se descartó. Revisá la base original.")
for vieja in sorted(glob.glob(os.path.join(carpeta, "respaldo-*.db")))[:-conservar]:
    os.remove(vieja)
print(f"Respaldo verificado: {destino} ({os.path.getsize(destino) // 1024} KB)")
