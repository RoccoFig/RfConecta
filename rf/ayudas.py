"""Pequeñas funciones que usan las plantillas: fechas relativas, enlaces de paginación, etc."""
import time
from flask import request, url_for
from .db import ahora


def hace(ts):
    if not ts:
        return "—"
    d = ahora() - ts
    if d < 60:
        return "ahora"
    if d < 3600:
        return f"hace {d // 60} min"
    if d < 86400:
        return f"hace {d // 3600} h"
    if d < 7 * 86400:
        return f"hace {d // 86400} d"
    return fecha(ts, hora=False)


def fecha(ts, hora=True):
    if not ts:
        return "—"
    return time.strftime("%d/%m/%Y %H:%M" if hora else "%d/%m/%Y", time.gmtime(ts - 10800))


def pagina_url(n):
    args = {**request.view_args, **request.args.to_dict(), "pagina": n}
    return url_for(request.endpoint, **args)


def registrar(app):
    app.jinja_env.globals.update(hace=hace, fecha=fecha, pagina_url=pagina_url)
