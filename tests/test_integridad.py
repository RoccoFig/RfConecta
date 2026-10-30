"""Integridad de datos: reglas que hace cumplir la base, migración desde versiones viejas y control de coherencia."""
import os, sqlite3
import pytest

LEGACY = """
CREATE TABLE comercios(id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, tema TEXT, logo_svg TEXT, tagline TEXT,
  dominio TEXT, clave_hash TEXT, logo_img BLOB, logo_mime TEXT, logo_ver INTEGER, puntos_visita INTEGER, sellos_tarjeta INTEGER,
  premio_tarjeta TEXT, categoria TEXT, creado INTEGER, estado TEXT NOT NULL DEFAULT 'activo');
CREATE TABLE clientes(id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, token TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL,
  telefono TEXT NOT NULL, email TEXT, acepta_whatsapp INTEGER NOT NULL DEFAULT 0, creado INTEGER NOT NULL, UNIQUE(comercio_id, telefono));
CREATE TABLE puntos_mov(id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, delta INTEGER NOT NULL, motivo TEXT NOT NULL, creado INTEGER NOT NULL);
CREATE TABLE premios(id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, nombre TEXT NOT NULL, costo INTEGER NOT NULL, activo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE canjes(id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, premio_id INTEGER NOT NULL, codigo TEXT UNIQUE NOT NULL,
  estado TEXT NOT NULL DEFAULT 'pendiente', creado INTEGER NOT NULL, entregado_en INTEGER);
CREATE TABLE tags(id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, uid TEXT UNIQUE NOT NULL, ultimo_contador INTEGER NOT NULL DEFAULT -1,
  activo INTEGER NOT NULL DEFAULT 1, ultimo_escaneo INTEGER);
CREATE TABLE otp(id INTEGER PRIMARY KEY, comercio_id INTEGER NOT NULL, telefono TEXT NOT NULL, codigo_hash TEXT NOT NULL, expira INTEGER NOT NULL,
  intentos INTEGER NOT NULL DEFAULT 0, usado INTEGER NOT NULL DEFAULT 0, creado INTEGER NOT NULL);
CREATE TABLE intentos_login(id INTEGER PRIMARY KEY, clave TEXT NOT NULL, creado INTEGER NOT NULL);
INSERT INTO comercios(id,slug,nombre,categoria,creado) VALUES(1,'viejo','Negocio Viejo','Kiosco',1700000000);
INSERT INTO clientes VALUES(1,1,'token-viejo-1','Ana','3815550000','',1,1700000100);
INSERT INTO clientes VALUES(2,1,'token-viejo-2','Beto','3815550001','',0,1700000200);
INSERT INTO puntos_mov VALUES(1,1,10,'visita',1700000300),(2,1,10,'visita',1700100300),(3,1,-5,'canje',1700100400),(4,2,10,'visita',1700000400);
INSERT INTO premios VALUES(1,1,'Alfajor',5,1),(2,1,'Gaseosa',50,1);
INSERT INTO canjes(id,cliente_id,premio_id,codigo,estado,creado) VALUES(1,1,1,'AB12CD','pendiente',1700100400),(2,2,1,'EF34AB','usado',1700000500);
"""


def con_(sitio):
    return sqlite3.connect(os.environ["DB_PATH"])


def test_el_historial_no_se_puede_modificar_ni_borrar(sitio):
    sitio.cliente("cafe-brasa")
    con = con_(sitio)
    for sql in ("UPDATE puntos_mov SET delta=9999", "UPDATE puntos_mov SET cliente_id=2", "UPDATE puntos_mov SET creado=0", "DELETE FROM puntos_mov"):
        with pytest.raises(sqlite3.DatabaseError, match="no se puede"):
            con.execute(sql)
    assert con.execute("SELECT delta FROM puntos_mov").fetchone()[0] == 10


def test_la_base_rechaza_saldos_negativos_e_inconsistencias(sitio):
    sitio.cliente("cafe-brasa")
    cid = sitio.cid(); con = con_(sitio)
    ins = "INSERT INTO puntos_mov(cliente_id,comercio_id,delta,motivo,creado) VALUES(?,?,?,?,1)"
    with pytest.raises(sqlite3.DatabaseError, match="Saldo insuficiente"):
        con.execute(ins, (cid, 1, -11, "canje"))
    with pytest.raises(sqlite3.DatabaseError, match="inconsistente"):
        con.execute(ins, (cid, 99, 5, "visita"))  # negocio que no es el del cliente
    with pytest.raises(sqlite3.DatabaseError, match="inconsistente"):
        con.execute("INSERT INTO puntos_mov(cliente_id,delta,motivo,creado) VALUES(?,?,?,1)", (cid, 5, "visita"))  # sin negocio
    assert con.execute("SELECT COALESCE(SUM(delta),0) FROM puntos_mov").fetchone()[0] == 10


def test_una_clave_de_idempotencia_no_se_repite(sitio):
    sitio.cliente("cafe-brasa")
    cid = sitio.cid(); con = con_(sitio)
    ins = "INSERT INTO puntos_mov(cliente_id,comercio_id,delta,motivo,creado,clave_idem) VALUES(?,?,?,?,1,?)"
    comercio = con.execute("SELECT comercio_id FROM clientes WHERE id=?", (cid,)).fetchone()[0]
    con.execute(ins, (cid, comercio, 1, "ajuste", "k1"))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(ins, (cid, comercio, 1, "ajuste", "k1"))


def test_el_control_de_integridad_da_cero_problemas_tras_mucha_actividad(sitio, reloj):
    from rf import motor
    u, cid, d = con_puntos_(sitio)
    for _ in range(5):
        reloj.avanzar(9); u.get("/dev/escanear/cafe-brasa")
    pid = sitio.bd("SELECT id FROM premios WHERE nombre='Café de filtro gratis'")[0][0]
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1"); sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="2")
    kid = sitio.bd("SELECT id FROM canjes WHERE costo>0")[0][0]
    sitio.post(d, f"/negocio/cafe-brasa/canjes/{kid}/cancelar", motivo="prueba")
    with sitio.app.app_context():
        assert motor.verificar_integridad() == []


def test_el_control_detecta_un_saldo_negativo_si_alguien_manipula_la_base(sitio):
    from rf import motor
    sitio.cliente("cafe-brasa")
    con = con_(sitio)
    con.execute("DROP TRIGGER mov_sin_saldo_negativo")  # simula una base alterada por fuera de la aplicación
    com = con.execute("SELECT comercio_id FROM clientes WHERE id=?", (sitio.cid(),)).fetchone()[0]
    con.execute("INSERT INTO puntos_mov(cliente_id,comercio_id,delta,motivo,creado) VALUES(?,?,?,?,1)", (sitio.cid(), com, -500, "x"))
    con.commit()
    with sitio.app.app_context():
        assert any("saldo negativo" in p for p in motor.verificar_integridad())


def con_puntos_(sitio):
    from test_canjes import con_puntos
    return con_puntos(sitio, 500)


def test_el_reloj_de_pruebas_esta_apagado_por_defecto(sitio):
    from rf import db as base
    assert base.DESFASE == 0


def test_migracion_desde_una_base_vieja_conserva_todo(tmp_path, monkeypatch):
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta); con.executescript(LEGACY); con.commit(); con.close()
    for k in ("RENDER", "MODO_DEV", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_PATH", str(ruta)); monkeypatch.setenv("ADMIN_PASSWORD", "x"); monkeypatch.setenv("SECRET_KEY", "k")
    from rf import create_app
    app = create_app()
    con = sqlite3.connect(ruta)
    assert con.execute("SELECT COUNT(*) FROM clientes").fetchone()[0] == 2 and con.execute("SELECT COUNT(*) FROM puntos_mov").fetchone()[0] == 4
    assert con.execute("SELECT SUM(delta) FROM puntos_mov WHERE cliente_id=1").fetchone()[0] == 15, "el saldo de cada cliente no cambia"
    assert con.execute("SELECT COUNT(*) FROM puntos_mov WHERE comercio_id IS NULL OR fuente IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT DISTINCT fuente FROM puntos_mov").fetchall() == [("historico",)]
    assert con.execute("SELECT comercio_id, vence_en>0, costo FROM canjes WHERE id=1").fetchone() == (1, 1, 5)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for trig in ("mov_no_modificar", "mov_no_borrar", "mov_sin_saldo_negativo", "canje_una_sola_vez"):
        assert con.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (trig,)).fetchone(), trig
    con.close()
    c = app.test_client()
    c.set_cookie("ct_viejo", "token-viejo-1")
    assert "Hola, Ana" in c.get("/l/viejo").get_data(as_text=True), "los clientes de antes siguen entrando con su sesión"
    with app.app_context():
        from rf import motor
        assert motor.verificar_integridad() == []
    create_app()  # arrancar de nuevo sobre la base ya migrada no rompe ni duplica nada
    assert sqlite3.connect(ruta).execute("SELECT COUNT(*) FROM puntos_mov").fetchone()[0] == 4
