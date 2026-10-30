"""Identidad visual por negocio: un tema por negocio, aplicado a TODAS las pantallas del cliente, sin mezclarse entre negocios."""
import io, json, re

CAFE = dict(primario="#5b3a29", acento="#d08c4f", fondo="#f6efe6", sup="#fffaf4", tinta="#2b1a10", tipografia="elegante",
            estilo="editorial", forma="recta", botones="solido", tarjetas="borde", cabecera="limpia", densidad="normal",
            decoracion="ninguna", titular="El café de la esquina, con premio", icono="☕")
GYM = dict(primario="#111111", acento="#c6ff00", boton="#c6ff00", fondo="#1a1a1a", sup="#242424", tinta="#f5f5f5", tipografia="impacto",
           estilo="audaz", forma="recta", botones="sombra", tarjetas="marco", cabecera="barra", densidad="compacta",
           decoracion="rayas", titular="Entrená y sumá", icono="💪")
ROPA = dict(primario="#7a3cff", acento="#ff5fa2", fondo="#fbf8ff", sup="#ffffff", tinta="#1f1630", tipografia="grotesca",
            estilo="suave", forma="redonda", botones="pildora", tarjetas="sombra", cabecera="centrada", densidad="amplia",
            decoracion="circulos", titular="Tu estilo tiene recompensa", icono="★")
PAGINAS = lambda slug: [f"/l/{slug}", f"/registro/{slug}", f"/l/{slug}/entrar", f"/l/{slug}/verificar?tel=1"]


def css(texto):
    m = re.search(r"<style>(:root\{[^<]*\})</style>", texto)
    return m.group(1) if m else None


def var(texto, nombre):
    m = re.search(rf"--{nombre}:([^;}}]+)", css(texto) or "")
    return m.group(1) if m else None


def clases(texto):
    return re.search(r'<body class="([^"]+)"', texto).group(1)


def tres_negocios(sitio):
    p = sitio.plataforma()
    slugs = []
    for nombre, tema in (("Club Cafe", CAFE), ("Gym Titan", GYM), ("Moda Lila", ROPA)):
        slug = sitio.crear_negocio(nombre)
        r = sitio.post(p, f"/plataforma/negocios/{slug}/tema", **tema)
        assert r.status_code == 302
        slugs.append(slug)
    return p, slugs


def test_cada_negocio_tiene_su_identidad_en_todas_sus_pantallas(sitio):
    p, (cafe, gym, ropa) = tres_negocios(sitio)
    for slug, tema in ((cafe, CAFE), (gym, GYM), (ropa, ROPA)):
        anon = sitio.http()
        paginas = [sitio.texto(anon.get(u)) for u in PAGINAS(slug)]
        u = sitio.cliente(slug, tel="3815557777")
        paginas.append(sitio.texto(u.get(f"/l/{slug}")))  # la tarjeta y los premios
        assert "Hola, Ana" in paginas[-1] and "Tu tarjeta de sellos" in paginas[-1]
        estilos = {css(t) for t in paginas}
        assert len(estilos) == 1 and None not in estilos, f"{slug}: todas las pantallas usan exactamente el mismo tema"
        assert {clases(t).split(" ", 3)[3] for t in paginas} == {clases(paginas[0]).split(" ", 3)[3]}
        t = paginas[0]
        assert var(t, "primario") == tema["primario"] and var(t, "acento") == tema["acento"] and var(t, "fondo") == tema["fondo"]
        assert f"e-{tema['estilo']}" in clases(t) and f"bt-{tema['botones']}" in clases(t) and f"cab-{tema['cabecera']}" in clases(t)
        assert tema["titular"] in t, "la página de inicio muestra su propio título"
        assert tema["icono"] in paginas[-1], "la tarjeta de sellos usa su ícono"
    a, b, c = (css(sitio.texto(sitio.http().get(f"/registro/{s}"))) for s in (cafe, gym, ropa))
    assert len({a, b, c}) == 3, "las tres páginas de registro se ven distintas"


def test_cambiar_el_tema_de_un_negocio_cambia_todas_sus_pantallas_y_no_toca_a_los_demas(sitio):
    p, (cafe, gym, ropa) = tres_negocios(sitio)
    antes = {s: css(sitio.texto(sitio.http().get(f"/registro/{s}"))) for s in (gym, ropa)}
    sitio.post(p, f"/plataforma/negocios/{cafe}/tema", primario="#0b5d1e", acento="#f2c14e", tipografia="geometrica")
    u = sitio.cliente(cafe, tel="3815551234")
    for t in [sitio.texto(sitio.http().get(x)) for x in PAGINAS(cafe)] + [sitio.texto(u.get(f"/l/{cafe}"))]:
        assert var(t, "primario") == "#0b5d1e" and var(t, "acento") == "#f2c14e" and "DM Sans" in var(t, "titulo")
        assert var(t, "fondo") == CAFE["fondo"], "lo que no se tocó se conserva"
    for s in (gym, ropa):
        assert css(sitio.texto(sitio.http().get(f"/registro/{s}"))) == antes[s], f"{s} no cambió"


def test_el_tema_persiste_tras_cerrar_sesion(sitio):
    p, (cafe, gym, ropa) = tres_negocios(sitio)
    sitio.post(p, "/plataforma/salir")
    guardado = json.loads(sitio.bd("SELECT tema FROM comercios WHERE slug=?", gym)[0][0])
    assert guardado["primario"] == GYM["primario"] and guardado["estilo"] == "audaz"
    t = sitio.texto(sitio.http().get(f"/l/{gym}"))
    assert var(t, "primario") == GYM["primario"] and "tj-marco" in clases(t)


def test_el_registro_queda_en_el_negocio_de_la_direccion(sitio):
    p, (cafe, gym, ropa) = tres_negocios(sitio)
    ids = {s: sitio.bd("SELECT id FROM comercios WHERE slug=?", s)[0][0] for s in (cafe, gym, ropa)}
    c = sitio.http()
    assert "Creá tu cuenta" in sitio.texto(c.get(f"/registro/{gym}"))
    r = sitio.post(c, f"/l/{gym}/registro", nombre="Leo", telefono="3815550101", comercio_id=str(ids[cafe]), slug=cafe)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/l/{gym}")
    assert sitio.bd("SELECT comercio_id FROM clientes WHERE telefono='3815550101'") == [(ids[gym],)], "el negocio sale de la dirección, no del formulario"
    assert "Hola, Leo" in sitio.texto(c.get(f"/l/{gym}"))
    assert "Hola, Leo" not in sitio.texto(c.get(f"/l/{cafe}")), "la sesión del cliente es solo de ese negocio"
    # el mismo teléfono puede ser cliente de otro negocio, por separado
    sitio.post(sitio.http(), f"/l/{ropa}/registro", nombre="Leo", telefono="3815550101")
    assert sitio.bd("SELECT COUNT(*) FROM clientes WHERE telefono='3815550101'")[0][0] == 2
    # un error de registro vuelve a la página de registro del mismo negocio
    r = sitio.post(sitio.http(), f"/l/{gym}/registro", nombre="", telefono="1")
    assert f"/registro/{gym}" in r.headers["Location"]
    assert sitio.http().get(f"/l/{gym}/registro").headers["Location"].endswith(f"/registro/{gym}")


def test_la_lealtad_sigue_funcionando_con_temas_propios(sitio):
    p, (cafe, gym, ropa) = tres_negocios(sitio)
    u = sitio.cliente(gym)  # entra escaneando el cartel: suma su primera visita al registrarse
    t = sitio.texto(u.get(f"/l/{gym}"))
    assert '<div class="puntos">10</div>' in t and "1 de" in t
    assert sitio.saldo(sitio.cid(slug=gym)) == 10


def test_escanear_el_cartel_sin_cuenta_lleva_directo_al_registro(sitio):
    c = sitio.http()
    t = sitio.texto(c.get("/l/cafe-brasa"))
    assert "Cómo funciona" in t, "sin cuenta se ve la página de inicio del negocio"
    c.get("/dev/escanear/cafe-brasa")
    t = sitio.texto(c.get("/l/cafe-brasa"))
    assert 'name="telefono"' in t and "tu visita de hoy se suma" in t


def test_un_negocio_no_puede_cambiar_ni_espiar_el_diseno_de_otro(sitio):
    p, (cafe, gym, ropa) = tres_negocios(sitio)
    sitio.asegurar_claves(cafe)
    dueno_cafe = sitio.dueno(cafe)
    antes = sitio.bd("SELECT tema FROM comercios WHERE slug=?", gym)[0][0]
    r = sitio.post(dueno_cafe, f"/negocio/{gym}/diseno", primario="#ff0000")
    assert r.status_code == 302 and "ingresar" in r.headers["Location"]
    assert sitio.post(dueno_cafe, f"/plataforma/negocios/{gym}/tema", primario="#ff0000").status_code == 302
    assert sitio.bd("SELECT tema FROM comercios WHERE slug=?", gym)[0][0] == antes
    assert "ingresar" in dueno_cafe.get(f"/l/{gym}/vista/inicio").headers["Location"], "la vista previa de otro negocio no se abre"
    assert "ingresar" in sitio.http().get(f"/l/{gym}/vista/inicio").headers["Location"]
    # el dueño sí cambia el suyo, y solo el suyo, aunque el formulario nombre a otro negocio
    r = sitio.post(dueno_cafe, f"/negocio/{cafe}/diseno", primario="#0000aa", slug=gym, comercio_id="1")
    assert r.status_code == 302
    assert json.loads(sitio.bd("SELECT tema FROM comercios WHERE slug=?", cafe)[0][0])["primario"] == "#0000aa"
    assert sitio.bd("SELECT tema FROM comercios WHERE slug=?", gym)[0][0] == antes
    assert sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='tema_modificado' AND actor=?", f"dueno:{cafe}")[0][0] == 1
    # la caja no toca el diseño
    caja = sitio.caja(cafe)
    assert caja.get(f"/negocio/{cafe}/diseno").status_code == 403
    assert sitio.post(caja, f"/negocio/{cafe}/diseno", primario="#123456").status_code == 403


def test_el_editor_del_dueno_y_la_vista_previa(sitio):
    sitio.asegurar_claves("cafe-brasa")
    d = sitio.dueno("cafe-brasa")
    t = sitio.texto(d.get("/negocio/cafe-brasa/diseno"))
    assert "data-tema-editor" in t and "/l/cafe-brasa/vista/inicio" in t and 'name="nombre"' not in t
    antes = sitio.bd("SELECT tema FROM comercios WHERE slug='cafe-brasa'")[0][0]
    for pagina in ("inicio", "registro", "tarjeta", "exito", "entrar"):
        r = d.get(f"/l/cafe-brasa/vista/{pagina}?primario=%23aa0000&estilo=audaz")
        assert r.status_code == 200 and var(sitio.texto(r), "primario") == "#aa0000" and "e-audaz" in clases(sitio.texto(r)), pagina
        assert "vista-previa" in clases(sitio.texto(r))
    assert "+10 puntos" in sitio.texto(d.get("/l/cafe-brasa/vista/exito"))
    assert sitio.bd("SELECT tema FROM comercios WHERE slug='cafe-brasa'")[0][0] == antes, "la vista previa no guarda nada"
    assert "se confunden" in sitio.texto(d.get("/l/cafe-brasa/vista/inicio?tinta=%23f4ebdd")), "avisa del contraste antes de guardar"
    assert d.get("/l/cafe-brasa/vista/otra").status_code == 404
    p = sitio.texto(sitio.plataforma().get("/plataforma/negocios/cafe-brasa?tab=diseno"))
    assert "data-tema-editor" in p and 'name="nombre"' in p


def test_el_tema_no_admite_css_ni_html(sitio):
    p = sitio.plataforma()
    antes = sitio.bd("SELECT tema FROM comercios WHERE slug='demo'")[0][0]
    for malo in (dict(fondo="red;}</style><script>alert(1)</script>"), dict(primario="#12345g"), dict(tipografia="x;}body{display:none"),
                 dict(estilo="<b>"), dict(botones="solido;background:url(https://evil)")):
        sitio.post(p, "/plataforma/negocios/demo/tema", **malo)
        assert sitio.bd("SELECT tema FROM comercios WHERE slug='demo'")[0][0] == antes, malo
    sitio.post(p, "/plataforma/negocios/demo/tema", titular='<script>alert(1)</script>', bajada='"><img src=x onerror=alert(1)>', icono="<i>")
    t = sitio.texto(sitio.http().get("/l/demo"))
    assert "<script>alert" not in t and "<img src=x" not in t and "&lt;script&gt;" in t
    # aunque alguien escribiera la base a mano, lo inválido no llega a la página
    sitio.bd("UPDATE comercios SET tema=? WHERE slug='demo'", json.dumps({"fondo": "red}</style><script>", "fuente": "x;}", "estilo": "a b",
                                                                          "radio": "0;background:url(//evil)", "primario": "#000000"}))
    t = sitio.texto(sitio.http().get("/l/demo"))
    estilo = css(t)
    assert "<script>" not in t and "evil" not in t and "red}" not in estilo and var(t, "primario") == "#000000"
    assert "e-clasico" in clases(t)
    sitio.bd("UPDATE comercios SET tema='no es json' WHERE slug='demo'")
    assert sitio.http().get("/l/demo").status_code == 200


def test_contraste_y_texto_de_botones(sitio):
    p = sitio.plataforma()
    sitio.post(p, "/plataforma/negocios/demo/tema", tinta="#eeeeee", fondo="#ffffff")
    assert json.loads(sitio.bd("SELECT tema FROM comercios WHERE slug='demo'")[0][0] or "{}").get("tinta") != "#eeeeee"
    sitio.post(p, "/plataforma/negocios/demo/tema", boton="#ffd400", boton_texto="#ffffff")  # letras blancas sobre amarillo: no se leen
    t = sitio.texto(sitio.http().get("/l/demo"))
    assert var(t, "boton") == "#ffd400" and var(t, "boton-texto") == "#141414", "se corrige solo a un color legible"


def test_los_colores_automaticos_siguen_al_color_principal(sitio):
    p = sitio.plataforma()
    # el editor manda TODOS los colores; los automáticos llegan con el valor calculado de antes
    viejo = var(sitio.texto(sitio.http().get("/l/demo")), "boton")
    sitio.post(p, "/plataforma/negocios/demo/tema", primario="#7a3cff", boton=viejo, sup=var(sitio.texto(sitio.http().get("/l/demo")), "sup"))
    t = sitio.texto(sitio.http().get("/l/demo"))
    assert var(t, "boton") == "#7a3cff" and var(t, "boton-texto") == "#ffffff"
    guardado = json.loads(sitio.bd("SELECT tema FROM comercios WHERE slug='demo'")[0][0])
    assert "boton" not in guardado and "sup" not in guardado
    sitio.post(p, "/plataforma/negocios/demo/tema", primario="#0b5d1e", boton="#ff5fa2")  # elegido a propósito: se respeta
    t = sitio.texto(sitio.http().get("/l/demo"))
    assert var(t, "primario") == "#0b5d1e" and var(t, "boton") == "#ff5fa2"


def test_temas_de_versiones_anteriores_se_entienden(sitio):
    t = sitio.texto(sitio.http().get("/l/cafe-brasa"))  # guardado como radio/titulo sueltos
    assert var(t, "radio") == "4px" and "Georgia" in var(t, "titulo") and var(t, "fondo") == "#f4ebdd"
    t = sitio.texto(sitio.http().get("/l/helados-copo"))
    assert var(t, "radio") == "22px" and "Trebuchet" in var(t, "fuente")
    assert "disenos/helados-copo/estilo.css" in t, "el diseño a medida sigue cargando encima del tema"


def test_imagen_de_cabecera(sitio):
    p = sitio.plataforma()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 200
    p.post("/plataforma/negocios/demo/tema", data={"csrf": sitio.tok(p), "fondo_img": (io.BytesIO(png), "f.png")})
    t = sitio.texto(sitio.http().get("/registro/demo"))
    assert "con-fondo" in clases(t) and "--fondo-img:url(/l/demo/fondo" in css(t)
    r = sitio.http().get("/l/demo/fondo")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.headers["X-Content-Type-Options"] == "nosniff"
    p.post("/plataforma/negocios/demo/tema", data={"csrf": sitio.tok(p), "fondo_img": (io.BytesIO(b"<svg onload=alert(1)>"), "x.svg")})
    assert sitio.http().get("/l/demo/fondo").data == png, "solo imágenes reales"
    sitio.post(p, "/plataforma/negocios/demo/tema", quitar_fondo="1")
    assert sitio.http().get("/l/demo/fondo").status_code == 404 and "con-fondo" not in clases(sitio.texto(sitio.http().get("/l/demo")))


def test_las_paginas_publicas_no_muestran_datos_internos(sitio):
    p, (cafe, gym, ropa) = tres_negocios(sitio)
    sitio.cliente(gym, nombre="Secreta", tel="3815559090")
    for u in PAGINAS(gym):
        t = sitio.texto(sitio.http().get(u))
        assert "Secreta" not in t and "3815559090" not in t and "pbkdf2" not in t and "scrypt" not in t, u


def test_desktop_y_celular_usan_la_misma_pagina(sitio):
    t = sitio.texto(sitio.http().get("/l/demo"))
    assert 'name="viewport"' in t and "width=device-width" in t
    estilo = sitio.texto(sitio.http().get("/static/css/cliente.css"))
    assert "@media (min-width:48rem)" in estilo and not re.search(r"#[0-9a-f]{6}", estilo.split("/* valores de respaldo")[1].split("*{box-sizing")[1].replace("#1b8a57", "").replace("#b42318", ""))
