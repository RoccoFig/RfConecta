"""Sesiones, inicio de sesión, protección CSRF y límite de intentos. Ninguna contraseña vive en el código."""
import os, secrets
from functools import wraps
from urllib.parse import urlparse
from flask import abort, g, redirect, request, session, url_for
from markupsafe import Markup
from werkzeug.security import check_password_hash
from .db import db, ahora
from .motor import comercio

MAX_FALLOS, VENTANA = 5, 15 * 60


def admin_password():
    return os.environ.get("ADMIN_PASSWORD", "")


def coincide(a, b):
    return secrets.compare_digest(a.encode(), b.encode())


# ---------- CSRF: toda acción que modifica datos exige el token de la sesión ----------
def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def csrf_input():
    return Markup('<input type="hidden" name="csrf" value="%s">') % token_csrf()


def proteger_pedido():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origen = request.headers.get("Origin")
        if origen and urlparse(origen).netloc != request.host:
            abort(403)
        enviado, esperado = request.form.get("csrf", ""), session.get("csrf", "")
        if not esperado or not enviado or not coincide(enviado, esperado):
            abort(403)


# ---------- límite de intentos de ingreso ----------
def ip():
    return request.remote_addr or "?"


def bloqueado(ambito, maximo=MAX_FALLOS, ventana=VENTANA):
    n = db().execute("SELECT COUNT(*) FROM intentos_login WHERE clave=? AND creado>?",
                     (f"{ambito}:{ip()}", ahora() - ventana)).fetchone()[0]
    return n >= maximo


def registrar_fallo(ambito):
    db().execute("INSERT INTO intentos_login(clave,creado) VALUES(?,?)", (f"{ambito}:{ip()}", ahora()))
    db().execute("DELETE FROM intentos_login WHERE creado<?", (ahora() - 24 * 3600,))
    db().commit()


# ---------- sesiones ----------
def es_plataforma():
    return session.get("rol") == "plataforma"


def iniciar(rol, slug=None, nivel=None):
    session.clear()  # sesión nueva en cada ingreso
    session["rol"] = rol
    if slug:
        session["negocio"] = slug
    if nivel:
        session["nivel"] = nivel  # 'dueno' ve todo; 'caja' solo valida visitas y entrega premios
    session.permanent = True
    token_csrf()


def verificar_plataforma(clave):
    ok = bool(admin_password()) and coincide(clave, admin_password())
    return ok


def verificar_negocio(com, clave):
    """Devuelve 'dueno', 'caja' o None según la clave escrita."""
    if com["clave_hash"] and check_password_hash(com["clave_hash"], clave):
        return "dueno"
    if com["clave_caja_hash"] and check_password_hash(com["clave_caja_hash"], clave):
        return "caja"
    return None


def actor_actual():
    if es_plataforma():
        return "plataforma"
    if session.get("rol") == "negocio":
        return f"{'dueno' if session.get('nivel') == 'dueno' else 'caja'}:{session.get('negocio')}"
    return "anonimo"


def destino_seguro(destino, defecto):
    """Solo se acepta una dirección interna sencilla: nada de esquemas, dominios, barras invertidas ni caracteres de control."""
    if not destino or not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
        return defecto
    if any(ord(ch) < 32 for ch in destino):
        return defecto
    p = urlparse(destino)
    return destino if not p.scheme and not p.netloc else defecto


def requiere_plataforma(f):
    @wraps(f)
    def w(*a, **k):
        if not es_plataforma():
            return redirect(url_for("plataforma.ingresar", siguiente=request.full_path.rstrip("?")))
        return f(*a, **k)
    return w


def requiere_negocio(f=None, *, caja=False):
    """Cada negocio solo entra a lo suyo; la plataforma puede entrar a cualquiera (soporte).
    Las pantallas de caja las puede usar el personal; todo lo demás es solo del dueño."""
    def decorador(f):
        @wraps(f)
        def w(slug, *a, **k):
            com = comercio(slug)
            propio = session.get("rol") == "negocio" and session.get("negocio") == slug
            if not (es_plataforma() or propio):
                return redirect(url_for("negocio.ingresar", slug=slug, siguiente=request.full_path.rstrip("?")))
            if propio and not caja and session.get("nivel") != "dueno":
                abort(403)
            g.com = com
            return f(slug, *a, **k)
        return w
    return decorador(f) if f else decorador
