import os, re, sqlite3, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _entorno(mp, ruta, **extra):
    for k in ("RENDER", "MODO_DEV", "COOLDOWN_HORAS", "PUBLIC_BASE_URL", "MOSTRAR_CODIGOS_EN_REGISTRO"):
        mp.delenv(k, raising=False)
    base = dict(DB_PATH=str(ruta), ADMIN_PASSWORD="x", SECRET_KEY="clave-de-prueba", MODO_DEV="1", PUBLIC_BASE_URL="https://rf.ejemplo",
                SDM_META_KEY="00" * 16, SDM_FILE_KEY="00" * 16)
    base.update(extra)
    for k, v in base.items():
        if v is None:
            mp.delenv(k, raising=False)
        else:
            mp.setenv(k, v)


class Sitio:
    """Ayudas para escribir pruebas cortas. Todas las acciones pasan por HTTP, como lo haría un usuario real."""
    def __init__(self, app):
        self.app = app

    def http(self):
        return self.app.test_client()

    def tok(self, c):
        with c.session_transaction() as s:
            s.setdefault("csrf", "t")
            return s["csrf"]

    def post(self, c, url, **datos):
        return c.post(url, data={"csrf": self.tok(c), **datos})

    def bd(self, sql, *p):
        con = sqlite3.connect(os.environ["DB_PATH"])
        try:
            r = con.execute(sql, p).fetchall()
            con.commit()
            return r
        finally:
            con.close()

    def plataforma(self):
        c = self.http()
        self.post(c, "/plataforma/ingresar", clave="x")
        return c

    def crear_negocio(self, nombre, clave="clave-dueno-1", caja="clave-caja-12"):
        p = self.plataforma()
        self.post(p, "/plataforma/negocios/nuevo", nombre=nombre, categoria="Otro", clave=clave)
        slug = re.sub(r"[^a-z0-9]+", "-", nombre.lower()).strip("-")
        if caja:
            self.post(p, f"/plataforma/negocios/{slug}/clave", clave=caja, tipo="caja")
        return slug

    def dueno(self, slug, clave="clave-dueno-1"):
        c = self.http()
        self.post(c, f"/negocio/{slug}/ingresar", clave=clave)
        return c

    def caja(self, slug, clave="clave-caja-12"):
        return self.dueno(slug, clave)

    def asegurar_claves(self, slug, dueno="clave-dueno-1", caja="clave-caja-12"):
        p = self.plataforma()
        self.post(p, f"/plataforma/negocios/{slug}/clave", clave=dueno)
        self.post(p, f"/plataforma/negocios/{slug}/clave", clave=caja, tipo="caja")

    def cliente(self, slug, nombre="Ana", tel="3815550000", escaneo=True):
        """Registra un cliente. Con escaneo=True entra por el atajo de pruebas y suma su primera visita."""
        c = self.http()
        if escaneo:
            c.get(f"/dev/escanear/{slug}")
        self.post(c, f"/l/{slug}/registro", nombre=nombre, telefono=tel)
        return c

    def crear_clientes(self, slug, cantidad, prefijo="Socio"):
        """Inserta clientes directamente (atajo para probar listados con mucha gente)."""
        com = self.bd("SELECT id FROM comercios WHERE slug=?", slug)[0][0]
        for i in range(cantidad):
            self.bd("INSERT INTO clientes(comercio_id,token,nombre,telefono,email,acepta_whatsapp,creado) VALUES(?,?,?,?,?,?,?)",
                    com, f"tk-{slug}-{prefijo}-{i}", f"{prefijo} {i:02d}", f"38155{i:05d}", "", 0, 1_700_000_000 + i)

    def cid(self, tel="3815550000", slug=None):
        sql = "SELECT id FROM clientes WHERE telefono=?" + (" AND comercio_id=(SELECT id FROM comercios WHERE slug=?)" if slug else "")
        return self.bd(sql, tel, *([slug] if slug else []))[0][0]

    def saldo(self, cid):
        return self.bd("SELECT COALESCE(SUM(delta),0) FROM puntos_mov WHERE cliente_id=?", cid)[0][0]

    def filas(self, tabla="puntos_mov"):
        return self.bd(f"SELECT COUNT(*) FROM {tabla}")[0][0]

    def texto(self, r):
        return r.get_data(as_text=True)


class Reloj:
    def __init__(self):
        from rf import db as base
        self.base = base

    def avanzar(self, horas):
        self.base.DESFASE += int(horas * 3600)


@pytest.fixture
def app(tmp_path, monkeypatch):
    _entorno(monkeypatch, tmp_path / "prueba.db")
    from rf import create_app, db as base
    base.DESFASE = 0
    a = create_app()
    a.config["TESTING"] = True
    yield a
    base.DESFASE = 0


@pytest.fixture
def sitio(app):
    return Sitio(app)


@pytest.fixture
def reloj(app):
    return Reloj()


@pytest.fixture
def crear_app(tmp_path, monkeypatch):
    """Para pruebas que necesitan otra configuración (producción, sin modo de pruebas, etc.)."""
    def _crear(**extra):
        _entorno(monkeypatch, tmp_path / "otra.db", **extra)
        from rf import create_app
        return create_app()
    return _crear
