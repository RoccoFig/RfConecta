"""RF — plataforma de fidelización para negocios locales.

Tres experiencias sobre un mismo motor:
  cliente      pantalla móvil que se abre al apoyar el celular en el cartel (diseño propio por negocio)
  negocio      panel del dueño de cada negocio
  plataforma   panel de RF para administrar todos los negocios
"""
import os
from flask import Flask, render_template, session
from werkzeug.middleware.proxy_fix import ProxyFix
from . import ayudas, cliente, db as base, negocio, plataforma
from .presentacion import avatar
from .graficos import barras
from .seguridad import csrf_input, proteger_pedido, token_csrf


def create_app():
    app = Flask(__name__)
    en_render = bool(os.environ.get("RENDER"))
    secreto = os.environ.get("SECRET_KEY", "")
    if en_render and (not secreto or not os.environ.get("ADMIN_PASSWORD") or os.environ.get("MODO_DEV") == "1"):
        raise RuntimeError("Antes de publicar: definí SECRET_KEY y ADMIN_PASSWORD en Environment, y quitá MODO_DEV.")
    app.secret_key = secreto or "solo-desarrollo-local"
    app.config.update(
        MAX_CONTENT_LENGTH=1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=en_render, PERMANENT_SESSION_LIFETIME=12 * 3600,
        SDM_META_KEY=os.environ.get("SDM_META_KEY", ""), SDM_FILE_KEY=os.environ.get("SDM_FILE_KEY", ""))
    if en_render:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)  # detrás del proxy HTTPS de Render

    base.init_db()
    app.teardown_appcontext(base.cerrar)
    app.before_request(proteger_pedido)
    ayudas.registrar(app)
    app.jinja_env.globals.update(csrf_input=csrf_input, avatar=avatar, barras=barras)
    for modulo in (cliente, plataforma, negocio):
        app.register_blueprint(modulo.bp)
    negocio.registrar_redirecciones(app)
    if os.environ.get("MODO_DEV") == "1":
        cliente.registrar_modo_dev(app)

    @app.after_request
    def cabeceras(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if session.get("rol"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def error(e):
        textos = {403: ("Acción no permitida", "Tu sesión venció o el pedido no es válido. Volvé atrás y probá de nuevo."),
                  404: ("No encontramos esa página", "Revisá la dirección o volvé al inicio."),
                  413: ("El archivo es muy pesado", "Elegí uno de menos de 1 MB.")}
        t, d = textos[e.code]
        return render_template("panel/error.html", codigo=e.code, titulo=t, texto=d), e.code

    return app
