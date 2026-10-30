"""Pedidos simultáneos reales (hilos contra la misma base): nadie puede ganar dos veces."""
import re
from concurrent.futures import ThreadPoolExecutor
import simulador_nfc as nfc
from test_canjes import con_puntos, premio

HILOS = 10


def en_paralelo(funcion):
    with ThreadPoolExecutor(HILOS) as ex:
        return list(ex.map(funcion, range(HILOS)))


def test_canjes_simultaneos_con_saldo_para_uno(sitio):
    u, cid, _ = con_puntos(sitio, 50)
    pid, costo = premio(sitio, "Café de filtro gratis")
    assert costo == 50
    token = sitio.bd("SELECT token FROM clientes WHERE id=?", cid)[0][0]

    def pedido(i):
        c = sitio.http(); c.set_cookie("ct_cafe-brasa", token)
        return sitio.post(c, f"/l/cafe-brasa/canjear/{pid}", nonce=f"n{i}").status_code
    en_paralelo(pedido)
    assert sitio.filas("canjes") == 1 and sitio.saldo(cid) == 0


def test_entregas_simultaneas_del_mismo_codigo(sitio):
    u, cid, _ = con_puntos(sitio, 50)
    pid, _ = premio(sitio, "Café de filtro gratis")
    sitio.post(u, f"/l/cafe-brasa/canjear/{pid}", nonce="1")
    codigo = sitio.bd("SELECT codigo FROM canjes")[0][0]

    def entrega(i):
        c = sitio.dueno("cafe-brasa"); sitio.post(c, "/negocio/cafe-brasa/canjes/validar", codigo=codigo)
    en_paralelo(entrega)
    assert sitio.bd("SELECT COUNT(*) FROM auditoria WHERE accion='canje_entregado'")[0][0] == 1


def test_confirmaciones_simultaneas_de_una_visita(sitio):
    sitio.asegurar_claves("cafe-brasa")
    u = sitio.cliente("cafe-brasa", escaneo=False)
    sitio.post(u, "/l/cafe-brasa/visita/pedir")
    sid = sitio.bd("SELECT id FROM solicitudes_visita")[0][0]

    def confirma(i):
        c = sitio.caja("cafe-brasa"); sitio.post(c, f"/negocio/cafe-brasa/caja/visita/{sid}", accion="confirmar")
    en_paralelo(confirma)
    assert sitio.filas() == 1


def test_el_mismo_link_nfc_usado_a_la_vez(sitio):
    sitio.asegurar_claves("cafe-brasa"); sitio.post(sitio.dueno("cafe-brasa"), "/negocio/cafe-brasa/carteles", uid="04DE5F1EACC040")
    u = sitio.cliente("cafe-brasa", escaneo=False)
    token = sitio.bd("SELECT token FROM clientes")[0][0]
    enlace = nfc.link(5)

    def toque(i):
        c = sitio.http(); c.set_cookie("ct_cafe-brasa", token); c.get(enlace)
    en_paralelo(toque)
    assert sitio.filas() == 1


def test_toques_nfc_distintos_dentro_del_intervalo(sitio):
    sitio.asegurar_claves("cafe-brasa"); sitio.post(sitio.dueno("cafe-brasa"), "/negocio/cafe-brasa/carteles", uid="04DE5F1EACC040")
    sitio.cliente("cafe-brasa", escaneo=False)
    token = sitio.bd("SELECT token FROM clientes")[0][0]

    def toque(i):
        c = sitio.http(); c.set_cookie("ct_cafe-brasa", token); c.get(nfc.link(10 + i))
    en_paralelo(toque)
    assert sitio.filas() == 1, "en el intervalo mínimo solo cuenta una visita, aunque cada link sea distinto"


def test_registros_simultaneos_del_mismo_telefono(sitio):
    def registro(i):
        c = sitio.http(); sitio.post(c, "/l/cafe-brasa/registro", nombre="Ana", telefono="3815550000")
    en_paralelo(registro)
    assert sitio.filas("clientes") == 1
