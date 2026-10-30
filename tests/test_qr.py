"""Códigos QR: apuntan al negocio correcto, no llevan secretos, son legibles y abrirlos no suma nada."""
import io, re
import xml.etree.ElementTree as ET
import cv2, numpy as np
import pytest

BASE = "https://rf.ejemplo"


def leer_qr(png):
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    texto, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return texto, img


@pytest.mark.parametrize("slug", ["cafe-brasa", "helados-copo", "demo"])
def test_el_qr_descargado_apunta_al_negocio_correcto(sitio, slug):
    texto, _ = leer_qr(sitio.plataforma().get(f"/negocio/{slug}/qr.png").data)
    assert texto == f"{BASE}/l/{slug}"


def test_el_qr_es_legible_con_margen_y_resolucion_suficientes(sitio):
    png = sitio.plataforma().get("/negocio/cafe-brasa/qr.png").data
    texto, img = leer_qr(png)
    assert min(img.shape) >= 300 and texto
    oscuros = np.argwhere(img < 128)
    margen = oscuros.min(axis=0)
    modulo = (oscuros.max(axis=0) - oscuros.min(axis=0)).min() / 29
    assert (margen >= 3.5 * modulo).all(), "el margen blanco (zona de silencio) debe ser de al menos 4 módulos"
    assert img[0, 0] == 255 and img.max() == 255 and img.min() == 0, "solo blanco y negro puros: máximo contraste"


def test_el_qr_no_lleva_secretos_ni_datos_de_clientes(sitio):
    sitio.cliente("cafe-brasa")
    texto, _ = leer_qr(sitio.plataforma().get("/negocio/cafe-brasa/qr.png").data)
    assert re.fullmatch(rf"{re.escape(BASE)}/l/[a-z0-9-]+", texto), "solo la dirección pública del negocio"
    token = sitio.bd("SELECT token FROM clientes")[0][0]
    assert token not in texto and "3815550000" not in texto and "clave" not in texto and "?" not in texto


def test_el_qr_svg_es_valido(sitio):
    r = sitio.plataforma().get("/negocio/cafe-brasa/qr.svg")
    assert r.mimetype == "image/svg+xml" and "attachment" in r.headers["Content-Disposition"]
    raiz = ET.fromstring(r.data)
    assert raiz.tag.endswith("svg") and raiz.get("viewBox") or raiz.get("width")
    assert f"{BASE}/l/cafe-brasa" in r.data.decode()


def test_la_pagina_imprimible_conserva_la_marca_y_las_instrucciones(sitio):
    sitio.asegurar_claves("cafe-brasa")
    t = sitio.texto(sitio.dueno("cafe-brasa").get("/negocio/cafe-brasa/qr/imprimible"))
    assert "Café Brasa" in t and "Escaneá para ver tu tarjeta de lealtad" in t and "<svg" in t and f"{BASE}/l/cafe-brasa" in t
    assert "#f4ebdd" in t and "disenos/cafe-brasa/logo.svg" in t, "usa los colores y el logo del negocio"


def test_los_qr_de_otro_negocio_no_se_descargan(sitio):
    sitio.asegurar_claves("cafe-brasa")
    d = sitio.dueno("cafe-brasa")
    assert d.get("/negocio/helados-copo/qr.png").status_code == 302
    assert sitio.caja("cafe-brasa").get("/negocio/cafe-brasa/qr.png").status_code == 403


def test_abrir_el_qr_muestra_la_marca_del_negocio_y_no_suma(sitio):
    anon = sitio.http()
    r = anon.get("/l/cafe-brasa")
    t = sitio.texto(r)
    assert "Café Brasa" in t and "Creá tu cuenta" in t and "disenos/cafe-brasa/estilo.css" in t
    for _ in range(3):
        anon.get("/l/cafe-brasa")
    assert sitio.filas() == 0 and sitio.filas("clientes") == 0


def test_el_cliente_registrado_ve_su_saldo_y_premios_pero_no_suma_al_abrir(sitio):
    u = sitio.cliente("cafe-brasa", escaneo=False)
    antes = sitio.filas()
    t = sitio.texto(u.get("/l/cafe-brasa"))
    assert "Hola, Ana" in t and "Medialuna con café" in t and "Registrar mi visita en caja" in t
    assert sitio.filas() == antes


def test_un_negocio_suspendido_muestra_el_aviso_desde_el_qr(sitio):
    sitio.post(sitio.plataforma(), "/plataforma/negocios/cafe-brasa/estado")
    assert "en pausa" in sitio.texto(sitio.http().get("/l/cafe-brasa"))


def test_el_qr_del_cliente_en_caja_solo_lleva_el_codigo_corto(sitio):
    u = sitio.cliente("cafe-brasa", escaneo=False)
    sitio.post(u, "/l/cafe-brasa/visita/pedir")
    pagina = sitio.texto(u.get("/l/cafe-brasa/visita"))
    codigo = re.search(r'class="codigo"[^>]*>([A-Z0-9]{6})<', pagina).group(1)
    destino = re.search(r"<desc>([^<]+)</desc>", pagina).group(1)
    assert destino == f"{BASE}/negocio/cafe-brasa/caja?codigo={codigo}"
    assert "3815550000" not in destino and "Ana" not in destino
    assert 'http-equiv="refresh"' in pagina
