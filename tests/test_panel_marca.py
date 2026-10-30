"""El panel del negocio (dueño y caja) lleva la identidad del negocio de la SESIÓN; la plataforma conserva la suya."""
import io, re

CAFE = dict(primario="#5b3a29", acento="#d08c4f", fondo="#f6efe6", sup="#fffaf4", tinta="#2b1a10", tipografia="elegante", forma="recta")
GYM = dict(primario="#111111", acento="#c6ff00", boton="#c6ff00", fondo="#1a1a1a", sup="#242424", tinta="#f5f5f5", tipografia="impacto")


def css(t):
    m = re.search(r"<style>(:root\{[^<]*\})</style>", t)
    return m.group(1) if m else None


def var(t, nombre):
    m = re.search(rf"--{nombre}:([^;}}]+)", css(t) or "")
    return m.group(1) if m else None


def negocios(sitio):
    """prueba: diseño a medida (carpeta propia con logo y panel.css) · club-cafe: tema del editor · sin-marca: nada."""
    p = sitio.plataforma()
    slugs = [sitio.crear_negocio(n) for n in ("Prueba", "Club Cafe", "Sin Marca", "Gym Titan")]
    assert slugs == ["prueba", "club-cafe", "sin-marca", "gym-titan"]
    sitio.post(p, "/plataforma/negocios/club-cafe/tema", **CAFE)
    sitio.post(p, "/plataforma/negocios/gym-titan/tema", **GYM)
    return p


def test_dueno_y_caja_ven_la_identidad_de_su_negocio(sitio):
    negocios(sitio)
    dueno, caja = sitio.dueno("club-cafe"), sitio.caja("club-cafe")
    td = sitio.texto(dueno.get("/negocio/club-cafe/"))
    tc = sitio.texto(caja.get("/negocio/club-cafe/caja"))
    assert css(td) == css(tc), "dueño y caja ven la misma marca"
    assert var(td, "marca-texto") == CAFE["primario"] and "Palatino" in var(td, "titulo-marca") and var(td, "r") == "6px"
    assert 'data-negocio="club-cafe"' in td and 'data-nivel="dueno"' in td and 'data-nivel="caja"' in tc
    assert "Estás atendiendo en" in tc and "Club Cafe" in tc
    assert "Programa de lealtad" in td and "<small>Caja</small>" in tc
    assert "disenos/prueba" not in td + tc


def test_diseno_a_medida_suma_su_logo_y_su_hoja_del_panel(sitio):
    negocios(sitio)
    for c in (sitio.dueno("prueba"), sitio.caja("prueba")):
        t = sitio.texto(c.get("/negocio/prueba/caja"))
        assert "/static/disenos/prueba/panel.css" in t and "/static/disenos/prueba/logo.png" in t
    t = sitio.texto(sitio.dueno("club-cafe").get("/negocio/club-cafe/"))
    assert "panel.css" not in t and "disenos/prueba" not in t
    # un logo subido desde el editor tiene prioridad, igual que en las pantallas del cliente
    p = sitio.plataforma()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    p.post("/plataforma/negocios/prueba/tema", data={"csrf": sitio.tok(p), "logo": (io.BytesIO(png), "l.png")})
    t = sitio.texto(sitio.dueno("prueba").get("/negocio/prueba/"))
    assert "/l/prueba/logo" in t and "disenos/prueba/logo.png" not in t


def test_negocio_sin_marca_usa_valores_por_defecto_legibles(sitio):
    negocios(sitio)
    t = sitio.texto(sitio.dueno("sin-marca").get("/negocio/sin-marca/"))
    from rf import temas
    assert css(t) == str(temas.css_panel(temas.normalizar({}))), "tema por defecto"
    assert "panel.css" not in t and 'class="avatar"' in t and ">SM<" in t, "sin logo: iniciales"


def test_un_negocio_de_pagina_oscura_tiene_un_panel_claro(sitio):
    negocios(sitio)
    t = sitio.texto(sitio.dueno("gym-titan").get("/negocio/gym-titan/"))
    from rf import temas
    assert temas.contraste(var(t, "text"), var(t, "paper")) >= 7 and temas.contraste(var(t, "text"), "#ffffff") >= 7
    assert temas.contraste(var(t, "marca-ink"), var(t, "marca")) >= 4.5, "el botón principal se lee"
    assert temas.contraste(var(t, "muted"), "#ffffff") >= 4.5


def test_la_marca_sale_de_la_sesion_y_nunca_de_otro_negocio(sitio):
    negocios(sitio)
    dueno, caja = sitio.dueno("club-cafe"), sitio.caja("club-cafe")
    for c, url in ((dueno, "/negocio/prueba/"), (dueno, "/negocio/prueba/caja"), (caja, "/negocio/prueba/caja"), (caja, "/negocio/sin-marca/caja")):
        r = c.get(url)
        assert r.status_code == 302 and "ingresar" in r.headers["Location"], url
    # parámetros o campos que nombran a otro negocio no cambian nada
    t = sitio.texto(caja.get("/negocio/club-cafe/caja?negocio=prueba&slug=prueba&comercio_id=1"))
    assert 'data-negocio="club-cafe"' in t and "disenos/prueba" not in t and var(t, "marca-texto") == CAFE["primario"]
    # la caja sigue sin poder entrar a pantallas del dueño
    assert caja.get("/negocio/club-cafe/").status_code == 403 and caja.get("/negocio/club-cafe/diseno").status_code == 403


def test_la_plataforma_conserva_su_interfaz_y_cambia_de_marca_al_dar_soporte(sitio):
    p = negocios(sitio)
    for url in ("/plataforma/", "/plataforma/negocios", "/plataforma/negocios/prueba?tab=diseno", "/plataforma/canjes"):
        t = sitio.texto(p.get(url))
        assert "data-negocio" not in t and "--marca-texto" not in t and "disenos/prueba/panel.css?v=" not in t, url
    a = sitio.texto(p.get("/negocio/prueba/caja"))
    b = sitio.texto(p.get("/negocio/club-cafe/caja"))
    c = sitio.texto(p.get("/plataforma/"))
    assert "disenos/prueba/panel.css?v=" in a and "Estás viendo el panel de Prueba como RF" in a
    assert "disenos/prueba" not in b and var(b, "marca-texto") == CAFE["primario"] and "Estás viendo el panel de Club Cafe" in b
    assert "data-negocio" not in c, "volver a la plataforma no arrastra la marca del último negocio"


def test_pantallas_de_ingreso(sitio):
    negocios(sitio)
    t = sitio.texto(sitio.http().get("/negocio/prueba/ingresar"))
    assert "disenos/prueba/panel.css" in t and "disenos/prueba/logo.png" in t and 'data-negocio="prueba"' in t
    t = sitio.texto(sitio.http().get("/negocio/club-cafe/ingresar"))
    assert var(t, "marca-texto") == CAFE["primario"] and "disenos/prueba" not in t
    assert "clave_hash" not in t and "pbkdf2" not in t and "scrypt" not in t
    t = sitio.texto(sitio.http().get("/plataforma/ingresar"))
    assert "data-negocio" not in t and "--marca-texto" not in t and ">RF<" in t


def test_el_panel_sigue_los_cambios_del_tema(sitio):
    negocios(sitio)
    d = sitio.dueno("club-cafe")
    sitio.post(d, "/negocio/club-cafe/diseno", primario="#0b5d1e", forma="redonda")
    t = sitio.texto(d.get("/negocio/club-cafe/caja"))
    assert var(t, "marca-texto") == "#0b5d1e" and var(t, "r") == "20px"
    assert var(sitio.texto(sitio.dueno("prueba").get("/negocio/prueba/")), "marca-texto") != "#0b5d1e"


def test_la_caja_muestra_el_progreso_del_cliente(sitio, reloj):
    negocios(sitio)
    u = sitio.cliente("club-cafe")  # se registra escaneando: 1 visita
    reloj.avanzar(9)  # pasado el tiempo mínimo entre visitas
    sitio.post(u, "/l/club-cafe/visita/pedir")
    codigo = re.search(r'class="codigo"[^>]*>([A-Z0-9]{6})<', sitio.texto(u.get("/l/club-cafe/visita"))).group(1)
    t = sitio.texto(sitio.caja("club-cafe").get(f"/negocio/club-cafe/caja?codigo={codigo}"))
    assert "1 de 8 sellos" in t and "10 puntos" in t and "Confirmar visita" in t
    assert "3815550000" not in t, "el teléfono se muestra enmascarado"
