"""TicketFlow — prototipo funcional (Flask + SQLite) con usuarios y niveles de acceso.

Roles:
  agente      trabaja tickets propios o sin asignar (toma, nota, estado), crea tickets.
  supervisor  todo lo anterior + asignar/reasignar, cerrar sin acción, reabrir, riesgo, bitácora, exportar.
  admin       todo lo anterior + gestión de usuarios.
"""
import csv, io, os, re, secrets, sqlite3, time
from functools import wraps
from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import importar as imp
import reglas

AQUI = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.secret_key = os.environ.get("TICKETFLOW_SECRET", "ticketflow-prototipo-dev")  # en producción: variable de entorno
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",  # la cookie no la lee JavaScript
                  MAX_CONTENT_LENGTH=6 * 1024 * 1024)

# Límites de entrada (validación en el servidor: el HTML del navegador no es una defensa)
MAX_MENSAJE, MAX_NOTA = 2000, 2000
RE_CLIENTE = re.compile(r"CUST_\d{5}")
REGIONES = ("NA", "EU", "MEA", "LATAM", "APAC")
INTENTOS = {}  # (usuario, ip) -> [instantes de intentos fallidos]; limita la fuerza bruta en el login
MAX_FALLOS, VENTANA = 5, 300


def limpiar_texto(s, maximo):
    """Quita caracteres de control y espacios sobrantes. NO borra '<' ni comillas: el texto se guarda
    tal cual y se escapa al mostrarlo (Jinja) y se parametriza al guardarlo (consultas con ?)."""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s or "").strip()
    return s[:maximo] if len(s) > maximo else s

ABIERTOS = "('open','in_progress','on_hold')"
ESTADOS = {"open": "Abierto", "in_progress": "En progreso", "on_hold": "En espera",
           "resolved": "Resuelto", "closed_no_action": "Cerrado sin acción"}
PRIORIDADES = {"urgent": "Urgente", "high": "Alta", "medium": "Media", "low": "Baja"}
NIVEL = {"agente": 1, "supervisor": 2, "admin": 3}
ROL_ES = {"agente": "Agente", "supervisor": "Supervisor", "admin": "Administrador"}
SLA_JOIN = "JOIN sla_objetivos s ON s.sla_plan = t.sla_plan AND s.priority = t.priority"
HORAS = "(julianday(:ahora) - julianday(t.created_at)) * 24"
VENCIDO = f"(t.status IN {ABIERTOS} AND {HORAS} > s.horas_resolucion)"
PAGINA = 25
RELOJ_TOPE = "2025-12-31T00:00:00"  # el dataset termina el 2025-12-30


# ---------- infraestructura ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(os.path.join(AQUI, "ticketflow.db"))
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def cerrar(_):
    c = g.pop("db", None)
    if c:
        c.close()


def reloj():
    """Reloj de demostración: el dataset termina en 2025, así que 'ahora' = último ticket creado."""
    if "ahora" not in g:
        # tope fijo: un lote importado con fechas futuras no debe mover el reloj y vencer todo
        g.ahora = db().execute("SELECT MAX(created_at) FROM tickets WHERE created_at <= ?", (RELOJ_TOPE,)).fetchone()[0]
    return g.ahora


def es_sup():
    return g.user is not None and NIVEL[g.user["rol"]] >= NIVEL["supervisor"]


def bitacora(accion, ticket_id=None, detalle=None, usuario=None):
    db().execute("INSERT INTO bitacora (usuario, accion, ticket_id, detalle) VALUES (?,?,?,?)",
                 (usuario or g.user["usuario"], accion, ticket_id, detalle))


@app.before_request
def cargar_usuario():
    g.user = None
    if session.get("uid"):
        g.user = db().execute("SELECT * FROM usuarios WHERE user_id = ? AND activo = 1", (session["uid"],)).fetchone()
        if not g.user:
            session.clear()
    if request.method == "POST" and not secrets.compare_digest(request.form.get("_csrf", ""), session.get("csrf", "x")):
        abort(400, "Token CSRF inválido o sesión expirada")


@app.after_request
def cabeceras(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    # Aunque alguien lograra meter un <script>, el navegador no ejecuta scripts que no sean del propio sitio
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                               "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'")
    return resp


@app.context_processor
def contexto():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return dict(user=g.get("user"), csrf=session["csrf"], ESTADOS=ESTADOS, PRIORIDADES=PRIORIDADES,
                ROL_ES=ROL_ES, ETIQUETAS=ETIQUETAS, es_sup=es_sup(), ahora=reloj() if g.get("user") else "")


ETIQUETAS = {
    "chat": "Chat", "email": "Correo", "in_app": "Dentro de la app", "phone_transcript": "Teléfono", "web_form": "Formulario web",
    "analytics_dashboard": "Panel de analítica", "api_integration": "Integración API", "billing": "Facturación",
    "data_export": "Exportación de datos", "login_auth": "Inicio de sesión", "mobile_app": "App móvil", "notifications": "Notificaciones",
    "account_access": "Acceso a cuenta", "billing_problem": "Problema de cobro", "bug": "Error (bug)", "feature_request": "Solicitud de mejora",
    "how_to": "Consulta de uso", "other": "Otro", "performance": "Rendimiento", "security_concern": "Seguridad",
    "education": "Educación", "enterprise": "Empresa", "individual": "Individual", "non_profit": "Sin fines de lucro",
    "small_business": "Pequeña empresa", "platinum": "Platinum", "gold": "Gold", "standard": "Estándar",
}


@app.template_filter("es")
def f_es(v):
    return ETIQUETAS.get(v, v) if v else "—"


@app.template_filter("horas")
def f_horas(h):
    if h is None:
        return "—"
    return f"{h / 24:.1f} d" if abs(h) >= 48 else f"{h:.1f} h"


def requiere(rol="agente"):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if not g.user:
                return redirect(url_for("login", next=request.full_path.rstrip("?")))
            if NIVEL[g.user["rol"]] < NIVEL[rol]:
                abort(403)
            return f(*a, **k)
        return w
    return deco


@app.errorhandler(403)
@app.errorhandler(400)
@app.errorhandler(404)
def error(e):
    msg = {403: "Tu nivel de acceso no permite esta acción.", 404: "No encontrado."}.get(e.code, e.description)
    return render_template("error.html", code=e.code, msg=msg), e.code


# ---------- acceso ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nombre = request.form.get("usuario", "").strip().lower()[:60]
        clave = (nombre, request.remote_addr)
        ahora_t = time.time()
        INTENTOS[clave] = [t for t in INTENTOS.get(clave, []) if ahora_t - t < VENTANA]
        if len(INTENTOS[clave]) >= MAX_FALLOS:
            flash("Demasiados intentos fallidos. Espera unos minutos e inténtalo de nuevo.", "error")
            return render_template("login.html", demo=[]), 429
        u = db().execute("SELECT * FROM usuarios WHERE usuario = ? AND activo = 1", (nombre,)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            INTENTOS.pop(clave, None)
            session.clear()
            session["uid"] = u["user_id"]
            bitacora("login", usuario=u["usuario"])
            db().commit()
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("panel"))
        INTENTOS.setdefault(clave, []).append(ahora_t)
        flash("Usuario o contraseña incorrectos.", "error")
    demo = db().execute("SELECT usuario, nombre, rol FROM usuarios WHERE activo = 1 ORDER BY user_id").fetchall()
    return render_template("login.html", demo=demo)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- panel ----------
@app.route("/")
@requiere()
def panel():
    c = db()
    sup = es_sup()
    p = {"ahora": reloj(), "uid": g.user["user_id"]}
    mio = "" if sup else " AND t.asignado_a = :uid"

    kpi = c.execute(f"""
        SELECT COALESCE(SUM(t.status IN {ABIERTOS}),0) AS abiertos,
               COALESCE(SUM({VENCIDO}),0) AS vencidos,
               COALESCE(SUM(t.reopened),0) AS reabiertos, COUNT(*) AS total
        FROM tickets t {SLA_JOIN} WHERE 1=1 {mio}""", p).fetchone()
    sin_asignar = c.execute("SELECT COUNT(*) FROM tickets WHERE status='open' AND asignado_a IS NULL").fetchone()[0]
    csat = c.execute(f"""SELECT ROUND(AVG(r.csat_score),2) FROM tickets t JOIN resoluciones r USING(ticket_id)
                         WHERE t.status='resolved' {mio}""", p).fetchone()[0]
    sla_plan = c.execute(f"""
        SELECT t.sla_plan AS plan, COUNT(*) AS n, SUM(r.resolution_time_hours <= s.horas_resolucion) AS ok
        FROM tickets t JOIN resoluciones r USING(ticket_id) {SLA_JOIN}
        WHERE t.status='resolved' {mio} GROUP BY t.sla_plan
        ORDER BY CASE t.sla_plan WHEN 'platinum' THEN 1 WHEN 'gold' THEN 2 ELSE 3 END""", p).fetchall()
    q_ab = f"SELECT t.{{0}} AS k, COUNT(*) AS n FROM tickets t WHERE t.status IN {ABIERTOS} {mio} GROUP BY 1"
    por_prioridad = {r["k"]: r["n"] for r in c.execute(q_ab.format("priority"), p)}
    por_area = c.execute(q_ab.format("product_area") + " ORDER BY n DESC", p).fetchall()
    tiempos = {r["priority"]: r for r in c.execute(f"""
        SELECT t.priority, ROUND(AVG(r.resolution_time_hours),1) AS horas, COUNT(*) AS n
        FROM tickets t JOIN resoluciones r USING(ticket_id) WHERE t.status='resolved' {mio}
        GROUP BY t.priority""", p)}
    pendientes = c.execute(f"""
        SELECT t.*, {HORAS} AS horas_abierto, s.horas_resolucion AS objetivo
        FROM tickets t {SLA_JOIN} WHERE t.status IN {ABIERTOS} AND t.asignado_a = :uid
        ORDER BY (t.status IN {ABIERTOS} AND {HORAS} > s.horas_resolucion) DESC,
                 CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
                 t.created_at LIMIT 8""", p).fetchall()
    al = calcular_alertas(c, None if sup else g.user["user_id"], 4)
    datos = dict(alertas_res=contar_alertas(c, None if sup else g.user["user_id"]), alertas_top=al, kpi=kpi, sin_asignar=sin_asignar, csat=csat, sla_plan=sla_plan, por_prioridad=por_prioridad,
                 por_area=por_area, tiempos=tiempos, pendientes=pendientes)
    if sup:
        datos["por_agente"] = c.execute(f"""
            SELECT u.user_id, u.nombre, u.usuario, SUM(t.status IN {ABIERTOS}) AS abiertos, SUM(t.status='resolved') AS resueltos,
                   ROUND(AVG(CASE WHEN t.status='resolved' THEN r.resolution_time_hours END),1) AS horas
            FROM usuarios u LEFT JOIN tickets t ON t.asignado_a = u.user_id
                 LEFT JOIN resoluciones r ON r.ticket_id = t.ticket_id
            WHERE u.rol = 'agente' AND u.activo = 1 GROUP BY u.user_id""").fetchall()
        datos["riesgo"] = c.execute("""
            SELECT customer_id, SUM(reopened) AS reaperturas FROM tickets
            GROUP BY customer_id HAVING SUM(reopened) > 1 ORDER BY reaperturas DESC, customer_id LIMIT 6""").fetchall()
        meses = c.execute("""SELECT substr(created_at,1,7) AS m, COUNT(*) AS n FROM tickets
                             GROUP BY m ORDER BY m DESC LIMIT 12""").fetchall()[::-1]
        mx = max(r["n"] for r in meses) or 1
        datos["tendencia"] = [(r["m"], r["n"], round(20 + i * 52.7, 1), round(110 - r["n"] / mx * 90, 1))
                              for i, r in enumerate(meses)]
    return render_template("panel.html", **datos)


# ---------- tickets ----------
def filtros(a):
    w, p = [], {"ahora": reloj(), "uid": g.user["user_id"]}
    for col, key in (("t.status", "status"), ("t.priority", "priority"), ("t.product_area", "area"),
                     ("t.customer_id", "customer"), ("t.sla_plan", "sla")):
        if a.get(key):
            w.append(f"{col} = :{key}")
            p[key] = a[key].strip()
    asig = a.get("asignado", "")
    if asig == "me":
        w.append("t.asignado_a = :uid")
    elif asig == "none":
        w.append("t.asignado_a IS NULL")
    elif asig.isdigit():
        w.append("t.asignado_a = :asig")
        p["asig"] = int(asig)
    if a.get("q", "").strip():
        w.append("(t.ticket_id LIKE :q OR t.customer_id LIKE :q OR t.initial_message LIKE :q)")
        p["q"] = f"%{a['q'].strip()}%"
    if a.get("vencidos"):
        w.append(VENCIDO)
    if a.get("reabiertos"):
        w.append("t.reopened = 1")
    return (" WHERE " + " AND ".join(w)) if w else "", p


ORDEN = {"recientes": "t.created_at DESC", "antiguos": "t.created_at ASC",
         "prioridad": "CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, t.created_at"}
FROM_LISTA = f"FROM tickets t {SLA_JOIN} LEFT JOIN usuarios u ON u.user_id = t.asignado_a"


@app.route("/tickets")
@requiere()
def lista():
    c = db()
    where, p = filtros(request.args)
    total = c.execute(f"SELECT COUNT(*) {FROM_LISTA}{where}", p).fetchone()[0]
    pag = max(1, request.args.get("page", 1, type=int))
    orden = ORDEN.get(request.args.get("orden"), ORDEN["recientes"])
    filas = c.execute(f"""SELECT t.*, u.nombre AS asignado_nombre, s.horas_resolucion AS objetivo,
                                 {HORAS} AS horas_abierto, {VENCIDO} AS vencido
                          {FROM_LISTA}{where} ORDER BY {orden} LIMIT {PAGINA} OFFSET {(pag - 1) * PAGINA}""", p).fetchall()
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    agentes = c.execute("SELECT user_id, nombre FROM usuarios WHERE rol='agente' AND activo=1").fetchall()
    areas = [r[0] for r in c.execute("SELECT DISTINCT product_area FROM tickets ORDER BY 1")]
    return render_template("lista.html", tickets=filas, total=total, pag=pag, paginas=max(1, -(-total // PAGINA)),
                           args=args, f=request.args, agentes=agentes, areas=areas)


@app.route("/tickets/export.csv")
@requiere("supervisor")
def exportar():
    where, p = filtros(request.args)
    filas = db().execute(f"""SELECT t.ticket_id, t.created_at, t.customer_id, t.customer_segment, t.channel, t.product_area,
                                    t.issue_type, t.priority, t.status, t.sla_plan, t.reopened, u.usuario AS asignado
                             {FROM_LISTA}{where} ORDER BY t.created_at DESC LIMIT 20000""", p).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(filas[0].keys() if filas else ["sin_resultados"])
    w.writerows([tuple(r) for r in filas])
    bitacora("exportar_csv", detalle=f"{len(filas)} filas")
    db().commit()
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tickets.csv"})


def ticket_o_404(tid):
    t = db().execute(f"""SELECT t.*, u.nombre AS asignado_nombre, s.horas_resolucion AS objetivo, {HORAS} AS horas_abierto
                         {FROM_LISTA} WHERE t.ticket_id = :tid""", {"tid": tid, "ahora": reloj()}).fetchone()
    return t or abort(404)


def puede_actuar(t):
    return es_sup() or t["asignado_a"] in (None, g.user["user_id"])


@app.route("/tickets/<tid>")
@requiere()
def detalle(tid):
    c = db()
    t = ticket_o_404(tid)
    legales = {r["hasta"] for r in c.execute("SELECT hasta FROM transiciones WHERE desde = ?", (t["status"],))}
    return render_template(
        "detalle.html", t=t, legales=legales, puede=puede_actuar(t),
        res=c.execute("SELECT * FROM resoluciones WHERE ticket_id = ?", (tid,)).fetchone(),
        notas=c.execute("SELECT n.*, u.nombre FROM notas n LEFT JOIN usuarios u ON u.usuario = n.agente "
                        "WHERE ticket_id = ? ORDER BY nota_id DESC", (tid,)).fetchall(),
        agentes=c.execute("SELECT user_id, nombre, rol FROM usuarios WHERE rol IN ('agente','supervisor') AND activo=1").fetchall(),
        vencido=t["status"] in ("open", "in_progress", "on_hold") and t["horas_abierto"] > t["objetivo"])


@app.post("/tickets/<tid>/tomar")
@requiere()
def tomar(tid):
    t = ticket_o_404(tid)
    with db():
        n = db().execute("UPDATE tickets SET asignado_a = ? WHERE ticket_id = ? AND asignado_a IS NULL",
                         (g.user["user_id"], tid)).rowcount
        if n:
            bitacora("tomar_ticket", tid)
    flash("Ticket asignado a ti." if n else "El ticket ya tiene responsable.", "ok" if n else "error")
    return redirect(url_for("detalle", tid=t["ticket_id"]))


@app.post("/tickets/<tid>/asignar")
@requiere("supervisor")
def asignar(tid):
    ticket_o_404(tid)
    destino = request.form.get("user_id") or None
    if destino:
        u = db().execute("SELECT * FROM usuarios WHERE user_id = ? AND activo = 1 AND rol IN ('agente','supervisor')",
                         (destino,)).fetchone()
        if not u:
            abort(400, "Usuario destino inválido")
    with db():
        db().execute("UPDATE tickets SET asignado_a = ? WHERE ticket_id = ?", (destino, tid))
        bitacora("asignar", tid, f"→ {u['usuario'] if destino else 'sin asignar'}")
    flash("Asignación actualizada.", "ok")
    return redirect(url_for("detalle", tid=tid))


@app.post("/tickets/<tid>/nota")
@requiere()
def nota(tid):
    """Transacción: nota + cambio de estado (+ resolución) + bitácora, todo o nada."""
    t = ticket_o_404(tid)
    nuevo, texto = request.form.get("nuevo_estado", ""), limpiar_texto(request.form.get("texto"), MAX_NOTA)
    destino = url_for("detalle", tid=tid)
    if nuevo not in ESTADOS or not texto:
        flash("Datos incompletos: elige un estado válido y escribe la nota (máx. 2000 caracteres).", "error")
        return redirect(destino)
    if not puede_actuar(t):
        flash("Este ticket está asignado a otro agente. Pide a un supervisor que lo reasigne.", "error")
        return redirect(destino)
    restringido = nuevo == "closed_no_action" or (t["status"] == "resolved" and nuevo == "in_progress")
    if restringido and not es_sup():
        flash("Cerrar sin acción o reabrir un ticket resuelto requiere nivel Supervisor.", "error")
        return redirect(destino)
    try:
        horas = float(request.form["horas"]) if request.form.get("horas") else round(t["horas_abierto"], 2)
        csat = int(request.form["csat"]) if request.form.get("csat") else None
        if horas != horas or horas < 0 or horas > 100000 or (csat is not None and not 0 <= csat <= 5):
            raise ValueError
    except ValueError:
        flash("Horas (≥ 0) o CSAT (0 a 5) con formato inválido. No se guardó nada.", "error")
        return redirect(destino)
    try:
        with db() as c:  # BEGIN/COMMIT; ROLLBACK si algo falla
            c.execute("UPDATE tickets SET status = ? WHERE ticket_id = ?", (nuevo, tid))
            if t["asignado_a"] is None and not es_sup():
                c.execute("UPDATE tickets SET asignado_a = ? WHERE ticket_id = ?", (g.user["user_id"], tid))
            c.execute("INSERT INTO notas (ticket_id, agente, texto, estado_resultante) VALUES (?,?,?,?)",
                      (tid, g.user["usuario"], texto, nuevo))
            if nuevo in ("resolved", "closed_no_action"):
                c.execute("INSERT INTO resoluciones (ticket_id, resolution_summary, resolution_time_hours, csat_score) "
                          "VALUES (?,?,?,?)", (tid, texto, max(horas, 0), csat))
            bitacora("cambio_estado", tid, f"{t['status']} → {nuevo}")
        flash(f"Nota registrada · estado: {ESTADOS[nuevo]}.", "ok")
    except sqlite3.DatabaseError as e:
        flash(f"RECHAZADO por la base de datos: {e}. No se guardó nada (rollback).", "error")
    return redirect(destino)


@app.route("/tickets/nuevo", methods=["GET", "POST"])
@requiere()
def nuevo():
    c = db()
    opc = {k: [r[0] for r in c.execute(f"SELECT DISTINCT {k} FROM tickets ORDER BY 1")]
           for k in ("channel", "product_area", "issue_type")}
    if request.method == "POST":
        f = request.form.to_dict()
        f["customer_id"] = f.get("customer_id", "").strip().upper()
        f["initial_message"] = limpiar_texto(f.get("initial_message"), MAX_MENSAJE)
        errores = []
        if not RE_CLIENTE.fullmatch(f["customer_id"]):
            errores.append("El ID del cliente debe tener el formato CUST_00000.")
        if not f["initial_message"]:
            errores.append("El mensaje del cliente es obligatorio (máx. 2000 caracteres).")
        for campo, validos, nombre in (("customer_segment", imp.CATALOGOS["customer_segment"], "segmento"),
                                       ("sla_plan", imp.CATALOGOS["sla_plan"], "plan SLA"),
                                       ("channel", set(opc["channel"]), "canal"),
                                       ("product_area", set(opc["product_area"]), "área"),
                                       ("issue_type", set(opc["issue_type"]), "tipo de problema")):
            if f.get(campo) not in validos:
                errores.append(f"Valor no permitido en {nombre}.")
        if f.get("priority") not in ("auto", *PRIORIDADES):
            errores.append("Prioridad no permitida.")
        if f.get("region") and f["region"] not in REGIONES:
            errores.append("Región no permitida.")
        if not errores and c.execute("""SELECT 1 FROM tickets WHERE customer_id = ? AND issue_type = ? AND initial_message = ?
                                         AND status IN ('open','in_progress','on_hold')""",
                                      (f["customer_id"], f["issue_type"], f["initial_message"])).fetchone():
            errores.append("Ya existe un ticket abierto de este cliente con el mismo problema y mensaje. No se creó un duplicado.")
        if errores:
            for e in errores:
                flash(e, "error")
            return render_template("nuevo.html", opc=opc, v=f), 400
        if f["priority"] == "auto":
            reab = c.execute("SELECT COALESCE(SUM(reopened),0) FROM tickets WHERE customer_id = ?", (f["customer_id"],)).fetchone()[0]
            f["priority"], motivos = reglas.sugerir_prioridad(f["issue_type"], f["initial_message"], reab)
            aviso = f" Prioridad automática: {PRIORIDADES[f['priority']]} ({'; '.join(motivos)})."
        else:
            aviso = ""
        try:
            with c:
                n = int(c.execute("SELECT MAX(ticket_id) FROM tickets").fetchone()[0].split("_")[1]) + 1
                tid = f"TCKT_{n:06d}"
                c.execute("INSERT OR IGNORE INTO clientes VALUES (?)", (f["customer_id"].strip(),))
                c.execute("""INSERT INTO tickets (ticket_id,customer_id,created_at,customer_segment,channel,product_area,
                                issue_type,priority,status,sla_plan,initial_message,region,asignado_a)
                             VALUES (?,?,strftime('%Y-%m-%dT%H:%M:%S', ?, '+1 minute'),?,?,?,?,?,'open',?,?,?,?)""",
                          (tid, f["customer_id"].strip(), reloj(), f["customer_segment"], f["channel"], f["product_area"],
                           f["issue_type"], f["priority"], f["sla_plan"], f["initial_message"].strip(),
                           f.get("region") or None, g.user["user_id"] if f.get("tomar") else None))
                bitacora("crear_ticket", tid, f"prioridad {f['priority']}")
            flash(f"Ticket {tid} creado.{aviso}", "ok")
            return redirect(url_for("detalle", tid=tid))
        except sqlite3.DatabaseError as e:
            flash(f"RECHAZADO por la base de datos: {e}", "error")
    return render_template("nuevo.html", opc=opc, v={})


# ---------- inteligencia (reglas deterministas) ----------
def contar_alertas(c, uid=None):
    """Conteo exacto en SQL: {'vencido': n, 'en_riesgo': n}."""
    mio = " AND t.asignado_a = :uid" if uid else ""
    r = c.execute(f"""SELECT COALESCE(SUM({HORAS} > s.horas_resolucion),0) AS v,
                              COALESCE(SUM({HORAS} <= s.horas_resolucion AND {HORAS} >= s.horas_resolucion * {reglas.UMBRAL_SLA_RIESGO}),0) AS r
                       FROM tickets t {SLA_JOIN} WHERE t.status IN {ABIERTOS} {mio}""", {"ahora": reloj(), "uid": uid}).fetchone()
    return {"vencido": r["v"], "en_riesgo": r["r"]}


def calcular_alertas(c, uid=None, limite=200):
    """Tickets abiertos que ya vencieron o consumieron >= 80 % de su SLA, con la acción que recomienda la regla."""
    mio = " AND t.asignado_a = :uid" if uid else ""
    filas = c.execute(f"""
        SELECT t.ticket_id, t.customer_id, t.priority, t.status, t.asignado_a, u.nombre AS responsable,
               {HORAS} AS horas_abierto, s.horas_resolucion AS objetivo
        FROM tickets t {SLA_JOIN} LEFT JOIN usuarios u ON u.user_id = t.asignado_a
        WHERE t.status IN {ABIERTOS} AND {HORAS} >= s.horas_resolucion * {reglas.UMBRAL_SLA_RIESGO} {mio}
        ORDER BY ({HORAS} > s.horas_resolucion),  -- primero los que aún se pueden salvar (en riesgo)
                 CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
                 (t.asignado_a IS NOT NULL), ({HORAS}) / s.horas_resolucion DESC LIMIT {limite}""",
        {"ahora": reloj(), "uid": uid}).fetchall()
    out = []
    for r in filas:
        d = dict(r)
        d["estado_sla"] = reglas.clasificar_sla(d["horas_abierto"], d["objetivo"])
        d["consumo"] = reglas.consumo_sla(d["horas_abierto"], d["objetivo"])
        d["accion"], d["motivo"] = reglas.recomendar(d)
        out.append(d)
    return out


@app.route("/alertas")
@requiere()
def alertas():
    c = db()
    uid = None if es_sup() else g.user["user_id"]
    lista_ = calcular_alertas(c, uid, 150)
    # conteos reales (la lista está limitada a 150 filas por rendimiento)
    cuentas = c.execute(f"""SELECT t.customer_id, SUM(t.reopened) AS reaperturas, SUM(t.status IN {ABIERTOS}) AS abiertos
                            FROM tickets t GROUP BY t.customer_id
                            HAVING SUM(t.reopened) >= {reglas.REAPERTURAS_CRITICAS} AND SUM(t.status IN {ABIERTOS}) > 0
                            ORDER BY reaperturas DESC, abiertos DESC LIMIT 15""").fetchall() if es_sup() else []
    resumen = contar_alertas(c, uid)
    return render_template("alertas.html", alertas=lista_, cuentas=cuentas, resumen=resumen,
                           umbral=int(reglas.UMBRAL_SLA_RIESGO * 100), reab=reglas.REAPERTURAS_CRITICAS)


@app.route("/importar", methods=["GET", "POST"])
@requiere("supervisor")
def importar_csv():
    resultado = None
    if request.method == "POST":
        f = request.files.get("archivo")
        if not f or not f.filename.lower().endswith(".csv"):
            flash("Selecciona un archivo .csv.", "error")
        else:
            try:
                resultado = imp.importar(db(), f.read(), os.path.basename(f.filename))
                bitacora("importar_csv", detalle=f"{os.path.basename(f.filename)[:60]}: {resultado['nuevos']} nuevos, "
                         f"{resultado['existentes']} ya estaban, {resultado['rechazados']} rechazados")
                db().commit()
            except ValueError as e:
                flash(str(e), "error")
            except sqlite3.DatabaseError as e:
                flash(f"RECHAZADO por la base de datos: {e}. No se guardó nada.", "error")
    rech = db().execute("SELECT * FROM rechazos ORDER BY id DESC LIMIT 15").fetchall() \
        if db().execute("SELECT 1 FROM sqlite_master WHERE name='rechazos'").fetchone() else []
    return render_template("importar.html", r=resultado, rechazos=rech)


# ---------- supervisión ----------
@app.route("/riesgo")
@requiere("supervisor")
def riesgo():
    filas = db().execute(f"""
        SELECT customer_id, SUM(reopened) AS reaperturas, COUNT(*) AS total, SUM(status IN {ABIERTOS}) AS abiertos
        FROM tickets GROUP BY customer_id HAVING SUM(reopened) > 1
        ORDER BY reaperturas DESC, total DESC, customer_id LIMIT 60""").fetchall()
    return render_template("riesgo.html", filas=filas)


@app.route("/bitacora")
@requiere("supervisor")
def ver_bitacora():
    return render_template("bitacora.html", filas=db().execute("SELECT * FROM bitacora ORDER BY id DESC LIMIT 150").fetchall())


# ---------- administración ----------
@app.route("/usuarios", methods=["GET", "POST"])
@requiere("admin")
def usuarios():
    c = db()
    if request.method == "POST":
        f = request.form
        try:
            with c:
                if f["accion"] == "crear":
                    if len(f["password"]) < 6:
                        raise ValueError("La contraseña debe tener al menos 6 caracteres.")
                    c.execute("INSERT INTO usuarios (usuario,nombre,rol,password_hash) VALUES (?,?,?,?)",
                              (f["usuario"].strip().lower(), f["nombre"].strip(), f["rol"], generate_password_hash(f["password"])))
                    bitacora("crear_usuario", detalle=f"{f['usuario']} ({f['rol']})")
                else:
                    uid = int(f["user_id"])
                    if uid == g.user["user_id"]:
                        raise ValueError("No puedes modificar tu propia cuenta desde aquí.")
                    if f["accion"] == "rol":
                        c.execute("UPDATE usuarios SET rol = ? WHERE user_id = ?", (f["rol"], uid))
                    elif f["accion"] == "activo":
                        c.execute("UPDATE usuarios SET activo = 1 - activo WHERE user_id = ?", (uid,))
                    bitacora("editar_usuario", detalle=f"id {uid}: {f['accion']}")
            flash("Cambios guardados.", "ok")
        except (sqlite3.DatabaseError, ValueError) as e:
            flash(f"No se pudo guardar: {e}", "error")
        return redirect(url_for("usuarios"))
    filas = c.execute("""SELECT u.*, (SELECT COUNT(*) FROM tickets t WHERE t.asignado_a = u.user_id
                                      AND t.status IN ('open','in_progress','on_hold')) AS abiertos
                         FROM usuarios u ORDER BY user_id""").fetchall()
    return render_template("usuarios.html", filas=filas)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
