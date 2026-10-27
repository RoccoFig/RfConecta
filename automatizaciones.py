"""Mensajes automáticos por WhatsApp. Se ejecuta cada tanto (cron o tarea programada):

    python automatizaciones.py            # respeta el horario de envío
    python automatizaciones.py --forzar   # ignora el horario (para probar)
"""
import os, sys, time, sqlite3
from datetime import datetime, timezone, timedelta

import app as a

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
DIAS_INACTIVO = 30       # sin visitas hace más de X días
DIAS_ENTRE_AVISOS = 30   # no repetir el aviso de inactivo antes de X días
HORA_DESDE, HORA_HASTA = 9, 21  # horario de envío (Argentina, UTC-3)


def hora_argentina():
    return datetime.now(timezone(timedelta(hours=-3))).hour


def main(forzar=False):
    if not forzar and not (HORA_DESDE <= hora_argentina() < HORA_HASTA):
        print("Fuera de horario de envío. No se manda nada.")
        return 0

    con = sqlite3.connect(a.DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS mensajes(
        id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, tipo TEXT NOT NULL, creado INTEGER NOT NULL)""")
    ahora = int(time.time())
    total = 0

    def enviar(cli, tipo, texto):
        a.enviar_whatsapp(cli["telefono"], texto)
        con.execute("INSERT INTO mensajes(cliente_id,tipo,creado) VALUES(?,?,?)", (cli["id"], tipo, ahora))
        con.commit()

    # 1) Bienvenida: una sola vez, solo a quien aceptó recibir mensajes
    pendientes = con.execute("""
        SELECT cl.*, co.nombre AS local, co.slug FROM clientes cl
        JOIN comercios co ON co.id = cl.comercio_id
        WHERE cl.acepta_whatsapp = 1
          AND NOT EXISTS (SELECT 1 FROM mensajes m WHERE m.cliente_id = cl.id AND m.tipo = 'bienvenida')
    """).fetchall()
    for c in pendientes:
        enviar(c, "bienvenida",
               f"¡Hola {c['nombre']}! Gracias por sumarte a {c['local']}. "
               f"Mirá tus puntos y premios acá: {BASE_URL}/l/{c['slug']}")
        total += 1

    # 2) Clientes que hace tiempo no vienen
    limite_visita = ahora - DIAS_INACTIVO * 86400
    limite_aviso = ahora - DIAS_ENTRE_AVISOS * 86400
    inactivos = con.execute("""
        SELECT cl.*, co.nombre AS local, co.slug FROM clientes cl
        JOIN comercios co ON co.id = cl.comercio_id
        WHERE cl.acepta_whatsapp = 1
          AND COALESCE((SELECT MAX(creado) FROM puntos_mov WHERE cliente_id = cl.id AND motivo = 'visita'), cl.creado) < ?
          AND NOT EXISTS (SELECT 1 FROM mensajes m WHERE m.cliente_id = cl.id
                          AND m.tipo = 'inactivo' AND m.creado > ?)
    """, (limite_visita, limite_aviso)).fetchall()
    for c in inactivos:
        enviar(c, "inactivo",
               f"{c['nombre']}, hace tiempo que no te vemos por {c['local']}. "
               f"Tus puntos te esperan: {BASE_URL}/l/{c['slug']}")
        total += 1

    print(f"Mensajes enviados: {total}")
    return total


if __name__ == "__main__":
    main(forzar="--forzar" in sys.argv)
