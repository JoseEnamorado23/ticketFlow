"""Genera las capturas de pantalla (PNG) de cada paso en 06_momento3/capturas/. Usa una copia de la base."""
import os, re, shutil, subprocess, sys, tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTO = os.path.join(RAIZ, "04_prototipo")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capturas")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, PROTO)
import app as A

tmp = tempfile.mkdtemp()
shutil.copy(os.path.join(PROTO, "ticketflow.db"), os.path.join(tmp, "ticketflow.db"))
A.AQUI = tmp
CSS = open(os.path.join(PROTO, "static", "style.css"), encoding="utf-8").read()
BROWSER = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def token(c, ruta):
    return re.search(r'name="_csrf" value="(\w+)"', c.get(ruta).get_data(as_text=True)).group(1)


def entrar(u):
    c = A.app.test_client()
    c.post("/login", data={"_csrf": token(c, "/login"), "usuario": u, "password": u + "123"})
    return c


def foto(nombre, html, ancho=1360, alto=900):
    html = re.sub(r'<link rel="stylesheet"[^>]*>', f"<style>:root{{color-scheme:light}}{CSS}</style>", html)
    html = re.sub(r"<script[^>]*></script>", "", html)
    f = os.path.join(tmp, nombre + ".html")
    open(f, "w", encoding="utf-8").write(html)
    png = os.path.join(OUT, nombre + ".png")
    subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={ancho},{alto}",
                    f"--screenshot={png}", "file:///" + f.replace("\\", "/")], capture_output=True, timeout=60)
    print("OK" if os.path.exists(png) else "FALLÓ", nombre)


def nuevo(c, **kw):
    d = dict(_csrf=token(c, "/tickets/nuevo"), customer_id="CUST_00861", customer_segment="enterprise", sla_plan="gold",
             region="LATAM", product_area="billing", issue_type="billing_problem", channel="email", priority="auto",
             initial_message="Prueba normal", tomar="1")
    d.update(kw)
    return c.post("/tickets/nuevo", data=d, follow_redirects=True)


anon = A.app.test_client()
foto("p1_login", anon.get("/login").get_data(as_text=True), alto=800)
sup = entrar("supervisor")
foto("p2_panel", sup.get("/").get_data(as_text=True), alto=1500)
foto("p2_lista", sup.get("/tickets?vencidos=1").get_data(as_text=True), alto=1000)

# Paso 3: importar dos veces el mismo archivo
lote = os.path.join(RAIZ, "05_momento2", "lote_prueba_m2.csv")
for i in (1, 2):
    with open(lote, "rb") as f:
        r = sup.post("/importar", data={"_csrf": token(sup, "/importar"), "archivo": (f, "lote_prueba_m2.csv")},
                     content_type="multipart/form-data")
    foto(f"p3_importar_carga{i}", r.get_data(as_text=True), alto=1400)

# Paso 4: XSS escapado
xss = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
r = nuevo(sup, initial_message=xss)
tid = re.search(r"TCKT_\d+", r.get_data(as_text=True)).group(0)
foto("p4_xss_detalle", sup.get(f"/tickets/{tid}").get_data(as_text=True), alto=1100)
admin = entrar("admin")
foto("p4_usuarios", admin.get("/usuarios").get_data(as_text=True), alto=900)

# Paso 5: alertas + prioridad automática
foto("p5_alertas", sup.get("/alertas").get_data(as_text=True), alto=1300)
foto("p5_prioridad_auto", nuevo(sup, issue_type="account_access", initial_message="My account was locked after multiple failed login attempts.", customer_id="CUST_00777").get_data(as_text=True), alto=1100)

# Paso 6: dato malo
foto("p6_dato_malo", nuevo(sup, customer_id="CLIENTE-1", initial_message="   ", region="XX").get_data(as_text=True), alto=1300)
shutil.rmtree(tmp, ignore_errors=True)
