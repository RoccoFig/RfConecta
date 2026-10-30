"""El motor de fidelización: reglas de puntos y sellos, canjes, auditoría y todas las consultas de estadísticas.
No sabe nada de pantallas. TODA modificación de puntos, sellos o canjes pasa por las funciones de este archivo:
las valida en el servidor, las hace en una transacción y deja registro."""
import json, logging, os, secrets, sqlite3, time
from flask import abort, request
from .db import db, ahora, COLS_COM, transaccion

PUNTOS_POR_VISITA = 10
SELLOS_POR_TARJETA = 8
PREMIO_TARJETA = "Premio por tarjeta completa"
INTERVALO_HORAS = float(os.environ.get("COOLDOWN_HORAS", 8))  # valor por defecto; cada negocio puede tener el suyo
SOLICITUD_MINUTOS = 5       # vigencia del código que el cliente muestra en caja
CANJE_VENCE_DIAS = 30       # vigencia de un premio canjeado
MAX_AJUSTE = 1000           # tope de un ajuste manual de puntos
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O/1/I para que no se confundan al dictar
DIA = 86400
log = logging.getLogger("rf.seguridad")


def un(sql, *a):
    return db().execute(sql, a).fetchone()[0]


def comercio(slug):
    c = db().execute(f"SELECT {COLS_COM} FROM comercios WHERE slug=?", (slug,)).fetchone()
    return c or abort(404)


def codigo_corto(n=8):
    return "".join(secrets.choice(ALFABETO) for _ in range(n))


# ---------- auditoría ----------
def auditar(accion, nivel="info", actor=None, com_id=None, objetivo=None, detalle=None):
    """Deja una fila en la tabla de auditoría y una línea de registro estructurada. Nunca guarda claves ni tokens."""
    try:
        ip = request.remote_addr
    except RuntimeError:
        ip = None
    db().execute("INSERT INTO auditoria(t,nivel,actor,accion,comercio_id,objetivo,detalle,ip) VALUES(?,?,?,?,?,?,?,?)",
                 (ahora(), nivel, actor, accion, com_id, objetivo, detalle, ip))
    from flask import g
    if not g.get("en_tx"):
        db().commit()
    log.log(logging.INFO if nivel == "info" else logging.WARNING,
            json.dumps({"evento": accion, "nivel": nivel, "actor": actor, "negocio": com_id, "objetivo": objetivo,
                        "detalle": detalle}, ensure_ascii=False))


# ---------- reglas de cada negocio ----------
def config(com_id):
    r = db().execute("""SELECT puntos_visita, sellos_tarjeta, premio_tarjeta, intervalo_horas, max_visitas_dia,
                        canje_vence_dias FROM comercios WHERE id=?""", (com_id,)).fetchone()
    return dict(puntos=r["puntos_visita"] or PUNTOS_POR_VISITA, sellos=r["sellos_tarjeta"] or SELLOS_POR_TARJETA,
                premio=r["premio_tarjeta"] or PREMIO_TARJETA,
                intervalo=r["intervalo_horas"] if r["intervalo_horas"] is not None else INTERVALO_HORAS,
                max_dia=r["max_visitas_dia"] or 0, vence_dias=r["canje_vence_dias"] or CANJE_VENCE_DIAS)


def reglas(com_id):
    c = config(com_id)
    return (c["puntos"], c["sellos"], c["premio"])


def saldo(cid):
    return un("SELECT COALESCE(SUM(delta),0) FROM puntos_mov WHERE cliente_id=?", cid)


def visitas(cid):
    return un("SELECT COUNT(*) FROM puntos_mov WHERE cliente_id=? AND motivo='visita'", cid)


def elegibilidad(cid, cfg, now=None):
    """¿Puede este cliente sumar una visita ahora? Devuelve (True, None) o (False, {motivo, proxima})."""
    now = now or ahora()
    ult = un("SELECT MAX(creado) FROM puntos_mov WHERE cliente_id=? AND motivo='visita'", cid)
    if ult and now - ult < cfg["intervalo"] * 3600:
        return False, dict(motivo="intervalo", proxima=int(ult + cfg["intervalo"] * 3600))
    if cfg["max_dia"]:
        inicio = (now - 10800) // DIA * DIA + 10800  # medianoche de Argentina
        if un("SELECT COUNT(*) FROM puntos_mov WHERE cliente_id=? AND motivo='visita' AND creado>=?", cid, inicio) >= cfg["max_dia"]:
            return False, dict(motivo="limite_diario", proxima=inicio + DIA)
    return True, None


def premio_tarjeta(com_id):
    nombre = config(com_id)["premio"]
    p = db().execute("SELECT id FROM premios WHERE comercio_id=? AND nombre=? AND activo=0", (com_id, nombre)).fetchone()
    if p:
        return p["id"]
    return db().execute("INSERT INTO premios(comercio_id,nombre,costo,activo) VALUES(?,?,0,0)", (com_id, nombre)).lastrowid


def _crear_canje(cid, com_id, premio_id, costo, cfg):
    for _ in range(8):
        codigo = codigo_corto()
        try:
            cur = db().execute("""INSERT INTO canjes(cliente_id,premio_id,codigo,creado,comercio_id,vence_en,costo)
                                  VALUES(?,?,?,?,?,?,?)""", (cid, premio_id, codigo, ahora(), com_id, ahora() + cfg["vence_dias"] * DIA, costo))
            return cur.lastrowid, codigo
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("No se pudo generar un código único")


def _movimiento(cid, com_id, delta, motivo, fuente, actor, idem=None, ref=None, nota=None):
    db().execute("""INSERT INTO puntos_mov(cliente_id,comercio_id,delta,motivo,creado,fuente,actor,clave_idem,ref,nota)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""", (cid, com_id, delta, motivo, ahora(), fuente, actor, idem, ref, nota))


# ---------- visitas: la única puerta por la que se suman puntos y sellos ----------
def _visita(cid, fuente, actor, idem=None):
    cli = db().execute("SELECT id, comercio_id FROM clientes WHERE id=?", (cid,)).fetchone()
    if not cli:
        return dict(estado="error", motivo="cliente")
    if idem and un("SELECT COUNT(*) FROM puntos_mov WHERE clave_idem=?", idem):
        return dict(estado="duplicada")
    cfg = config(cli["comercio_id"])
    ok, info = elegibilidad(cid, cfg)
    if not ok:
        auditar("visita_rechazada", "info", actor, cli["comercio_id"], f"cliente:{cid}", f"{fuente}:{info['motivo']}")
        return dict(estado="rechazada", **info)
    _movimiento(cid, cli["comercio_id"], cfg["puntos"], "visita", fuente, actor, idem)
    completa = visitas(cid) % cfg["sellos"] == 0
    if completa:  # tarjeta completa: premio automático
        _crear_canje(cid, cli["comercio_id"], premio_tarjeta(cli["comercio_id"]), 0, cfg)
    return dict(estado="ok", puntos=cfg["puntos"], completa=completa)


def registrar_visita(cid, fuente, actor, idem=None):
    """fuente: 'nfc' (cartel firmado), 'qr_caja' (confirmada por el personal), 'dev' (solo pruebas)."""
    try:
        with transaccion():
            return _visita(cid, fuente, actor, idem)
    except sqlite3.IntegrityError:
        return dict(estado="duplicada")


# ---------- canje de premios con puntos ----------
def canjear(cid, premio_id, nonce):
    actor = f"cliente:{cid}"
    try:
        with transaccion():
            cli = db().execute("SELECT id, comercio_id FROM clientes WHERE id=?", (cid,)).fetchone()
            prem = db().execute("SELECT * FROM premios WHERE id=? AND comercio_id=? AND activo=1 AND costo>0",
                                (premio_id, cli["comercio_id"])).fetchone() if cli else None
            if not prem:
                return dict(estado="no_existe")
            idem = f"canje:{cid}:{nonce or secrets.token_hex(8)}"
            if un("SELECT COUNT(*) FROM puntos_mov WHERE clave_idem=?", idem):
                return dict(estado="duplicado")
            cfg = config(cli["comercio_id"])
            canje_id, codigo = _crear_canje(cid, cli["comercio_id"], prem["id"], prem["costo"], cfg)
            _movimiento(cid, cli["comercio_id"], -prem["costo"], "canje", "cliente", actor, idem, f"canje:{canje_id}")
            return dict(estado="ok", codigo=codigo, premio=prem["nombre"])
    except sqlite3.IntegrityError as e:
        if "Saldo insuficiente" in str(e):
            return dict(estado="saldo")
        raise


def entregar_canje(codigo, com_id, actor):
    """El personal del negocio entrega el premio. Solo sirve para canjes de ese negocio, una única vez y sin vencer."""
    codigo = (codigo or "").strip().upper()
    with transaccion():
        k = db().execute("""SELECT k.*, p.nombre AS premio, c.nombre AS cliente FROM canjes k JOIN premios p ON p.id=k.premio_id
                            JOIN clientes c ON c.id=k.cliente_id WHERE k.codigo=? AND k.comercio_id=?""", (codigo, com_id)).fetchone()
        if not k:
            auditar("canje_rechazado", "aviso", actor, com_id, None, "codigo_desconocido")
            return dict(estado="no_existe")
        motivo = {"usado": "ya_usado", "cancelado": "cancelado"}.get(k["estado"])
        if not motivo and k["vence_en"] and k["vence_en"] < ahora():
            motivo = "vencido"
        if motivo:
            auditar("canje_rechazado", "aviso", actor, com_id, f"canje:{k['id']}", motivo)
            return dict(estado=motivo, canje=k)
        cur = db().execute("UPDATE canjes SET estado='usado', entregado_en=?, resuelto_por=? WHERE id=? AND estado='pendiente'",
                           (ahora(), actor, k["id"]))
        if not cur.rowcount:
            return dict(estado="ya_usado", canje=k)
        auditar("canje_entregado", "info", actor, com_id, f"canje:{k['id']}")
        return dict(estado="ok", canje=k)


def cancelar_canje(canje_id, com_id, actor, motivo=""):
    """Anula un canje pendiente (o vencido) y devuelve los puntos al cliente, dejando todo registrado."""
    with transaccion():
        k = db().execute("SELECT * FROM canjes WHERE id=? AND comercio_id=?", (canje_id, com_id)).fetchone()
        if not k or k["estado"] != "pendiente":
            return dict(estado="no_cancelable")
        cur = db().execute("UPDATE canjes SET estado='cancelado', entregado_en=?, resuelto_por=? WHERE id=? AND estado='pendiente'",
                           (ahora(), actor, canje_id))
        if not cur.rowcount:
            return dict(estado="no_cancelable")
        if k["costo"] > 0:
            _movimiento(k["cliente_id"], com_id, k["costo"], "devolucion", "manual", actor, f"cancela:{canje_id}", f"canje:{canje_id}", motivo[:120] or None)
        auditar("canje_cancelado", "aviso", actor, com_id, f"canje:{canje_id}", f"devuelve {k['costo']} puntos")
        return dict(estado="ok", devuelto=k["costo"])


def ajustar_puntos(cid, com_id, delta, motivo, actor):
    """Corrección manual del saldo. Exige motivo, tiene tope y queda en el historial y la auditoría."""
    motivo = (motivo or "").strip()
    if not (3 <= len(motivo) <= 120):
        return dict(estado="motivo")
    if not delta or abs(delta) > MAX_AJUSTE:
        return dict(estado="monto")
    try:
        with transaccion():
            if not db().execute("SELECT 1 FROM clientes WHERE id=? AND comercio_id=?", (cid, com_id)).fetchone():
                return dict(estado="no_existe")
            _movimiento(cid, com_id, delta, "ajuste", "manual", actor, nota=motivo)
            auditar("ajuste_manual", "aviso" if abs(delta) >= 100 else "info", actor, com_id, f"cliente:{cid}", f"{delta:+d}: {motivo}")
            return dict(estado="ok")
    except sqlite3.IntegrityError as e:
        if "Saldo insuficiente" in str(e):
            return dict(estado="saldo")
        raise


# ---------- visita pedida desde el celular (QR) y confirmada por el personal ----------
def crear_solicitud(cid):
    """El cliente pide registrar su visita. Genera un código corto, de un solo uso y con vencimiento."""
    with transaccion():
        cli = db().execute("SELECT id, comercio_id FROM clientes WHERE id=?", (cid,)).fetchone()
        if not cli:
            return dict(estado="error")
        now, cfg = ahora(), config(cli["comercio_id"])
        db().execute("UPDATE solicitudes_visita SET estado='vencida' WHERE estado='pendiente' AND vence_en<=?", (now,))
        ex = db().execute("SELECT * FROM solicitudes_visita WHERE cliente_id=? AND estado='pendiente' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        if ex:
            return dict(estado="ok", solicitud=ex)
        ok, info = elegibilidad(cid, cfg)
        if not ok:
            return dict(estado="rechazada", **info)
        if un("SELECT COUNT(*) FROM solicitudes_visita WHERE cliente_id=? AND creado>?", cid, now - 3600) >= 6:
            auditar("solicitudes_excesivas", "aviso", f"cliente:{cid}", cli["comercio_id"], f"cliente:{cid}")
            return dict(estado="limite")
        for _ in range(8):
            try:
                cur = db().execute("INSERT INTO solicitudes_visita(comercio_id,cliente_id,codigo,creado,vence_en) VALUES(?,?,?,?,?)",
                                   (cli["comercio_id"], cid, codigo_corto(6), now, now + SOLICITUD_MINUTOS * 60))
                return dict(estado="ok", solicitud=db().execute("SELECT * FROM solicitudes_visita WHERE id=?", (cur.lastrowid,)).fetchone())
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("No se pudo generar un código único")


def solicitud_activa(cid):
    return db().execute("SELECT * FROM solicitudes_visita WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()


def solicitud_para_caja(com_id, codigo):
    """Datos que ve el personal antes de confirmar. Solo del negocio de quien consulta; el teléfono va enmascarado."""
    codigo = (codigo or "").strip().upper()
    s = db().execute("""SELECT s.*, c.nombre AS cliente, c.telefono FROM solicitudes_visita s JOIN clientes c ON c.id=s.cliente_id
                        WHERE s.codigo=? AND s.comercio_id=? AND s.estado='pendiente' AND s.vence_en>?""", (codigo, com_id, ahora())).fetchone()
    if not s:
        return None
    ok, info = elegibilidad(s["cliente_id"], config(com_id))
    return dict(s, telefono_masc="•••• " + s["telefono"][-4:], puntos=saldo(s["cliente_id"]), visitas=visitas(s["cliente_id"]), elegible=ok, motivo=info)


def solicitudes_pendientes(com_id):
    return db().execute("""SELECT s.*, c.nombre AS cliente FROM solicitudes_visita s JOIN clientes c ON c.id=s.cliente_id
                           WHERE s.comercio_id=? AND s.estado='pendiente' AND s.vence_en>? ORDER BY s.id""", (com_id, ahora())).fetchall()


def resolver_solicitud(sol_id, com_id, actor, confirmar=True):
    """Confirma o rechaza. Confirmar suma la visita una sola vez (clave idempotente) con las mismas reglas que el cartel NFC."""
    try:
        with transaccion():
            s = db().execute("SELECT * FROM solicitudes_visita WHERE id=? AND comercio_id=?", (sol_id, com_id)).fetchone()
            if not s:
                auditar("solicitud_rechazada", "aviso", actor, com_id, f"solicitud:{sol_id}", "no_pertenece_al_negocio")
                return dict(estado="no_existe")
            if s["estado"] != "pendiente":
                return dict(estado="ya_resuelta")
            if s["vence_en"] <= ahora():
                db().execute("UPDATE solicitudes_visita SET estado='vencida' WHERE id=?", (sol_id,))
                return dict(estado="vencida")
            if not confirmar:
                db().execute("UPDATE solicitudes_visita SET estado='rechazada', resuelto_en=?, resuelto_por=? WHERE id=?", (ahora(), actor, sol_id))
                auditar("solicitud_rechazada_por_personal", "info", actor, com_id, f"solicitud:{sol_id}")
                return dict(estado="rechazada")
            r = _visita(s["cliente_id"], "qr_caja", actor, f"sol:{sol_id}")
            nuevo = "confirmada" if r["estado"] == "ok" else "rechazada"
            db().execute("UPDATE solicitudes_visita SET estado=?, resuelto_en=?, resuelto_por=?, motivo=? WHERE id=? AND estado='pendiente'",
                         (nuevo, ahora(), actor, r.get("motivo"), sol_id))
            return dict(r, cliente_id=s["cliente_id"])
    except sqlite3.IntegrityError:
        return dict(estado="ya_resuelta")


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
    if estado in ("pendiente", "usado", "cancelado"):
        where.append("k.estado=?"); p.append(estado)
    if q:
        where.append("(c.nombre LIKE ? OR k.codigo LIKE ?)"); p += [f"%{q}%", f"%{q.upper()}%"]
    cuerpo = ("FROM canjes k JOIN clientes c ON c.id=k.cliente_id JOIN premios p ON p.id=k.premio_id "
              "JOIN comercios co ON co.id=c.comercio_id WHERE " + " AND ".join(where))
    base = ("SELECT k.id, k.codigo, k.estado, k.creado, k.entregado_en, k.vence_en, k.costo, c.nombre AS cliente, c.id AS cliente_id, "
            "p.nombre AS premio, " + _NEG + " " + cuerpo)
    return _pagina(base + " ORDER BY (k.estado='pendiente') DESC, k.creado DESC", "SELECT COUNT(*) " + cuerpo, p, pagina, por_pagina)


def tags_lista(cid=None):
    w, p = ("WHERE t.comercio_id=?", [cid]) if cid else ("", [])
    return db().execute(f"""SELECT t.id, t.uid, t.activo, t.ultimo_contador, t.ultimo_escaneo, {_NEG}
        FROM tags t JOIN comercios co ON co.id=t.comercio_id {w} ORDER BY t.activo DESC, t.id DESC""", p).fetchall()


# ---------- integridad y seguridad ----------
def verificar_integridad():
    """Controles que deberían dar siempre cero problemas. Devuelve la lista de problemas encontrados."""
    p = []
    r = db().execute("PRAGMA integrity_check").fetchone()[0]
    if r != "ok":
        p.append(f"La base de datos reporta daños: {r}")
    n = un("SELECT COUNT(*) FROM (SELECT cliente_id FROM puntos_mov GROUP BY cliente_id HAVING SUM(delta)<0)")
    if n:
        p.append(f"{n} cliente(s) con saldo negativo")
    if un("SELECT COUNT(*) FROM puntos_mov m JOIN clientes c ON c.id=m.cliente_id WHERE m.comercio_id IS NOT c.comercio_id"):
        p.append("Hay movimientos de puntos asignados a un negocio distinto del de su cliente")
    if un("SELECT COUNT(*) FROM puntos_mov WHERE cliente_id NOT IN (SELECT id FROM clientes)"):
        p.append("Hay movimientos de puntos sin cliente")
    if un("""SELECT COUNT(*) FROM puntos_mov m WHERE m.ref LIKE 'canje:%' AND m.delta<0
             AND NOT EXISTS (SELECT 1 FROM canjes k WHERE 'canje:'||k.id=m.ref)"""):
        p.append("Hay descuentos de puntos de canjes que no existen")
    if un("SELECT COUNT(*) FROM canjes WHERE estado='usado' AND entregado_en IS NULL"):
        p.append("Hay canjes entregados sin fecha de entrega")
    if un("SELECT COUNT(*) FROM canjes k WHERE k.comercio_id IS NOT (SELECT comercio_id FROM clientes WHERE id=k.cliente_id)"):
        p.append("Hay canjes asignados a un negocio distinto del de su cliente")
    return p


def indicadores_seguridad(dias=7):
    """Señales para revisar, no acusaciones: un número alto indica que conviene mirar, no que haya fraude."""
    desde = ahora() - dias * DIA
    cuentas = dict(db().execute("SELECT accion, COUNT(*) FROM auditoria WHERE t>? GROUP BY accion", (desde,)).fetchall())
    reintentos = db().execute("""SELECT a.objetivo, a.comercio_id, co.nombre AS negocio, COUNT(*) AS n FROM auditoria a
        LEFT JOIN comercios co ON co.id=a.comercio_id WHERE a.accion='visita_rechazada' AND a.t>? AND a.objetivo LIKE 'cliente:%'
        GROUP BY a.objetivo HAVING n>=5 ORDER BY n DESC LIMIT 5""", (desde,)).fetchall()
    return dict(cuentas=cuentas, reintentos=reintentos,
                ajustes=db().execute("""SELECT a.t, a.actor, a.detalle, co.nombre AS negocio FROM auditoria a LEFT JOIN comercios co ON co.id=a.comercio_id
                                         WHERE a.accion='ajuste_manual' AND a.t>? ORDER BY a.t DESC LIMIT 8""", (desde,)).fetchall())


def auditoria_lista(nivel=None, accion=None, cid=None, pagina=1, por_pagina=25):
    where, p = ["1=1"], []
    if nivel in ("info", "aviso", "alerta"):
        where.append("a.nivel=?"); p.append(nivel)
    if accion:
        where.append("a.accion=?"); p.append(accion)
    if cid:
        where.append("a.comercio_id=?"); p.append(cid)
    cuerpo = "FROM auditoria a LEFT JOIN comercios co ON co.id=a.comercio_id WHERE " + " AND ".join(where)
    return _pagina("SELECT a.*, co.nombre AS negocio " + cuerpo + " ORDER BY a.id DESC", "SELECT COUNT(*) " + cuerpo, p, pagina, por_pagina)
