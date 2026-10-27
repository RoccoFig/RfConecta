@app.post("/l/<slug>/baja")
def baja(slug):
    com = comercio(slug)
    c = cliente_actual(com) or abort(403)
    db().execute("UPDATE clientes SET acepta_whatsapp=0 WHERE id=?", (c["id"],))
    db().commit()
    return redirect(url_for("local", slug=slug))

def registrar_visita(c):
    sumo = sumar_visita(c["id"])
    session["resultado"] = {"sumo": sumo,
                            "completa": sumo and visitas(c["id"]) % SELLOS_POR_TARJETA == 0}

def tras_escaneo_valido(com):
    c = cliente_actual(com)
    if c:
        registrar_visita(c)
    else:
        session["scan_ok"] = com["slug"]  # su primera visita se suma al registrarse
    return redirect(url_for("local", slug=com["slug"]))

@app.route("/t")
def escaneo():
    if not (SDM_META_KEY and SDM_FILE_KEY):
        abort(503)
    try:
        uid, ctr = sdm.verificar(request.args.get("picc_data", ""), request.args.get("cmac", ""),
                                 bytes.fromhex(SDM_META_KEY), bytes.fromhex(SDM_FILE_KEY))
    except ValueError:
        abort(403)
    tag = db().execute("SELECT * FROM tags WHERE uid=? AND activo=1", (uid,)).fetchone() or abort(403)
    com = db().execute("SELECT * FROM comercios WHERE id=?", (tag["comercio_id"],)).fetchone()
    cur = db().execute("UPDATE tags SET ultimo_contador=? WHERE id=? AND ultimo_contador<?",
                       (ctr, tag["id"], ctr))
    db().commit()
    if not cur.rowcount:  # el contador no avanzó: es un escaneo repetido o copiado, no suma
        session["resultado"] = {"sumo": False, "completa": False}
        return redirect(url_for("local", slug=com["slug"]))
    return tras_escaneo_valido(com)

if MODO_DEV:
    @app.route("/dev/escanear/<slug>")
    def dev_escanear(slug):
        return tras_escaneo_valido(comercio(slug))

# ---------- panel del local ----------
def solo_admin(f):
    @wraps(f)
    def w(*a, **k):
        auth = request.authorization
        if not auth or not secrets.compare_digest(auth.password or "", ADMIN_PASSWORD):
            return Response("Acceso restringido", 401, {"WWW-Authenticate": 'Basic realm="admin"'})
        return f(*a, **k)
    return w

@app.route("/admin")
@solo_admin
def admin():
    pend = db().execute("""SELECT k.codigo, p.nombre, c.nombre AS cliente FROM canjes k
        JOIN premios p ON p.id=k.premio_id JOIN clientes c ON c.id=k.cliente_id
        WHERE k.estado='pendiente' ORDER BY k.creado""").fetchall()
    clientes = db().execute("""SELECT c.nombre, c.telefono, COALESCE(SUM(m.delta),0) AS total
        FROM clientes c LEFT JOIN puntos_mov m ON m.cliente_id=c.id GROUP BY c.id ORDER BY total DESC""").fetchall()
    ntags = db().execute("SELECT COUNT(*) FROM tags WHERE activo=1").fetchone()[0]
    return pagina("Panel", T_ADMIN, pend=pend, clientes=clientes, msg=request.args.get("m"), ntags=ntags)

@app.post("/admin/validar")
@solo_admin
def admin_validar():
    cod = request.form.get("codigo", "").strip().upper()
    cur = db().execute("UPDATE canjes SET estado='usado' WHERE codigo=? AND estado='pendiente'", (cod,))
    db().commit()
    return redirect(url_for("admin", m="Canje entregado." if cur.rowcount else "Código inválido o ya usado."))

@app.post("/admin/tags")
@solo_admin
def admin_tags():
    uid = re.sub(r"[^0-9A-Fa-f]", "", request.form.get("uid", "")).upper()
    if len(uid) != 14:
        return redirect(url_for("admin", m="El UID debe tener 14 caracteres (7 bytes)."))
    com = db().execute("SELECT id FROM comercios ORDER BY id LIMIT 1").fetchone()
    cur = db().execute("INSERT OR IGNORE INTO tags(comercio_id,uid) VALUES(?,?)", (com["id"], uid))
    db().commit()
    return redirect(url_for("admin", m="Cartel registrado." if cur.rowcount else "Ese cartel ya estaba registrado."))

@app.route("/")
def inicio():
    return """
    <!doctype html>
    <html lang="es">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>RF Conecta - Demo</title>
      <style>
        body{font:16px/1.5 system-ui,sans-serif;background:#f2f4f1;color:#16322c;
             max-width:36rem;margin:3rem auto;padding:0 1rem}
        a{display:block;background:#16322c;color:white;text-decoration:none;
          padding:.9rem 1rem;border-radius:.5rem;margin:1rem 0}
      </style>
    </head>
    <body>
      <h1>RF Conecta</h1>
      <p>Sistema de fidelización en modo de prueba.</p>
      <a href="/l/cafe-brasa">Probar Café Brasa</a>
      <a href="/l/demo">Probar Local Demo</a>
      <a href="/admin">Panel del local (requiere contraseña)</a>
    </body>
    </html>
    """

init_db()
if __name__ == "__main__":
    app.run(debug=True)
