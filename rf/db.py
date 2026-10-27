"""Base de datos: conexión, esquema y migraciones. Las migraciones solo agregan; nunca borran datos."""
import os, sqlite3, time
from flask import g

DB_PATH = os.environ.get("DB_PATH", "puntos.db")

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
CREATE INDEX IF NOT EXISTS ix_mov_cliente ON puntos_mov(cliente_id, motivo, creado);
CREATE INDEX IF NOT EXISTS ix_clientes_com ON clientes(comercio_id, creado);
CREATE INDEX IF NOT EXISTS ix_login ON intentos_login(clave, creado);
"""

# columnas que se agregan a tablas que ya existían en versiones anteriores
COLUMNAS_NUEVAS = {
    "comercios": {"tema": "TEXT", "logo_svg": "TEXT", "tagline": "TEXT", "dominio": "TEXT", "clave_hash": "TEXT",
                  "logo_img": "BLOB", "logo_mime": "TEXT", "logo_ver": "INTEGER", "puntos_visita": "INTEGER",
                  "sellos_tarjeta": "INTEGER", "premio_tarjeta": "TEXT", "categoria": "TEXT", "creado": "INTEGER",
                  "estado": "TEXT NOT NULL DEFAULT 'activo'"},
    "tags": {"ultimo_escaneo": "INTEGER"},
    "canjes": {"entregado_en": "INTEGER"},
}

# columnas de un negocio sin el logo (que pesa); "tiene_logo" avisa si hay uno subido
COLS_COM = ("id,slug,nombre,tagline,tema,logo_svg,logo_ver,dominio,clave_hash,puntos_visita,sellos_tarjeta,"
            "premio_tarjeta,categoria,creado,estado,(logo_img IS NOT NULL) AS tiene_logo")


def ahora():
    return int(time.time())


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


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


def init_db(sembrar=True):
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    migrar(c)
    if sembrar:
        from .demos import sembrar_demos
        sembrar_demos(c)
    c.commit()
    c.close()
