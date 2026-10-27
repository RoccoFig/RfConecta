"""El motor de fidelización: reglas de puntos y sellos, canjes y todas las consultas de estadísticas.
No sabe nada de pantallas: lo usan por igual el cliente, el negocio y la plataforma."""
import os, secrets, time
from flask import abort
from .db import db, ahora, COLS_COM

PUNTOS_POR_VISITA = 10
SELLOS_POR_TARJETA = 8
PREMIO_TARJETA = "Premio por tarjeta completa"
COOLDOWN_HORAS = float(os.environ.get("COOLDOWN_HORAS", 8))  # una suma por visita cada X horas (anti-abuso)
DIA = 86400


def un(sql, *a):
    return db().execute(sql, a).fetchone()[0]


def comercio(slug):
    c = db().execute(f"SELECT {COLS_COM} FROM comercios WHERE slug=?", (slug,)).fetchone()
    return c or abort(404)


# ---------- reglas, puntos y sellos ----------
def saldo(cid):
    return un("SELECT COALESCE(SUM(delta),0) FROM puntos_mov WHERE cliente_id=?", cid)


def visitas(cid):
    return un("SELECT COUNT(*) FROM puntos_mov WHERE cliente_id=? AND motivo='visita'", cid)


def reglas(com_id):
    r = db().execute("SELECT puntos_visita, sellos_tarjeta, premio_tarjeta FROM comercios WHERE id=?",
                     (com_id,)).fetchone()
    return (r["puntos_visita"] or PUNTOS_POR_VISITA, r["sellos_tarjeta"] or SELLOS_POR_TARJETA,
            r["premio_tarjeta"] or PREMIO_TARJETA)


def premio_tarjeta(com_id):
    nombre = reglas(com_id)[2]
    p = db().execute("SELECT id FROM premios WHERE comercio_id=? AND nombre=? AND activo=0", (com_id, nombre)).fetchone()
    if p:
        return p["id"]
    return db().execute("INSERT INTO premios(comercio_id,nombre,costo,activo) VALUES(?,?,0,0)", (com_id, nombre)).lastrowid


def sumar_visita(cid):
    ult = un("SELECT MAX(creado) FROM puntos_mov WHERE cliente_id=? AND motivo='visita'", cid)
    if ult and ahora() - ult < COOLDOWN_HORAS * 3600:
        return False
    com_id = un("SELECT comercio_id FROM clientes WHERE id=?", cid)
    puntos, sellos, _ = reglas(com_id)
    db().execute("INSERT INTO puntos_mov(cliente_id,delta,motivo,creado) VALUES(?,?,?,?)", (cid, puntos, "visita", ahora()))
    if visitas(cid) % sellos == 0:  # tarjeta completa: premio automático
        db().execute("INSERT INTO canjes(cliente_id,premio_id,codigo,creado) VALUES(?,?,?,?)",
                     (cid, premio_tarjeta(com_id), secrets.token_hex(3).upper(), ahora()))
    db().commit()
    return True


def canjear(cid, premio):
    """Descuenta los puntos y genera el código. Devuelve True si alcanzaban."""
    if saldo(cid) < premio["costo"]:
        return False
    db().execute("INSERT INTO puntos_mov(cliente_id,delta,motivo,creado) VALUES(?,?,?,?)",
                 (cid, -premio["costo"], "canje", ahora()))
    db().execute("INSERT INTO canjes(cliente_id,premio_id,codigo,creado) VALUES(?,?,?,?)",
                 (cid, premio["id"], secrets.token_hex(3).upper(), ahora()))
    db().commit()
    return True


def entregar_canje(codigo, cid=None):
    filtro, p = ("AND cliente_id IN (SELECT id FROM clientes WHERE comercio_id=?)", [cid]) if cid else ("", [])
    cur = db().execute(f"UPDATE canjes SET estado='usado', entregado_en=? WHERE codigo=? AND estado='pendiente' {filtro}",
                       [ahora(), codigo.strip().upper()] + p)
    db().commit()
    return cur.rowcount > 0


# ---------- estadísticas ----------
def serie_visitas(dias, cid=None):
    now = ahora()
    filtro, p = ("AND c.comercio_id=?", [cid]) if cid else ("", [])
    por_dia = dict(db().execute(f"""SELECT date(m.creado-10800,'unixepoch'), COUNT(*) FROM puntos_mov m
        JOIN clientes c ON c.id=m.cliente_id WHERE m.motivo='visita' AND m.creado>? {filtro} GROUP BY 1""",
                                [now - (dias + 1) * DIA] + p).fetchall())
    etiquetas, valores = [], []
    for i in range(dias - 1, -1, -1):
        t = time.gmtime(now - 10800 - i * DIA)
        etiquetas.append(time.strftime("%d/%m", t))
        valores.append(por_dia.get(time.strftime("%Y-%m-%d", t), 0))
    return etiquetas, valores


def kpis(cid=None):
    now = ahora()
    f, p = (" AND c.comercio_id=?", [cid]) if cid else ("", [])
    def n(sql, *extra):
        return db().execute(sql, list(extra) + p).fetchone()[0]
    vis = ("SELECT COUNT(*) FROM puntos_mov m JOIN clientes c ON c.id=m.cliente_id "
           "WHERE m.motivo='visita' AND m.creado>? AND m.creado<=?" + f)
    k = dict(
        clientes=n("SELECT COUNT(*) FROM clientes c WHERE 1=1" + f),
        nuevos7=n("SELECT COUNT(*) FROM clientes c WHERE c.creado>?" + f, now - 7 * DIA),
        visitas7=n(vis, now - 7 * DIA, now), visitas7_prev=n(vis, now - 14 * DIA, now - 7 * DIA),
        visitas30=n(vis, now - 30 * DIA, now),
        puntos30=n("SELECT COALESCE(SUM(m.delta),0) FROM puntos_mov m JOIN clientes c ON c.id=m.cliente_id "
                   "WHERE m.motivo='visita' AND m.creado>?" + f, now - 30 * DIA),
        canjeados30=n("SELECT COALESCE(-SUM(m.delta),0) FROM puntos_mov m JOIN clientes c ON c.id=m.cliente_id "
                      "WHERE m.motivo='canje' AND m.creado>?" + f, now - 30 * DIA),
        pendientes=n("SELECT COUNT(*) FROM canjes k JOIN clientes c ON c.id=k.cliente_id WHERE k.estado='pendiente'" + f),
        pendientes_viejos=n("SELECT COUNT(*) FROM canjes k JOIN clientes c ON c.id=k.cliente_id "
                            "WHERE k.estado='pendiente' AND k.creado<?" + f, now - 2 * DIA),
        entregados=n("SELECT COUNT(*) FROM canjes k JOIN clientes c ON c.id=k.cliente_id WHERE k.estado='usado'" + f),
        con_visita=n("SELECT COUNT(*) FROM clientes c WHERE (SELECT COUNT(*) FROM puntos_mov m WHERE m.cliente_id=c.id "
                     "AND m.motivo='visita')>=1" + f),
        recurrentes=n("SELECT COUNT(*) FROM clientes c WHERE (SELECT COUNT(*) FROM puntos_mov m WHERE m.cliente_id=c.id "
                      "AND m.motivo='visita')>=2" + f))
    k["retorno"] = round(100 * k["recurrentes"] / k["con_visita"]) if k["con_visita"] else None
    prev = k["visitas7_prev"]
    k["delta_visitas"] = round(100 * (k["visitas7"] - prev) / prev) if prev else None
    if not cid:
        k["negocios"] = un("SELECT COUNT(*) FROM comercios")
        k["negocios_activos"] = un("SELECT COUNT(*) FROM comercios WHERE estado='activo'")
    return k


def negocios_resumen():
    now = ahora()
    return db().execute(f"""SELECT {COLS_COM},
        (SELECT COUNT(*) FROM clientes c WHERE c.comercio_id=co.id) AS nclientes,
        (SELECT COUNT(*) FROM puntos_mov m JOIN clientes c ON c.id=m.cliente_id WHERE c.comercio_id=co.id
            AND m.motivo='visita' AND m.creado>?) AS visitas30,
        (SELECT MAX(m.creado) FROM puntos_mov m JOIN clientes c ON c.id=m.cliente_id WHERE c.comercio_id=co.id
            AND m.motivo='visita') AS ultima,
        (SELECT COUNT(*) FROM canjes k JOIN clientes c ON c.id=k.cliente_id WHERE c.comercio_id=co.id
            AND k.estado='pendiente') AS pendientes,
        (SELECT COUNT(*) FROM tags t WHERE t.comercio_id=co.id AND t.activo=1) AS ntags
        FROM comercios co ORDER BY co.id""", (now - 30 * DIA,)).fetchall()


def alertas_plataforma():
    out, now = [], ahora()
    for n in negocios_resumen():
        if n["estado"] != "activo":
            continue
        base = dict(slug=n["slug"], nombre=n["nombre"])
        if not n["ntags"]:
            out.append(dict(base, nivel="warn", texto="No tiene carteles NFC registrados", enlace="carteles"))
        if not db().execute("SELECT 1 FROM premios WHERE comercio_id=? AND activo=1", (n["id"],)).fetchone():
            out.append(dict(base, nivel="warn", texto="No tiene premios cargados", enlace="premios"))
        if n["nclientes"] and (not n["ultima"] or now - n["ultima"] > 14 * DIA):
            out.append(dict(base, nivel="warn", texto="Sin visitas en los últimos 14 días", enlace="resumen"))
        viejos = un("""SELECT COUNT(*) FROM canjes k JOIN clientes c ON c.id=k.cliente_id
                       WHERE c.comercio_id=? AND k.estado='pendiente' AND k.creado<?""", n["id"], now - 2 * DIA)
        if viejos:
            out.append(dict(base, nivel="bad", texto=f"{viejos} canje(s) pendientes hace más de 2 días", enlace="canjes"))
    return out


def ranking_negocios(limite=5):
    return [n for n in sorted(negocios_resumen(), key=lambda r: -r["visitas30"]) if n["visitas30"]][:limite]


def premios_populares(cid, limite=5):
    return db().execute("""SELECT p.nombre, COUNT(*) AS n FROM canjes k JOIN premios p ON p.id=k.premio_id
        JOIN clientes c ON c.id=k.cliente_id WHERE c.comercio_id=? AND p.activo!=0 GROUP BY p.id
        ORDER BY n DESC LIMIT ?""", (cid, limite)).fetchall()


# ---------- listados con filtros y paginación ----------
def _pagina(sql_lista, sql_cuenta, params, pagina, por_pagina):
    total = db().execute(sql_cuenta, params).fetchone()[0]
    paginas = max(1, -(-total // por_pagina))
    pagina = min(max(pagina, 1), paginas)
    filas = db().execute(sql_lista + " LIMIT ? OFFSET ?", params + [por_pagina, (pagina - 1) * por_pagina]).fetchall()
    return dict(filas=filas, total=total, pagina=pagina, paginas=paginas)


_NEG = "co.nombre AS negocio, co.slug AS slug, co.tema AS tema, co.id AS cid, (co.logo_img IS NOT NULL) AS tiene_logo, co.logo_ver AS logo_ver"


def actividad(cid=None, tipo=None, q=None, pagina=1, por_pagina=20):
    partes = {
        "visita": ("SELECT m.creado AS t, 'visita' AS tipo, c.nombre AS cliente, c.id AS cliente_id, " + _NEG +
                   ", ('+' || m.delta || ' puntos') AS detalle FROM puntos_mov m JOIN clientes c ON c.id=m.cliente_id "
                   "JOIN comercios co ON co.id=c.comercio_id WHERE m.motivo='visita'"),
        "canje": ("SELECT k.creado AS t, 'canje' AS tipo, c.nombre AS cliente, c.id AS cliente_id, " + _NEG + ", (p.nombre || ' · ' || k.estado) AS detalle "
                  "FROM canjes k JOIN clientes c ON c.id=k.cliente_id JOIN premios p ON p.id=k.premio_id "
                  "JOIN comercios co ON co.id=c.comercio_id WHERE 1=1"),
        "registro": ("SELECT c.creado AS t, 'registro' AS tipo, c.nombre AS cliente, c.id AS cliente_id, " + _NEG + ", 'Nuevo cliente' AS detalle FROM clientes c "
                     "JOIN comercios co ON co.id=c.comercio_id WHERE 1=1")}
    sqls, params = [], []
    for t, sql in partes.items():
        if tipo and tipo != t:
            continue
        if cid:
            sql += " AND co.id=?"; params.append(cid)
        if q:
            sql += " AND c.nombre LIKE ?"; params.append(f"%{q}%")
        sqls.append(sql)
    union = " UNION ALL ".join(sqls)
    return _pagina(f"SELECT * FROM ({union}) ORDER BY t DESC", f"SELECT COUNT(*) FROM ({union})", params, pagina, por_pagina)


def clientes_lista(cid=None, q=None, orden="recientes", pagina=1, por_pagina=15):
    where, p = ["1=1"], []
    if cid:
        where.append("c.comercio_id=?"); p.append(cid)
    if q:
        where.append("(c.nombre LIKE ? OR c.telefono LIKE ?)"); p += [f"%{q}%", f"%{q}%"]
    orden_sql = {"recientes": "c.creado DESC", "puntos": "puntos DESC", "visitas": "visitas DESC",
                 "nombre": "c.nombre COLLATE NOCASE"}.get(orden, "c.creado DESC")
    base = ("SELECT c.id, c.nombre, c.telefono, c.email, c.creado, c.acepta_whatsapp, " + _NEG + ", "
            "(SELECT COALESCE(SUM(delta),0) FROM puntos_mov WHERE cliente_id=c.id) AS puntos, "
            "(SELECT COUNT(*) FROM puntos_mov WHERE cliente_id=c.id AND motivo='visita') AS visitas, "
            "(SELECT MAX(creado) FROM puntos_mov WHERE cliente_id=c.id AND motivo='visita') AS ultima "
            "FROM clientes c JOIN comercios co ON co.id=c.comercio_id WHERE " + " AND ".join(where))
    return _pagina(base + f" ORDER BY {orden_sql}", "SELECT COUNT(*) FROM clientes c WHERE " + " AND ".join(where), p, pagina, por_pagina)


def canjes_lista(cid=None, estado=None, q=None, pagina=1, por_pagina=15):
    where, p = ["1=1"], []
    if cid:
        where.append("co.id=?"); p.append(cid)
    if estado in ("pendiente", "usado"):
        where.append("k.estado=?"); p.append(estado)
    if q:
        where.append("(c.nombre LIKE ? OR k.codigo LIKE ?)"); p += [f"%{q}%", f"%{q.upper()}%"]
    cuerpo = ("FROM canjes k JOIN clientes c ON c.id=k.cliente_id JOIN premios p ON p.id=k.premio_id "
              "JOIN comercios co ON co.id=c.comercio_id WHERE " + " AND ".join(where))
    base = ("SELECT k.id, k.codigo, k.estado, k.creado, k.entregado_en, c.nombre AS cliente, c.id AS cliente_id, "
            "p.nombre AS premio, " + _NEG + " " + cuerpo)
    return _pagina(base + " ORDER BY (k.estado='pendiente') DESC, k.creado DESC", "SELECT COUNT(*) " + cuerpo, p, pagina, por_pagina)


def tags_lista(cid=None):
    w, p = ("WHERE t.comercio_id=?", [cid]) if cid else ("", [])
    return db().execute(f"""SELECT t.id, t.uid, t.activo, t.ultimo_contador, t.ultimo_escaneo, {_NEG}
        FROM tags t JOIN comercios co ON co.id=t.comercio_id {w} ORDER BY t.activo DESC, t.id DESC""", p).fetchall()
