"""TicketFlow · Momento 3 · evidencia automática de los pasos 3 a 6.
Trabaja sobre una COPIA de la base: no modifica ticketflow.db.  Uso:  python pruebas_m3.py"""
import math, os, re, shutil, sqlite3, sys, tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTO = os.path.join(RAIZ, "04_prototipo")
sys.path.insert(0, PROTO)
import app as A
import reglas
from werkzeug.security import check_password_hash

tmp = tempfile.mkdtemp()
DB = os.path.join(tmp, "ticketflow.db")
shutil.copy(os.path.join(PROTO, "ticketflow.db"), DB)
A.AQUI = tmp


def H(t):
    print(f"\n======== {t} ========")


def sql(q, *p):
    c = sqlite3.connect(DB)
    try:
        return c.execute(q, p).fetchall()
    finally:
        c.close()


def token(c, ruta="/login"):
    return re.search(r'name="_csrf" value="(\w+)"', c.get(ruta).get_data(as_text=True)).group(1)


def entrar(u):
    c = A.app.test_client()
    c.post("/login", data={"_csrf": token(c), "usuario": u, "password": u + "123"})
    return c


def msgs(html):
    return [re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r'<div class="msg [a-z]+">(.*?)</div>', html, re.S)]


def nuevo(c, **kw):
    d = dict(_csrf=token(c, "/tickets/nuevo"), customer_id="CUST_00861", customer_segment="enterprise", sla_plan="gold",
             region="LATAM", product_area="billing", issue_type="billing_problem", channel="email", priority="auto",
             initial_message="Prueba normal", tomar="1")
    d.update(kw)
    r = c.post("/tickets/nuevo", data=d, follow_redirects=True)
    return r, msgs(r.get_data(as_text=True))


sup = entrar("supervisor")
n0 = sql("SELECT COUNT(*) FROM tickets")[0][0]

H("PASO 3 · Datos: limpieza y carga sin duplicados")
lote = os.path.join(RAIZ, "05_momento2", "lote_prueba_m2.csv")
for i in (1, 2):
    with open(lote, "rb") as f:
        r = sup.post("/importar", data={"_csrf": token(sup, "/importar"), "archivo": (f, "lote_prueba_m2.csv")},
                     content_type="multipart/form-data")
    num = re.findall(r"<em>([^<]+)</em></div><b>([\d,]+)</b>", r.get_data(as_text=True))
    print(f"Carga #{i}:", dict(num))
n1 = sql("SELECT COUNT(*) FROM tickets")[0][0]
print(f"Tickets antes: {n0:,} · después de cargar DOS veces el mismo archivo: {n1:,} (+{n1 - n0})")
print("Rechazos guardados para auditoría:", sql("SELECT COUNT(*) FROM rechazos")[0][0])
for a, f, t, mo in sql("SELECT archivo, fila, ticket_id, motivo FROM rechazos ORDER BY id LIMIT 6"):
    print(f"   fila {f:>2} {t or '(sin id)':<12} -> {mo}")

H("PASO 3b · Duplicado al crear a mano")
_, m1 = nuevo(sup, initial_message="Prueba de duplicado")
_, m2 = nuevo(sup, initial_message="Prueba de duplicado")
print("1er intento:", m1)
print("2do intento:", m2)

H("PASO 4 · Seguridad: código metido por formularios")
xss = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
r, m = nuevo(sup, initial_message=xss)
tid = re.search(r"TCKT_\d+", " ".join(m)).group(0)
print("Guardado en la base (texto inerte):", sql("SELECT initial_message FROM tickets WHERE ticket_id=?", tid)[0][0])
pag = sup.get(f"/tickets/{tid}")
body = pag.get_data(as_text=True)
print('¿El HTML contiene un <script> real sin escapar?:', "<script>alert" in body)
print("Así lo recibe el navegador:", re.search(r"&lt;script&gt;.*?&gt;", body).group(0)[:70])
print("Cabecera CSP:", pag.headers["Content-Security-Policy"][:80], "...")
c2 = A.app.test_client()
r = c2.post("/login", data={"_csrf": token(c2), "usuario": "admin' --", "password": "' OR 1=1 --"})
print("Inyección SQL en el login:", "RECHAZADA ->" if msgs(r.get_data(as_text=True)) else "¡ENTRÓ!", msgs(r.get_data(as_text=True)))
r = sup.get("/tickets", query_string={"q": "x' OR '1'='1"})
print("Inyección SQL en el buscador: HTTP", r.status_code, "· se trata como texto:", "Ningún ticket coincide" in r.get_data(as_text=True))
print("POST sin token CSRF: HTTP", sup.post("/tickets/nuevo", data={"customer_id": "CUST_00861"}).status_code)

H("PASO 4b · Seguridad: contraseñas")
for u, ph in sql("SELECT usuario, password_hash FROM usuarios ORDER BY user_id LIMIT 3"):
    print(f"{u:<11} clave real: {u + '123':<14} guardado: {ph[:48]}...")
h = sql("SELECT password_hash FROM usuarios WHERE usuario='luis'")[0][0]
print("Clave correcta verifica:", check_password_hash(h, "luis123"), "· clave incorrecta:", check_password_hash(h, "otra"))
print("¿Aparece 'luis123' en la base?:", "luis123" in h)
A.INTENTOS.clear()
c3 = A.app.test_client()
for i in range(7):
    r = c3.post("/login", data={"_csrf": token(c3), "usuario": "luis", "password": f"mala{i}"})
print("Tras 7 intentos fallidos seguidos: HTTP", r.status_code, msgs(r.get_data(as_text=True)))

H("PASO 5 · Inteligencia: ¿regla o IA?")
filas = sql("SELECT priority, issue_type, initial_message FROM tickets")


def cramer(i):
    obs, fa, fb = {}, {}, {}
    for f in filas:
        obs[(f[i], f[0])] = obs.get((f[i], f[0]), 0) + 1
        fa[f[i]] = fa.get(f[i], 0) + 1
        fb[f[0]] = fb.get(f[0], 0) + 1
    n = len(filas)
    chi = sum((obs.get((a, b), 0) - fa[a] * fb[b] / n) ** 2 / (fa[a] * fb[b] / n) for a in fa for b in fb)
    return math.sqrt(chi / n / (min(len(fa), len(fb)) - 1))


print(f"Asociación (V de Cramér: 0 = ninguna, 1 = total) prioridad vs tipo de problema: {cramer(1):.3f}")
print(f"Asociación prioridad vs texto del mensaje ({len({f[2] for f in filas})} mensajes distintos): {cramer(2):.3f}")
print("-> Ni el tipo ni el mensaje predicen la prioridad en el dataset: un modelo de IA no tendría qué aprender.")
print("Reglas de prioridad automática (ejemplos):")
for tipo, msg in [("security_concern", "We need details about your data encryption"),
                  ("account_access", "My account was locked after multiple failed login attempts."),
                  ("performance", "Overall performance has degraded"), ("how_to", "I have a general question"),
                  ("bug", "There was a data loss after the update")]:
    print(f"  {tipo:<17} {msg[:50]:<50} -> {reglas.sugerir_prioridad(tipo, msg)}")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
with A.app.test_request_context():
    A.g.db = con
    print("Conteo de alertas SLA (todo el equipo):", A.contar_alertas(con))
    for a in A.calcular_alertas(con, None, 3):
        print(f"  {a['ticket_id']} {a['priority']:<6} consumo {a['consumo']:.0%} -> {a['accion']} ({a['motivo']})")
con.close()

H("PASO 6 · Dato malo: qué responde la app")
casos = {"ID de cliente con formato incorrecto": dict(customer_id="CLIENTE-1"), "Mensaje vacío": dict(initial_message="   "),
         "Prioridad inventada": dict(priority="urgentt"), "Región fuera de catálogo": dict(region="XX"),
         "Plan SLA inválido": dict(sla_plan="diamante")}
antes = sql("SELECT COUNT(*) FROM tickets")[0][0]
for k, d in casos.items():
    r, m = nuevo(sup, **d)
    print(f"{k:<40} -> HTTP {r.status_code} · {m}")
print(f"Tickets antes/después de los 5 intentos malos: {antes:,} / {sql('SELECT COUNT(*) FROM tickets')[0][0]:,}  (no se guardó nada)")
tt = sql("SELECT ticket_id FROM tickets WHERE status='in_progress' LIMIT 1")[0][0]
tok = token(sup, "/tickets/nuevo")
r = sup.post(f"/tickets/{tt}/nota", data={"_csrf": tok, "nuevo_estado": "resolved", "texto": "Listo", "csat": "9"}, follow_redirects=True)
print("CSAT = 9 al resolver ->", msgs(r.get_data(as_text=True)))
r = sup.post(f"/tickets/{tt}/nota", data={"_csrf": tok, "nuevo_estado": "open", "texto": "Salto ilegal"}, follow_redirects=True)
print("in_progress -> open (salto ilegal) ->", msgs(r.get_data(as_text=True)))
shutil.rmtree(tmp, ignore_errors=True)
