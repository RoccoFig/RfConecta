"""Control de acceso, aislamiento entre negocios, CSRF, fuerza bruta, subidas, inyecciones y configuración de producción."""
import base64, io, re
import pytest
from test_canjes import con_puntos, premio

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


# ---------- quién puede entrar adónde ----------
def test_sin_sesion_todo_pide_ingresar(sitio):
    anon = sitio.http()
    for u in ("/plataforma/", "/plataforma/clientes", "/plataforma/seguridad", "/negocio/cafe-brasa/", "/negocio/cafe-brasa/caja", "/negocio/cafe-brasa/qr.png"):
        r = anon.get(u)
        assert r.status_code == 302 and "ingresar" in r.headers["Location"], u


def test_un_dueno_no_entra_a_otro_negocio_ni_a_la_plataforma(sitio):
    sitio.asegurar_claves("cafe-brasa"); sitio.asegurar_claves("helados-copo")
    d = sitio.dueno("cafe-brasa")
    for u in ("/negocio/helados-copo/", "/negocio/helados-copo/clientes", "/negocio/helados-copo/qr.png", "/plataforma/", "/plataforma/respaldo"):
        r = d.get(u)
        assert r.status_code in (302, 405) and (r.status_code == 405 or "ingresar" in r.headers["Location"]), u


def test_un_dueno_no_puede_descargar_el_respaldo_de_toda_la_plataforma(sitio):
    sitio.asegurar_claves("cafe-brasa")
    r = sitio.post(sitio.dueno("cafe-brasa"), "/plataforma/respaldo")
    assert r.status_code == 302 and "ingresar" in r.headers["Location"] and r.mimetype != "application/octet-stream"


def test_la_caja_solo_ve_la_caja(sitio):
    sitio.asegurar_claves("cafe-brasa")
    c = sitio.caja("cafe-brasa")
    assert c.get("/negocio/cafe-brasa/caja").status_code == 200
    for u in ("/negocio/cafe-brasa/", "/negocio/cafe-brasa/clientes", "/negocio/cafe-brasa/premios", "/negocio/cafe-brasa/ajustes", "/negocio/cafe-brasa/actividad", "/negocio/cafe-brasa/qr", "/negocio/cafe-brasa/canjes"):
        assert c.get(u).status_code == 403, u
    cuerpo = sitio.texto(c.get("/negocio/cafe-brasa/caja"))
    assert "Clientes" not in cuerpo.split("<main")[0], "el menú de la caja no debe mostrar secciones del dueño"
    assert sitio.post(c, "/negocio/cafe-brasa/premios", nombre="x", costo="1").status_code == 403


def test_la_plataforma_entra_a_cualquier_negocio_como_soporte(sitio):
    p = sitio.plataforma()
    r = p.get("/negocio/cafe-brasa/")
    assert r.status_code == 200 and "como RF" in sitio.texto(r)


def test_las_direcciones_viejas_redirigen(sitio):
    anon = sitio.http()
    assert anon.get("/admin").status_code == 301 and anon.get("/l/demo/admin").status_code == 301


# ---------- aislamiento de datos (referencias directas a objetos de otro negocio) ----------
def test_un_dueno_no_toca_clientes_canjes_ni_premios_de_otro(sitio):
    u, cid, d = con_puntos(sitio, 200)
    sitio.asegurar_claves("helados-copo")
    otro = sitio.dueno("helados-copo")
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    kid, codigo = sitio.bd("SELECT id, codigo FROM canjes")[0]
    assert otro.get(f"/negocio/helados-copo/clientes/{cid}").status_code == 404
    antes = sitio.saldo(cid)
    sitio.post(otro, f"/negocio/helados-copo/clientes/{cid}/ajustar", puntos="-50", motivo="intento ajeno")
    sitio.post(otro, f"/negocio/helados-copo/canjes/{kid}/cancelar")
    sitio.post(otro, f"/negocio/helados-copo/canjes/validar", codigo=codigo)
    assert sitio.post(otro, f"/negocio/helados-copo/premios/{pid}", nombre="hack", costo="1").status_code == 404
    assert sitio.post(otro, f"/negocio/helados-copo/premios/{pid}/quitar").status_code == 404
    assert sitio.saldo(cid) == antes and sitio.bd("SELECT estado FROM canjes")[0][0] == "pendiente"
    assert sitio.bd("SELECT nombre, activo FROM premios WHERE id=?", pid)[0] == ("Café de filtro gratis", 1)


def test_cada_negocio_ve_solo_sus_clientes_y_actividad(sitio):
    sitio.asegurar_claves("cafe-brasa"); sitio.asegurar_claves("helados-copo")
    sitio.cliente("cafe-brasa", "Cliente Café", "3815550001"); sitio.cliente("helados-copo", "Cliente Helado", "3815550002")
    d = sitio.dueno("cafe-brasa")
    for u in ("/negocio/cafe-brasa/clientes", "/negocio/cafe-brasa/actividad", "/negocio/cafe-brasa/"):
        t = sitio.texto(d.get(u))
        assert "Cliente Helado" not in t and "3815550002" not in t, u
    assert "Cliente Café" in sitio.texto(d.get("/negocio/cafe-brasa/clientes"))


def test_la_sesion_de_cliente_de_un_negocio_no_sirve_en_otro(sitio):
    u = sitio.cliente("cafe-brasa")
    assert "Creá tu cuenta" in sitio.texto(u.get("/l/helados-copo"))
    assert sitio.post(u, "/l/helados-copo/canjear/1", nonce="x").status_code == 403
    assert u.get("/l/helados-copo/visita").status_code == 403


# ---------- CSRF y origen ----------
def test_los_pedidos_que_modifican_exigen_token(sitio):
    anon = sitio.http()
    assert anon.post("/plataforma/ingresar", data={"clave": "x"}).status_code == 403
    assert anon.post("/l/cafe-brasa/registro", data={"nombre": "A", "telefono": "3815550000"}).status_code == 403
    assert anon.post("/l/cafe-brasa/visita/pedir", data={}).status_code == 403
    sitio.asegurar_claves("cafe-brasa")
    d = sitio.dueno("cafe-brasa")
    assert d.post("/negocio/cafe-brasa/premios", data={"nombre": "x", "costo": "5"}).status_code == 403
    assert d.post("/negocio/cafe-brasa/premios", data={"csrf": "falso", "nombre": "x", "costo": "5"}).status_code == 403
    assert sitio.filas("premios") == 9  # no se creó nada


def test_origen_ajeno_rechazado(sitio):
    p = sitio.plataforma()
    r = p.post("/plataforma/negocios/nuevo", data={"csrf": sitio.tok(p), "nombre": "Z"}, headers={"Origin": "https://malo.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("destino", ["//evil.com", "/\\evil.com", "https://evil.com", "javascript:alert(1)", "/\r\nSet-Cookie: x=1", "//evil.com/%2F.."])
def test_sin_redireccion_abierta(sitio, destino):
    c = sitio.http()
    r = sitio.post(c, "/plataforma/ingresar", clave="x", siguiente=destino)
    loc = r.headers["Location"]
    assert "evil" not in loc and "Set-Cookie" not in loc and not loc.startswith("//") and "\n" not in loc


def test_destino_interno_valido_se_respeta(sitio):
    r = sitio.post(sitio.http(), "/plataforma/ingresar", clave="x", siguiente="/plataforma/clientes?q=ana")
    assert r.headers["Location"] == "/plataforma/clientes?q=ana"


# ---------- fuerza bruta y enumeración ----------
def test_ingreso_a_la_plataforma_se_bloquea_tras_cinco_fallos(sitio):
    c = sitio.http()
    for _ in range(5):
        assert sitio.post(c, "/plataforma/ingresar", clave="mala").status_code == 200
    assert sitio.post(c, "/plataforma/ingresar", clave="x").status_code == 429
    assert sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='ingreso_fallido'")[0][0] == 5


def test_ingreso_a_un_negocio_se_bloquea_tras_cinco_fallos(sitio):
    sitio.asegurar_claves("cafe-brasa")
    c = sitio.http()
    for _ in range(5):
        sitio.post(c, "/negocio/cafe-brasa/ingresar", clave="mala")
    assert sitio.post(c, "/negocio/cafe-brasa/ingresar", clave="clave-dueno-1").status_code == 429


def test_el_registro_masivo_se_frena_pero_no_molesta_a_un_local_concurrido(sitio):
    from rf.cliente import MAX_REGISTROS_HORA
    for i in range(MAX_REGISTROS_HORA + 5):
        c = sitio.http(); sitio.post(c, "/l/cafe-brasa/registro", nombre="B", telefono=f"38155{i:05d}")
    assert sitio.filas("clientes") == MAX_REGISTROS_HORA, "un cliente por minuto en promedio pasa sin problema; una avalancha no"


def test_pedir_codigo_de_ingreso_no_revela_si_el_telefono_existe(sitio):
    sitio.cliente("cafe-brasa")
    a = sitio.post(sitio.http(), "/l/cafe-brasa/entrar", telefono="3815550000").headers["Location"]
    b = sitio.post(sitio.http(), "/l/cafe-brasa/entrar", telefono="3815559999").headers["Location"]
    assert re.sub(r"\d{8,}", "N", a) == re.sub(r"\d{8,}", "N", b)


def test_los_codigos_de_ingreso_no_se_escriben_en_los_registros_en_produccion(crear_app, capsys):
    app = crear_app(RENDER="1", MODO_DEV=None)
    c = app.test_client()
    base = "https://rf.ejemplo"
    def post(url, **d):
        with c.session_transaction(base_url=base) as s: s.setdefault("csrf", "t")
        return c.post(url, data={"csrf": "t", **d}, base_url=base)
    post("/l/demo/registro", nombre="Ana", telefono="3815550000")
    capsys.readouterr()
    post("/l/demo/entrar", telefono="3815550000")
    salida = capsys.readouterr().out
    assert not re.search(r"\d{6}", salida) and "no enviado" in salida


def test_en_desarrollo_el_codigo_si_sale_por_consola(sitio, capsys):
    sitio.cliente("cafe-brasa")
    capsys.readouterr()
    sitio.post(sitio.http(), "/l/cafe-brasa/entrar", telefono="3815550000")
    assert re.search(r"es \d{6}", capsys.readouterr().out)


# ---------- sesiones y contraseñas ----------
def test_el_ingreso_renueva_la_sesion_y_salir_la_cierra(sitio):
    c = sitio.http()
    antes = sitio.tok(c)
    sitio.post(c, "/plataforma/ingresar", clave="x")
    assert sitio.tok(c) != antes, "debe haber una sesión nueva tras ingresar (anti fijación de sesión)"
    assert c.get("/plataforma/").status_code == 200
    sitio.post(c, "/plataforma/salir")
    assert c.get("/plataforma/").status_code == 302


def test_las_claves_se_guardan_cifradas(sitio):
    sitio.asegurar_claves("cafe-brasa")
    h, hc = sitio.bd("SELECT clave_hash, clave_caja_hash FROM comercios WHERE slug='cafe-brasa'")[0]
    assert "clave-dueno-1" not in h and "clave-caja-12" not in hc and h.split(":")[0] in ("scrypt", "pbkdf2")


def test_clave_de_caja_y_de_dueno_deben_ser_distintas(sitio):
    sitio.asegurar_claves("cafe-brasa")
    p = sitio.plataforma()
    sitio.post(p, "/plataforma/negocios/cafe-brasa/clave", clave="clave-dueno-1", tipo="caja")
    assert sitio.caja("cafe-brasa", "clave-dueno-1").get("/negocio/cafe-brasa/clientes").status_code == 200, "esa clave sigue siendo la del dueño"
    assert sitio.bd("SELECT clave_caja_hash FROM comercios WHERE slug='cafe-brasa'")[0][0] != sitio.bd("SELECT clave_hash FROM comercios WHERE slug='cafe-brasa'")[0][0]
    assert sitio.caja("cafe-brasa").get("/negocio/cafe-brasa/clientes").status_code == 403


# ---------- ajustes manuales ----------
def test_ajuste_manual_auditado_y_con_limites(sitio):
    u, cid, d = con_puntos(sitio, 100)
    sitio.post(d, f"/negocio/cafe-brasa/clientes/{cid}/ajustar", puntos="-30", motivo="visita cargada dos veces")
    assert sitio.saldo(cid) == 70
    fila = sitio.bd("SELECT delta, motivo, fuente, actor, nota FROM puntos_mov ORDER BY id DESC LIMIT 1")[0]
    assert fila == (-30, "ajuste", "manual", "dueno:cafe-brasa", "visita cargada dos veces")
    assert sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='ajuste_manual'")[0][0] == 2
    sitio.post(d, f"/negocio/cafe-brasa/clientes/{cid}/ajustar", puntos="-500", motivo="demasiado")  # dejaría el saldo negativo
    sitio.post(d, f"/negocio/cafe-brasa/clientes/{cid}/ajustar", puntos="5000", motivo="exagerado")   # supera el tope
    sitio.post(d, f"/negocio/cafe-brasa/clientes/{cid}/ajustar", puntos="10", motivo="")               # sin motivo
    sitio.post(d, f"/negocio/cafe-brasa/clientes/{cid}/ajustar", puntos="0", motivo="nada")
    assert sitio.saldo(cid) == 70


def test_la_caja_no_puede_ajustar_puntos(sitio):
    u, cid, d = con_puntos(sitio, 100)
    caja = sitio.caja("cafe-brasa")
    assert sitio.post(caja, f"/negocio/cafe-brasa/clientes/{cid}/ajustar", puntos="999", motivo="intento").status_code == 403
    assert sitio.saldo(cid) == 100


# ---------- subidas, inyecciones y encabezados ----------
def test_subida_de_logo_solo_acepta_imagenes_reales(sitio):
    p = sitio.plataforma()
    tema = dict(csrf=sitio.tok(p), fondo="#fdf3e3", tinta="#3b2412", acento="#e8a33d", forma="suave", tipografia="clasica", icono="x")
    def subir(contenido, nombre):
        p.post("/plataforma/negocios/cafe-brasa/tema", data={**tema, "logo": (io.BytesIO(contenido), nombre)})
        return sitio.bd("SELECT logo_img IS NOT NULL FROM comercios WHERE slug='cafe-brasa'")[0][0]
    assert subir(b"<svg onload=alert(1)></svg>", "x.svg") == 0
    assert subir(b"<html><script>alert(1)</script>", "x.png") == 0
    assert subir(PNG + b"0" * 500_000, "grande.png") == 0
    assert subir(PNG, "ok.png") == 1
    r = p.get("/l/cafe-brasa/logo")
    assert r.mimetype == "image/png" and r.headers["X-Content-Type-Options"] == "nosniff"


def test_inyeccion_sql_y_xss(sitio):
    sitio.asegurar_claves("cafe-brasa")
    sitio.cliente("cafe-brasa", "<script>alert(1)</script>", "3815550000")
    d, p = sitio.dueno("cafe-brasa"), sitio.plataforma()
    for u in ("/negocio/cafe-brasa/clientes?q=' OR 1=1 --", "/negocio/cafe-brasa/clientes?q=%27;DROP TABLE clientes;--", "/plataforma/actividad?q=\" OR \"1\"=\"1&tipo='x"):
        assert (d if u.startswith("/negocio") else p).get(u).status_code == 200
    assert sitio.filas("clientes") == 1
    for c, u in ((d, "/negocio/cafe-brasa/clientes"), (d, "/negocio/cafe-brasa/"), (p, "/plataforma/clientes"), (p, "/plataforma/")):
        t = sitio.texto(c.get(u))
        assert "<script>alert(1)" not in t, u
    assert "&lt;script&gt;" in sitio.texto(d.get("/negocio/cafe-brasa/clientes"))


def test_encabezados_de_seguridad(sitio):
    r = sitio.http().get("/l/cafe-brasa")
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0] and "frame-ancestors 'self'" in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "Strict-Transport-Security" not in r.headers  # solo bajo HTTPS
    assert "Strict-Transport-Security" in sitio.http().get("/l/cafe-brasa", base_url="https://rf.ejemplo").headers
    assert "no-store" in sitio.plataforma().get("/plataforma/").headers["Cache-Control"]
    for pagina in ("/l/cafe-brasa", "/plataforma/ingresar"):
        assert not re.search(r"<script(?![^>]*\bsrc=)", sitio.texto(sitio.http().get(pagina))), "no debe haber scripts en línea"


def test_no_hay_consola_de_depuracion_ni_errores_con_detalles(sitio):
    c = sitio.http()
    assert c.get("/console").status_code == 404
    t = sitio.texto(c.get("/l/no-existe"))
    assert "Traceback" not in t and "werkzeug" not in t.lower()


def test_salud_no_expone_nada(sitio):
    r = sitio.http().get("/salud")
    assert r.status_code == 200 and r.get_json() == {"ok": True}


def test_produccion_exige_configuracion_segura(crear_app):
    with pytest.raises(RuntimeError):
        crear_app(RENDER="1", SECRET_KEY=None, MODO_DEV=None)
    with pytest.raises(RuntimeError):
        crear_app(RENDER="1", ADMIN_PASSWORD=None, MODO_DEV=None)
    app = crear_app(RENDER="1", MODO_DEV=None)
    assert app.config["SESSION_COOKIE_SECURE"] is True and app.config["SESSION_COOKIE_HTTPONLY"] is True
