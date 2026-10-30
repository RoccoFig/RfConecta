"""Los paneles: que cada pantalla cargue, que los números salgan de la base y que filtros, vacíos y respaldo funcionen."""
import re
import sqlite3
import pytest
from test_canjes import con_puntos, premio


PLATAFORMA = ["/plataforma/", "/plataforma/negocios", "/plataforma/negocios/nuevo", "/plataforma/clientes", "/plataforma/actividad", "/plataforma/canjes",
              "/plataforma/carteles", "/plataforma/seguridad", "/plataforma/ajustes"] + [f"/plataforma/negocios/cafe-brasa?tab={t}" for t in ("resumen", "configuracion", "diseno", "carteles")]
NEGOCIO = ["", "caja", "clientes", "actividad", "premios", "canjes", "carteles", "qr", "qr/imprimible", "ajustes", "ajustes?tab=cuenta"]


def test_todas_las_pantallas_de_la_plataforma_cargan(sitio):
    p = sitio.plataforma()
    for u in PLATAFORMA:
        assert p.get(u).status_code == 200, u


def test_todas_las_pantallas_del_negocio_cargan_con_y_sin_datos(sitio):
    sitio.asegurar_claves("cafe-brasa"); sitio.asegurar_claves("helados-copo")
    sitio.cliente("cafe-brasa")
    for slug in ("cafe-brasa", "helados-copo"):  # el segundo no tiene ningún dato
        d = sitio.dueno(slug)
        for u in NEGOCIO:
            assert d.get(f"/negocio/{slug}/{u}").status_code == 200, (slug, u)
    d = sitio.dueno("cafe-brasa")
    assert d.get(f"/negocio/cafe-brasa/clientes/{sitio.cid()}").status_code == 200


def test_los_numeros_del_resumen_salen_de_la_base(sitio, reloj):
    u = sitio.cliente("cafe-brasa"); sitio.cliente("helados-copo", tel="3815550001")
    reloj.avanzar(9); u.get("/dev/escanear/cafe-brasa")
    t = sitio.texto(sitio.plataforma().get("/plataforma/"))
    clientes = sitio.filas("clientes"); visitas = sitio.bd("SELECT COUNT(*) FROM puntos_mov WHERE motivo='visita'")[0][0]
    assert re.search(rf'Clientes</div><div class="v">{clientes}<', t) and re.search(rf'Visitas en 7 días</div><div class="v">{visitas}<', t)
    from rf import motor
    with sitio.app.app_context():
        et, val = motor.serie_visitas(30)
    assert sum(val) == visitas and len(val) == 30


def test_el_saldo_mostrado_coincide_con_el_historial(sitio, reloj):
    u = sitio.cliente("cafe-brasa")
    for _ in range(3):
        reloj.avanzar(9); u.get("/dev/escanear/cafe-brasa")
    esperado = sitio.saldo(sitio.cid())
    assert esperado == 40 and f'<div class="puntos">{esperado}</div>' in sitio.texto(u.get("/l/cafe-brasa"))
    sitio.asegurar_claves("cafe-brasa")
    ficha = sitio.texto(sitio.dueno("cafe-brasa").get(f"/negocio/cafe-brasa/clientes/{sitio.cid()}"))
    assert re.search(rf'Puntos</div><div class="v">{esperado}<', ficha)


def test_busqueda_filtros_y_paginacion(sitio):
    p = sitio.plataforma()
    sitio.crear_clientes("cafe-brasa", 18)
    sitio.cliente("helados-copo", "Otra Persona", "3815550999")
    assert "Página 1 de 2" in sitio.texto(p.get("/plataforma/clientes?negocio=cafe-brasa"))
    assert "Socio" in sitio.texto(p.get("/plataforma/clientes?negocio=cafe-brasa&pagina=2"))
    t = sitio.texto(p.get("/plataforma/clientes?q=Socio 07"))
    assert "Socio 07" in t and "Socio 03" not in t and "Otra Persona" not in t
    assert "Otra Persona" in sitio.texto(p.get("/plataforma/clientes?negocio=helados-copo"))
    assert "se registró" in sitio.texto(p.get("/plataforma/actividad?tipo=registro")) and "sumó una visita" not in sitio.texto(p.get("/plataforma/actividad?tipo=registro"))
    assert "Página 1 de 1" in sitio.texto(p.get("/plataforma/clientes?q=zzzz-no-existe")) or "Sin clientes" in sitio.texto(p.get("/plataforma/clientes?q=zzzz-no-existe"))
    assert p.get("/plataforma/clientes?pagina=999").status_code == 200 and p.get("/plataforma/clientes?pagina=-4&orden=hack").status_code == 200


def test_estados_vacios_con_mensajes_claros(sitio):
    sitio.asegurar_claves("cafe-brasa"); d = sitio.dueno("cafe-brasa")
    assert "Sin clientes" in sitio.texto(d.get("/negocio/cafe-brasa/clientes"))
    assert "Sin actividad" in sitio.texto(d.get("/negocio/cafe-brasa/actividad"))
    assert "Sin canjes" in sitio.texto(d.get("/negocio/cafe-brasa/canjes"))
    assert "Todavía no hay carteles" in sitio.texto(d.get("/negocio/cafe-brasa/carteles")) or "Sin carteles" in sitio.texto(d.get("/negocio/cafe-brasa/carteles"))
    assert "Esperando un código" in sitio.texto(sitio.caja("cafe-brasa").get("/negocio/cafe-brasa/caja"))


def test_crear_un_negocio_nuevo_y_entrar_a_su_panel(sitio):
    slug = sitio.crear_negocio("Gimnasio Fuerza")
    assert slug == "gimnasio-fuerza"
    d = sitio.dueno(slug)
    assert d.get(f"/negocio/{slug}/").status_code == 200 and "Gimnasio Fuerza" in sitio.texto(d.get(f"/negocio/{slug}/"))
    assert sitio.http().get(f"/l/{slug}").status_code == 200
    p = sitio.plataforma()
    sitio.post(p, "/plataforma/negocios/nuevo", nombre="Gimnasio Fuerza", clave="otra-clave-123")
    sitio.post(p, "/plataforma/negocios/nuevo", nombre="Corto", clave="123")
    assert sitio.bd("SELECT COUNT(*) FROM comercios WHERE slug IN ('gimnasio-fuerza','corto')")[0][0] == 1


def test_reglas_invalidas_se_rechazan(sitio):
    sitio.asegurar_claves("cafe-brasa"); d = sitio.dueno("cafe-brasa")
    validas = dict(puntos_visita="10", sellos_tarjeta="8", premio_tarjeta="Premio", intervalo_horas="8", max_visitas_dia="0", canje_vence_dias="30")
    for campo, valor in (("puntos_visita", "0"), ("sellos_tarjeta", "1"), ("sellos_tarjeta", "99"), ("intervalo_horas", "0"), ("intervalo_horas", "9999"),
                         ("max_visitas_dia", "-1"), ("canje_vence_dias", "0"), ("premio_tarjeta", " "), ("puntos_visita", "abc")):
        sitio.post(d, "/negocio/cafe-brasa/ajustes/reglas", **{**validas, campo: valor})
        assert sitio.bd("SELECT puntos_visita FROM comercios WHERE slug='cafe-brasa'")[0][0] is None, (campo, valor)
    sitio.post(d, "/negocio/cafe-brasa/ajustes/reglas", **validas)
    assert sitio.bd("SELECT puntos_visita, intervalo_horas FROM comercios WHERE slug='cafe-brasa'")[0] == (10, 8)


def test_cambiar_las_reglas_no_toca_saldos_ni_canjes_existentes(sitio):
    u, cid, d = con_puntos(sitio, 100)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    antes = (sitio.saldo(cid), sitio.bd("SELECT codigo, costo, vence_en FROM canjes")[0])
    sitio.post(d, "/negocio/cafe-brasa/ajustes/reglas", puntos_visita="50", sellos_tarjeta="3", premio_tarjeta="Nuevo", intervalo_horas="24", max_visitas_dia="1", canje_vence_dias="1")
    sitio.post(d, f"/negocio/cafe-brasa/premios/{pid}", nombre="Café doble", costo="500")
    assert (sitio.saldo(cid), sitio.bd("SELECT codigo, costo, vence_en FROM canjes")[0]) == antes


def test_el_respaldo_es_una_base_valida_y_completa(sitio, tmp_path):
    sitio.cliente("cafe-brasa")
    r = sitio.post(sitio.plataforma(), "/plataforma/respaldo")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    archivo = tmp_path / "copia.db"; archivo.write_bytes(r.data)
    con = sqlite3.connect(archivo)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for tabla in ("clientes", "puntos_mov", "canjes", "comercios"):
        assert con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == sitio.filas(tabla)
    assert sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='respaldo_descargado'")[0][0] == 1


def test_la_pantalla_de_seguridad_muestra_los_intentos_rechazados(sitio, reloj):
    registrar = sitio.cliente("cafe-brasa")
    for _ in range(6):
        registrar.get("/dev/escanear/cafe-brasa")  # fuera del intervalo: se rechazan
    sitio.post(sitio.http(), "/plataforma/ingresar", clave="mala")
    t = sitio.texto(sitio.plataforma().get("/plataforma/seguridad"))
    assert "visita_rechazada" in t and "ingreso_fallido" in t and "Para revisar" in t
    assert "cliente:" in t and "intentos fuera de regla" in t


def test_el_diseno_a_medida_sigue_funcionando_en_los_dos_negocios_de_ejemplo(sitio):
    a, b = sitio.cliente("cafe-brasa"), sitio.cliente("helados-copo")
    assert "cb-tazas" in sitio.texto(a.get("/l/cafe-brasa")) and "hc-fila" in sitio.texto(b.get("/l/helados-copo"))
    assert "Tu tarjeta de sellos" in sitio.texto(sitio.cliente("demo").get("/l/demo"))
