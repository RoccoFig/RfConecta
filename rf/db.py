"""Base de datos: conexión, esquema y migraciones. Las migraciones solo agregan; nunca borran datos."""
import os, sqlite3, time
from contextlib import contextmanager
from flask import g

DB_PATH = os.environ.get("DB_PATH", "puntos.db")  # solo para quienes importan el valor; el programa usa ruta()
DESFASE = 0  # segundos de adelanto del reloj: SOLO lo modifican las pruebas automáticas


def ruta():
    return os.environ.get("DB_PATH", "puntos.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS comercios(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clientes(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, token TEXT UNIQUE NOT NULL,
  nombre TEXT NOT NULL, telefono TEXT NOT NULL, email TEXT,
  acepta_whatsapp INTEGER NOT NULL DEFAULT 0, creado INTEGER NOT NULL,
  UNIQUE(comercio_id, telefono));
CREATE TABLE IF NOT EXISTS puntos_mov(
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, delta INTEGER NOT NULL,
  motivo TEXT NOT NULL, creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS premios(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, nombre TEXT NOT NULL,
  costo INTEGER NOT NULL, activo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS canjes(
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, premio_id INTEGER NOT NULL,
  codigo TEXT UNIQUE NOT NULL, estado TEXT NOT NULL DEFAULT 'pendiente', creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tags(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, uid TEXT UNIQUE NOT NULL,
  ultimo_contador INTEGER NOT NULL DEFAULT -1, activo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS otp(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, telefono TEXT NOT NULL,
  codigo_hash TEXT NOT NULL, expira INTEGER NOT NULL, intentos INTEGER NOT NULL DEFAULT 0,
  usado INTEGER NOT NULL DEFAULT 0, creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS intentos_login(
  id INTEGER PRIMARY KEY, clave TEXT NOT NULL, creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS solicitudes_visita(
  id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, cliente_id INTEGER NOT NULL, codigo TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'pendiente', creado INTEGER NOT NULL, vence_en INTEGER NOT NULL,
  resuelto_en INTEGER, resuelto_por TEXT, motivo TEXT);
CREATE TABLE IF NOT EXISTS auditoria(
  id INTEGER PRIMARY KEY, t INTEGER NOT NULL, nivel TEXT NOT NULL DEFAULT 'info', actor TEXT, accion TEXT NOT NULL,
  comercio_id INTEGER, objetivo TEXT, detalle TEXT, ip TEXT);
CREATE INDEX IF NOT EXISTS ix_mov_cliente ON puntos_mov(cliente_id, motivo, creado);
CREATE UNIQUE INDEX IF NOT EXISTS ux_solicitud_codigo ON solicitudes_visita(codigo) WHERE estado='pendiente';
CREATE INDEX IF NOT EXISTS ix_audit_t ON auditoria(t);
CREATE INDEX IF NOT EXISTS ix_audit_accion ON auditoria(accion, t);
CREATE INDEX IF NOT EXISTS ix_clientes_com ON clientes(comercio_id, creado);
CREATE INDEX IF NOT EXISTS ix_login ON intentos_login(clave, creado);
"""

# columnas que se agregan a tablas que ya existían en versiones anteriores
COLUMNAS_NUEVAS = {
    "comercios": {"tema": "TEXT", "logo_svg": "TEXT", "tagline": "TEXT", "dominio": "TEXT", "clave_hash": "TEXT",
                  "logo_img": "BLOB", "logo_mime": "TEXT", "logo_ver": "INTEGER", "puntos_visita": "INTEGER",
                  "sellos_tarjeta": "INTEGER", "premio_tarjeta": "TEXT", "categoria": "TEXT", "creado": "INTEGER",
                  "estado": "TEXT NOT NULL DEFAULT 'activo'", "intervalo_horas": "REAL", "max_visitas_dia": "INTEGER",
                  "clave_caja_hash": "TEXT", "canje_vence_dias": "INTEGER"},
    "tags": {"ultimo_escaneo": "INTEGER"},
    "canjes": {"entregado_en": "INTEGER", "comercio_id": "INTEGER", "vence_en": "INTEGER", "costo": "INTEGER NOT NULL DEFAULT 0",
               "resuelto_por": "TEXT"},
    "puntos_mov": {"comercio_id": "INTEGER", "fuente": "TEXT", "actor": "TEXT", "clave_idem": "TEXT", "ref": "TEXT", "nota": "TEXT"},
}

# Reglas que la base de datos hace cumplir aunque el programa tuviera un error
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS mov_no_modificar BEFORE UPDATE OF cliente_id, delta, motivo, creado ON puntos_mov
BEGIN SELECT RAISE(ABORT, 'El historial de puntos no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS mov_no_borrar BEFORE DELETE ON puntos_mov
BEGIN SELECT RAISE(ABORT, 'El historial de puntos no se puede borrar'); END;
CREATE TRIGGER IF NOT EXISTS mov_coherente BEFORE INSERT ON puntos_mov
WHEN NEW.comercio_id IS NULL OR NOT EXISTS (SELECT 1 FROM clientes WHERE id=NEW.cliente_id AND comercio_id=NEW.comercio_id)
BEGIN SELECT RAISE(ABORT, 'Movimiento inconsistente con el cliente o el negocio'); END;
CREATE TRIGGER IF NOT EXISTS mov_sin_saldo_negativo BEFORE INSERT ON puntos_mov
WHEN NEW.delta < 0 AND (SELECT COALESCE(SUM(delta),0) FROM puntos_mov WHERE cliente_id=NEW.cliente_id) + NEW.delta < 0
BEGIN SELECT RAISE(ABORT, 'Saldo insuficiente'); END;
CREATE TRIGGER IF NOT EXISTS canje_una_sola_vez BEFORE UPDATE OF estado ON canjes WHEN OLD.estado != 'pendiente'
BEGIN SELECT RAISE(ABORT, 'El canje ya fue resuelto'); END;
"""

# columnas de un negocio sin el logo (que pesa); "tiene_logo" avisa si hay uno subido
COLS_COM = ("id,slug,nombre,tagline,tema,logo_svg,logo_ver,dominio,clave_hash,puntos_visita,sellos_tarjeta,"
            "premio_tarjeta,categoria,creado,estado,clave_caja_hash,intervalo_horas,max_visitas_dia,canje_vence_dias,"
            "(logo_img IS NOT NULL) AS tiene_logo")


def ahora():
    return int(time.time()) + DESFASE


def db():
    if "db" not in g:
        g.db = sqlite3.connect(ruta(), timeout=15)
        g.db.row_factory = sqlite3.Row
    return g.db


@contextmanager
def transaccion():
    """Bloque de escritura todo-o-nada. Toma el candado de escritura al empezar, así dos pedidos simultáneos
    no pueden decidir con datos viejos. Si ya hay una transacción abierta, se suma a ella."""
    con = db()
    if g.get("en_tx"):
        yield con
        return
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    g.en_tx = True
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        g.en_tx = False


def cerrar(_=None):
    c = g.pop("db", None)
    if c:
        c.close()


def migrar(c):
    for tabla, cols in COLUMNAS_NUEVAS.items():
        existentes = {r[1] for r in c.execute(f"PRAGMA table_info({tabla})")}
        for col, tipo in cols.items():
            if col not in existentes:
                c.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_comercios_dominio ON comercios(dominio)")
    c.execute("UPDATE comercios SET creado=? WHERE creado IS NULL", (ahora(),))
    # datos de versiones anteriores: se completan las columnas nuevas sin tocar lo que ya existía
    c.execute("""UPDATE puntos_mov SET comercio_id=(SELECT comercio_id FROM clientes WHERE id=puntos_mov.cliente_id),
                 fuente=COALESCE(fuente,'historico'), actor=COALESCE(actor,'historico') WHERE comercio_id IS NULL""")
    c.execute("""UPDATE canjes SET comercio_id=(SELECT comercio_id FROM clientes WHERE id=canjes.cliente_id) WHERE comercio_id IS NULL""")
    c.execute("UPDATE canjes SET vence_en=creado + 30*86400 WHERE vence_en IS NULL")
    c.execute("UPDATE canjes SET entregado_en=creado WHERE estado='usado' AND entregado_en IS NULL")  # versiones viejas no guardaban la fecha
    c.execute("UPDATE canjes SET costo=COALESCE((SELECT costo FROM premios WHERE id=canjes.premio_id),0) WHERE costo=0 AND comercio_id IS NOT NULL AND estado!='cancelado'")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_mov_idem ON puntos_mov(clave_idem) WHERE clave_idem IS NOT NULL")
    c.execute("CREATE INDEX IF NOT EXISTS ix_canjes_com ON canjes(comercio_id, estado)")


def init_db(sembrar=True):
    c = sqlite3.connect(ruta())
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    migrar(c)
    c.executescript(TRIGGERS)
    if sembrar:
        from .demos import sembrar_demos
        sembrar_demos(c)
    c.commit()
    c.close()
