"""Canje de premios: cobro exacto, un solo uso, vencimiento, cancelación con devolución y contabilidad coherente."""
import re
import pytest

ALFABETO = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def con_puntos(sitio, puntos, slug="cafe-brasa", tel="3815550000"):
    """Cliente con exactamente `puntos`, cargados con ajustes auditados (como lo haría un dueño)."""
    sitio.asegurar_claves(slug)
    u = sitio.cliente(slug, tel=tel, escaneo=False)
    cid = sitio.cid(tel, slug)
    d = sitio.dueno(slug)
    sitio.post(d, f"/negocio/{slug}/clientes/{cid}/ajustar", puntos=str(puntos), motivo="carga de prueba")
    return u, cid, d


def premio(sitio, nombre, slug="cafe-brasa"):
    return sitio.bd("SELECT id, costo FROM premios WHERE nombre=? AND comercio_id=(SELECT id FROM comercios WHERE slug=?)", nombre, slug)[0]


def test_canje_descuenta_exactamente_y_genera_un_codigo_seguro(sitio):
    u, cid, _ = con_puntos(sitio, 120)
    pid, costo = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="a1")
    codigo = sitio.bd("SELECT codigo FROM canjes")[0][0]
    assert len(codigo) == 8 and set(codigo) <= ALFABETO
    assert sitio.saldo(cid) == 120 - costo
    assert sitio.bd("SELECT delta, motivo, fuente, ref FROM puntos_mov ORDER BY id DESC LIMIT 1")[0][:3] == (-costo, "canje", "cliente")


def test_no_se_puede_canjear_sin_puntos(sitio):
    u, cid, _ = con_puntos(sitio, 10)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="a1")
    assert sitio.filas("canjes") == 0 and sitio.saldo(cid) == 10


def test_doble_envio_con_el_mismo_identificador_canjea_una_vez(sitio):
    u, cid, _ = con_puntos(sitio, 500)
    pid, costo = premio(sitio, "Café de filtro gratis")
    for _ in range(3):
        sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="mismo")
    assert sitio.filas("canjes") == 1 and sitio.saldo(cid) == 500 - costo


def test_canjear_un_premio_de_otro_negocio_no_funciona(sitio):
    u, cid, _ = con_puntos(sitio, 500)
    pid, _ = premio(sitio, "Bochita extra", "helados-copo")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="x")
    assert sitio.filas("canjes") == 0 and sitio.saldo(cid) == 500


def test_premio_quitado_no_se_puede_canjear_pero_el_canje_hecho_vale(sitio):
    u, cid, d = con_puntos(sitio, 500)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    sitio.post(d, f"/negocio/cafe-brasa/premios/{pid}/quitar")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="2")
    assert sitio.filas("canjes") == 1
    codigo = sitio.bd("SELECT codigo FROM canjes")[0][0]
    sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=codigo)
    assert sitio.bd("SELECT estado FROM canjes")[0][0] == "usado"


def test_entrega_una_sola_vez_y_deja_auditoria(sitio):
    u, cid, d = con_puntos(sitio, 100)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    codigo = sitio.bd("SELECT codigo FROM canjes")[0][0]
    sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=codigo.lower())  # no distingue mayúsculas
    sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=codigo)
    assert sitio.bd("SELECT estado, resuelto_por FROM canjes")[0] == ("usado", "dueno:cafe-brasa")
    assert sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='canje_entregado'")[0][0] == 1
    assert sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='canje_rechazado' AND detalle='ya_usado'")[0][0] == 1


def test_otro_negocio_no_puede_entregar_mi_canje(sitio):
    u, cid, _ = con_puntos(sitio, 100)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    codigo = sitio.bd("SELECT codigo FROM canjes")[0][0]
    sitio.asegurar_claves("helados-copo")
    sitio.post(sitio.dueno("helados-copo"), "/negocio/helados-copo/canjes/validar", codigo=codigo)
    assert sitio.bd("SELECT estado FROM canjes")[0][0] == "pendiente"


def test_canje_vencido_no_se_entrega(sitio, reloj):
    u, cid, d = con_puntos(sitio, 100)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    codigo = sitio.bd("SELECT codigo FROM canjes")[0][0]
    reloj.avanzar(24 * 31)
    sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=codigo)
    assert sitio.bd("SELECT estado FROM canjes")[0][0] == "pendiente"
    assert "Vencido" in sitio.texto(d.get("/negocio/cafe-brasa/canjes"))
    assert "vencido" in sitio.bd("SELECT detalle FROM auditoria WHERE accion='canje_rechazado'")[0][0]


def test_cancelar_devuelve_los_puntos_una_vez(sitio):
    u, cid, d = con_puntos(sitio, 100)
    pid, costo = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    kid = sitio.bd("SELECT id FROM canjes")[0][0]
    for _ in range(2):
        sitio.post(d, f"/negocio/cafe-brasa/canjes/{kid}/cancelar", motivo="error de carga")
    assert sitio.saldo(cid) == 100 and sitio.bd("SELECT estado FROM canjes")[0][0] == "cancelado"
    assert sitio.bd("SELECT COUNT(*) FROM puntos_mov WHERE motivo='devolucion'")[0][0] == 1
    codigo = sitio.bd("SELECT codigo FROM canjes")[0][0]
    sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=codigo)
    assert sitio.bd("SELECT estado FROM canjes")[0][0] == "cancelado", "un canje cancelado no se puede entregar"


def test_no_se_cancela_un_canje_ya_entregado(sitio):
    u, cid, d = con_puntos(sitio, 100)
    pid, costo = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    kid, codigo = sitio.bd("SELECT id, codigo FROM canjes")[0]
    sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=codigo)
    sitio.post(d, f"/negocio/cafe-brasa/canjes/{kid}/cancelar")
    assert sitio.saldo(cid) == 100 - costo and sitio.bd("SELECT estado FROM canjes")[0][0] == "usado"


def test_el_premio_de_la_tarjeta_completa_no_cuesta_ni_devuelve_puntos(sitio, reloj):
    sitio.asegurar_claves("helados-copo")
    d = sitio.dueno("helados-copo")
    sitio.post(d, "/negocio/helados-copo/ajustes/reglas", puntos_visita="10", sellos_tarjeta="2", premio_tarjeta="Cucurucho de regalo",
               intervalo_horas="1", max_visitas_dia="0", canje_vence_dias="30")
    u = sitio.cliente("helados-copo"); reloj.avanzar(2); u.get("/dev/escanear/helados-copo")
    kid = sitio.bd("SELECT id FROM canjes")[0][0]
    antes = sitio.saldo(sitio.cid(slug="helados-copo"))
    sitio.post(d, f"/negocio/helados-copo/canjes/{kid}/cancelar")
    assert sitio.saldo(sitio.cid(slug="helados-copo")) == antes and sitio.bd("SELECT COUNT(*) FROM puntos_mov WHERE motivo='devolucion'")[0][0] == 0


def test_frenar_la_adivinanza_de_codigos(sitio):
    sitio.asegurar_claves("cafe-brasa")
    d = sitio.dueno("cafe-brasa")
    u, cid, _ = con_puntos(sitio, 100)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    real = sitio.bd("SELECT codigo FROM canjes")[0][0]
    for i in range(10):
        sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=f"ZZZZZZ{i:02d}")
    sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=real)
    assert sitio.bd("SELECT estado FROM canjes")[0][0] == "pendiente", "tras 10 fallos el sistema se frena, incluso con el código correcto"


def test_la_base_impide_reabrir_un_canje_resuelto(sitio):
    u, cid, d = con_puntos(sitio, 100)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    sitio.post(d, "/negocio/cafe-brasa/canjes/validar", codigo=sitio.bd("SELECT codigo FROM canjes")[0][0])
    with pytest.raises(Exception, match="ya fue resuelto"):
        sitio.bd("UPDATE canjes SET estado='pendiente'")
