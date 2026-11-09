import pytest
"""Visitas, puntos y sellos: elegibilidad, idempotencia y las tres vías (NFC, QR en caja, atajo de pruebas)."""
import re
import simulador_nfc as nfc


def pedir_codigo(sitio, cliente, slug):
    sitio.post(cliente, f"/l/{slug}/visita/pedir")
    return re.search(r'class="codigo"[^>]*>([A-Z0-9]{6})<', sitio.texto(cliente.get(f"/l/{slug}/visita"))).group(1)


def test_abrir_o_refrescar_la_pagina_no_suma(sitio):
    u = sitio.cliente("cafe-brasa")
    antes = sitio.filas()
    for _ in range(5):
        u.get("/l/cafe-brasa")
    u.get("/l/cafe-brasa?puntos=500&sumar=1&visita=1&comercio_id=2")
    assert sitio.filas() == antes


def test_el_cliente_no_puede_fijar_los_puntos(sitio):
    u = sitio.cliente("cafe-brasa", escaneo=False)
    antes = sitio.filas()
    sitio.post(u, "/l/cafe-brasa/visita/pedir", puntos="9999", comercio_id="2", cliente_id="1", delta="500")
    assert sitio.filas() == antes
    sitio.asegurar_claves("cafe-brasa")
    caja = sitio.caja("cafe-brasa")
    sid = sitio.bd("SELECT id FROM solicitudes_visita")[0][0]
    sitio.post(caja, f"/negocio/cafe-brasa/caja/visita/{sid}", accion="confirmar", puntos="9999")
    assert sitio.bd("SELECT delta FROM puntos_mov ORDER BY id DESC LIMIT 1")[0][0] == 10


def test_flujo_qr_con_confirmacion_del_personal(sitio):
    sitio.asegurar_claves("cafe-brasa")
    u = sitio.cliente("cafe-brasa", escaneo=False)
    codigo = pedir_codigo(sitio, u, "cafe-brasa")
    assert sitio.filas() == 0, "pedir la visita no debe sumar nada"
    caja = sitio.caja("cafe-brasa")
    vista = sitio.texto(caja.get(f"/negocio/cafe-brasa/caja?codigo={codigo}"))
    assert "Ana" in vista and "Confirmar visita" in vista and "3815550000" not in vista, "el teléfono debe ir enmascarado"
    sid = sitio.bd("SELECT id FROM solicitudes_visita")[0][0]
    sitio.post(caja, f"/negocio/cafe-brasa/caja/visita/{sid}", accion="confirmar")
    fila = sitio.bd("SELECT delta, motivo, fuente, actor, clave_idem FROM puntos_mov")[0]
    assert fila == (10, "visita", "qr_caja", "caja:cafe-brasa", f"sol:{sid}")
    assert "+10 puntos" in sitio.texto(u.get("/l/cafe-brasa/visita", follow_redirects=True))


def test_confirmar_dos_veces_suma_una_sola(sitio):
    sitio.asegurar_claves("cafe-brasa")
    u = sitio.cliente("cafe-brasa", escaneo=False)
    pedir_codigo(sitio, u, "cafe-brasa")
    caja = sitio.caja("cafe-brasa")
    sid = sitio.bd("SELECT id FROM solicitudes_visita")[0][0]
    for _ in range(3):
        sitio.post(caja, f"/negocio/cafe-brasa/caja/visita/{sid}", accion="confirmar")
    assert sitio.filas() == 1


def test_codigo_vencido_no_suma(sitio, reloj):
    sitio.asegurar_claves("cafe-brasa")
    u = sitio.cliente("cafe-brasa", escaneo=False)
    codigo = pedir_codigo(sitio, u, "cafe-brasa")
    reloj.avanzar(0.2)  # 12 minutos: pasó la vigencia de 5
    caja = sitio.caja("cafe-brasa")
    assert "ya venció" in sitio.texto(caja.get(f"/negocio/cafe-brasa/caja?codigo={codigo}"))
    sid = sitio.bd("SELECT id FROM solicitudes_visita")[0][0]
    sitio.post(caja, f"/negocio/cafe-brasa/caja/visita/{sid}", accion="confirmar")
    assert sitio.filas() == 0 and sitio.bd("SELECT estado FROM solicitudes_visita")[0][0] == "vencida"


def test_el_personal_de_otro_negocio_no_puede_confirmar(sitio):
    sitio.asegurar_claves("cafe-brasa"); sitio.asegurar_claves("helados-copo")
    u = sitio.cliente("cafe-brasa", escaneo=False)
    codigo = pedir_codigo(sitio, u, "cafe-brasa")
    otra = sitio.caja("helados-copo")
    assert "ya venció" in sitio.texto(otra.get(f"/negocio/helados-copo/caja?codigo={codigo}")), "no debe ver pedidos ajenos"
    sid = sitio.bd("SELECT id FROM solicitudes_visita")[0][0]
    sitio.post(otra, f"/negocio/helados-copo/caja/visita/{sid}", accion="confirmar")
    assert sitio.filas() == 0 and sitio.bd("SELECT estado FROM solicitudes_visita")[0][0] == "pendiente"


def test_pedir_visita_respeta_el_intervalo(sitio):
    u = sitio.cliente("cafe-brasa")  # ya sumó con el atajo
    r = sitio.post(u, "/l/cafe-brasa/visita/pedir")
    assert sitio.bd("SELECT COUNT(*) FROM solicitudes_visita")[0][0] == 0
    assert "a partir de las" in sitio.texto(u.get(r.headers["Location"]))


def test_intervalo_configurable_por_negocio(sitio, reloj):
    sitio.asegurar_claves("cafe-brasa")
    dueno = sitio.dueno("cafe-brasa")
    sitio.post(dueno, "/negocio/cafe-brasa/ajustes/reglas", puntos_visita="10", sellos_tarjeta="8", premio_tarjeta="x",
               intervalo_horas="2", max_visitas_dia="0", canje_vence_dias="30")
    u = sitio.cliente("cafe-brasa")
    reloj.avanzar(1); u.get("/dev/escanear/cafe-brasa"); assert sitio.filas() == 1
    reloj.avanzar(1.5); u.get("/dev/escanear/cafe-brasa"); assert sitio.filas() == 2


def test_tope_diario(sitio, reloj):
    sitio.asegurar_claves("cafe-brasa")
    dueno = sitio.dueno("cafe-brasa")
    sitio.post(dueno, "/negocio/cafe-brasa/ajustes/reglas", puntos_visita="10", sellos_tarjeta="8", premio_tarjeta="x",
               intervalo_horas="1", max_visitas_dia="2", canje_vence_dias="30")
    u = sitio.cliente("cafe-brasa")
    for _ in range(4):
        reloj.avanzar(1.2); u.get("/dev/escanear/cafe-brasa")
    visitas = sitio.bd("SELECT creado FROM puntos_mov WHERE motivo='visita' ORDER BY id")
    dias = {(t - 10800) // 86400 for (t,) in visitas}
    assert all(sum(1 for (t,) in visitas if (t - 10800) // 86400 == d) <= 2 for d in dias)
    assert sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='visita_rechazada'")[0][0] >= 1


def test_tarjeta_completa_genera_premio_y_se_reinicia(sitio, reloj):
    u = sitio.cliente("helados-copo")
    for _ in range(7):
        reloj.avanzar(9); u.get("/dev/escanear/helados-copo")
    assert sitio.bd("SELECT COUNT(*) FROM canjes WHERE costo=0")[0][0] == 1
    assert "8 de 8" in sitio.texto(u.get("/l/helados-copo"))
    reloj.avanzar(9); u.get("/dev/escanear/helados-copo")
    assert "1 de 8" in sitio.texto(u.get("/l/helados-copo"))


# ---- cartel NFC (simulado por HTTP) ----
def registrar_cartel(sitio, slug="cafe-brasa", uid="04DE5F1EACC040"):
    sitio.asegurar_claves(slug)
    sitio.post(sitio.dueno(slug), f"/negocio/{slug}/carteles", uid=uid)


def test_nfc_valido_suma_y_registra_el_origen(sitio):
    registrar_cartel(sitio)
    u = sitio.cliente("cafe-brasa", escaneo=False)
    u.get(nfc.link(5))
    assert sitio.bd("SELECT fuente, clave_idem FROM puntos_mov")[0][0] == "nfc"


def test_nfc_rechazos(sitio):
    registrar_cartel(sitio)
    c = sitio.http()
    assert c.get(nfc.link(1, uid="04AAAAAAAAAAAA")).status_code == 403, "cartel sin registrar"
    assert c.get(nfc.link(2).replace("cmac=", "cmac=00")).status_code == 403, "firma alterada"
    assert c.get("/t?picc_data=zz&cmac=00").status_code == 403, "formato inválido"
    assert c.get("/t").status_code == 403, "sin parámetros"
    assert c.get(nfc.link(3, clave_archivo=bytes([1]) * 16)).status_code == 403, "firmado con otra clave"
    assert sitio.filas() == 0 and sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion LIKE 'escaneo%'")[0][0] >= 4


def test_nfc_repetido_o_viejo_no_suma(sitio, reloj):
    registrar_cartel(sitio)
    u = sitio.cliente("cafe-brasa", escaneo=False)
    u.get(nfc.link(5)); reloj.avanzar(9)
    u.get(nfc.link(5)); u.get(nfc.link(3))
    assert sitio.filas() == 1 and sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='escaneo_repetido'")[0][0] == 2
    reloj.avanzar(9); u.get(nfc.link(6))
    assert sitio.filas() == 2


def test_nfc_cartel_desactivado_o_negocio_suspendido(sitio, reloj):
    registrar_cartel(sitio)
    u = sitio.cliente("cafe-brasa", escaneo=False)
    p = sitio.plataforma()
    sitio.post(p, f"/plataforma/carteles/{sitio.bd('SELECT id FROM tags')[0][0]}/estado")
    assert u.get(nfc.link(7)).status_code == 403
    sitio.post(p, f"/plataforma/carteles/{sitio.bd('SELECT id FROM tags')[0][0]}/estado")
    sitio.post(p, "/plataforma/negocios/cafe-brasa/estado"); reloj.avanzar(9)
    u.get(nfc.link(8))
    assert sitio.filas() == 0 and "en pausa" in sitio.texto(u.get("/l/cafe-brasa"))


def test_nfc_de_un_cartel_suma_solo_en_su_negocio(sitio):
    registrar_cartel(sitio, "helados-copo")
    u = sitio.cliente("cafe-brasa", escaneo=False)
    u.get(nfc.link(4))  # cartel de helados escaneado por un cliente que solo tiene cuenta en el café
    assert sitio.bd("SELECT COUNT(*) FROM puntos_mov")[0][0] == 0
    assert "Creá tu cuenta" in sitio.texto(u.get("/l/helados-copo"))


def test_nfc_primer_escaneo_de_un_cliente_nuevo_se_suma_al_registrarse(sitio):
    registrar_cartel(sitio)
    c = sitio.http(); c.get(nfc.link(9))
    sitio.post(c, "/l/cafe-brasa/registro", nombre="Nuevo", telefono="3815551111")
    f = sitio.bd("SELECT fuente, clave_idem FROM puntos_mov")
    assert len(f) == 1 and f[0][0] == "nfc" and f[0][1].startswith("nfc:")


def test_el_atajo_de_pruebas_no_existe_en_produccion(crear_app):
    sin_modo = crear_app(MODO_DEV=None)
    assert sin_modo.test_client().get("/dev/escanear/demo").status_code == 404
    import pytest
    with pytest.raises(RuntimeError):
        crear_app(RENDER="1", MODO_DEV="1")


def test_sdm_link_real_con_enc():
    """Link real de un NTAG 424 DNA con claves en cero y datos cifrados (&enc=)."""
    from rf import sdm
    uid, ctr = sdm.verificar("B71323C16AB79CA1E4DF039CE49EC7CB", "13ABC1EE4A2333F5",
                             bytes(16), bytes(16), "6C4A114B464575AAD019D9BB066EBAC3")
    assert (uid, ctr) == ("044D1DBA772390", 4)
    with pytest.raises(ValueError):  # sin enc la firma no coincide
        sdm.verificar("B71323C16AB79CA1E4DF039CE49EC7CB", "13ABC1EE4A2333F5", bytes(16), bytes(16))
