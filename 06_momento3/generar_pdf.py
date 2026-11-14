"""Arma E3_Informe_TicketFlow.pdf (7 pasos + correcciones del M2) con las capturas y la evidencia."""
import os, re
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle)

AQUI = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(AQUI, "capturas")
REPO = "[PEGAR AQUÍ EL ENLACE AL REPOSITORIO]"
pdfmetrics.registerFont(TTFont("A", r"C:\Windows\Fonts\arial.ttf"))
pdfmetrics.registerFont(TTFont("A-B", r"C:\Windows\Fonts\arialbd.ttf"))
pdfmetrics.registerFont(TTFont("M", r"C:\Windows\Fonts\consola.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("A", normal="A", bold="A-B", italic="A", boldItalic="A-B")
IND = colors.HexColor("#4338ca")
S = dict(
    t=ParagraphStyle("t", fontName="A-B", fontSize=26, leading=30, textColor=IND, spaceAfter=6),
    h1=ParagraphStyle("h1", fontName="A-B", fontSize=17, leading=21, textColor=IND, spaceBefore=4, spaceAfter=4),
    h2=ParagraphStyle("h2", fontName="A-B", fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=3),
    p=ParagraphStyle("p", fontName="A", fontSize=10, leading=14.5, alignment=TA_LEFT, spaceAfter=5),
    li=ParagraphStyle("li", fontName="A", fontSize=10, leading=14, leftIndent=14, bulletIndent=2, spaceAfter=2),
    cap=ParagraphStyle("cap", fontName="A", fontSize=8.5, leading=11, textColor=colors.HexColor("#667085"), spaceAfter=8),
    code=ParagraphStyle("code", fontName="M", fontSize=7, leading=8.6, backColor=colors.HexColor("#f2f4f7"), borderPadding=6),
)
P = lambda t, s="p": Paragraph(t, S[s])
B = lambda items: [Paragraph(i, S["li"], bulletText="•") for i in items]


def img(nombre, texto, h=11.5):
    ruta = os.path.join(CAP, nombre + ".png")
    w, hh = Image(ruta).imageWidth, Image(ruta).imageHeight
    esc = min(17 * cm / w, h * cm / hh)
    im = Image(ruta, w * esc, hh * esc)
    t = Table([[im]], colWidths=[w * esc])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d0d5dd")), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return KeepTogether([t, Spacer(1, 3), P(texto, "cap")])


ev = open(os.path.join(AQUI, "evidencia_m3.txt"), encoding="utf-8").read()
secciones = re.split(r"\n?======== (.*?) ========\n", ev)
EV = {secciones[i]: secciones[i + 1].strip("\n") for i in range(1, len(secciones) - 1, 2)}


def evidencia(clave, titulo="Salida real de pruebas_m3.py"):
    txt = EV[next(k for k in EV if k.startswith(clave))]
    txt = "\n".join(l if len(l) <= 135 else l[:132] + "..." for l in txt.splitlines())
    return [P(titulo, "h2"), Preformatted(txt, S["code"]), Spacer(1, 6)]


f = []
f += [P("TicketFlow", "t"), P("Momento evaluativo 3 · Seguridad e inteligencia de aplicaciones", "h1"),
      P("Optativa II · Grupo OPL-07 · Universidad Cooperativa de Colombia · Ingeniería de Sistemas, 9.º semestre (2026-II)"),
      P("<b>Autores:</b> Luis Ramos, José Enamorado, Tomás Tordecilla · <b>Profesor:</b> Joseph David Gari Bustos"),
      P(f"<b>Repositorio de la aplicación:</b> {REPO}"), Spacer(1, 8),
      P("Qué corregimos respecto al Momento 2", "h1"),
      P("Al repasar la aplicación del Momento 2 contra lo que pide este momento encontramos cinco fallas y las corregimos. "
        "Cada corrección tiene su evidencia en los pasos de este documento:")]
f += B(["<b>Validación solo en el navegador y en la base.</b> El formulario «Nuevo ticket» confiaba en el HTML (pattern) y en los errores de la base. "
        "Ahora el servidor valida formato, catálogos, longitud y vacíos, y responde con un mensaje claro sin guardar nada (pasos 4 y 6).",
        "<b>Cualquier ID creaba un cliente.</b> La app hacía <i>INSERT OR IGNORE</i> en clientes con lo que llegara. Ahora el ID debe cumplir CUST_00000 (paso 6).",
        "<b>Se podían crear tickets duplicados a mano.</b> Ahora el mismo cliente con el mismo problema y mensaje, ya abierto, se rechaza (paso 3).",
        "<b>La carga de datos vivía fuera de la app</b> (un script aparte). Ahora hay una pantalla «Importar datos» que limpia, valida y no duplica; los rechazos quedan "
        "guardados con su motivo (paso 3).",
        "<b>El reloj de demostración se movía.</b> Se calculaba como la fecha máxima de los tickets: al importar un lote con fechas de 2026, todo pasó a «vencido» y las alertas "
        "dejaron de servir. Lo detectamos al probar el paso 3 y ahora el reloj tiene un tope fijo al fin del dataset.",
        "<b>Seguridad añadida:</b> cabeceras CSP/anti-clickjacking, cookie de sesión HttpOnly y SameSite, límite de intentos de login y scripts propios en lugar de scripts en línea."])
f += [P("Diferencia conocida que dejamos documentada: el pipeline del Momento 2 acepta el estado <i>closed</i> y la app (base del E1) no; por eso la importación "
        "de la app rechaza esas filas con el motivo «status fuera de catálogo». Unificar los catálogos queda como mejora.", "cap"), PageBreak()]

# Paso 1
f += [P("Paso 1 · La idea", "h1"),
      P("<b>Problema.</b> Un equipo de soporte técnico recibe cientos de tickets con distintas prioridades y planes de servicio (SLA). Sin una herramienta que los consolide, "
        "nadie sabe a tiempo qué ticket está por vencer su SLA, qué área concentra carga ni qué clientes reportan lo mismo una y otra vez (tickets reabiertos)."),
      P("<b>Quién la usa.</b> Agentes de soporte (trabajan sus tickets) y el supervisor o coordinador (asigna, vigila SLA y cuentas en riesgo). Hay tres niveles de acceso: agente, supervisor y administrador."),
      P("<b>Qué necesita para decidir.</b> Saber <i>qué atender primero y por qué</i>: cuántos tickets están vencidos o por vencer, quién los tiene, y qué clientes requieren contacto proactivo. "
        "Dataset: 100.000 tickets sintéticos de una empresa SaaS (Kaggle, Synthetic IT Support Tickets)."),
      img("p1_login", "Pantalla de acceso con tres roles y cuentas de demostración.", 9), PageBreak()]

# Paso 2
f += [P("Paso 2 · La app funcionando", "h1"),
      P("<b>Recorrido de punta a punta:</b> (1) el supervisor inicia sesión → (2) importa o crea un ticket → (3) el sistema valida y guarda → (4) la regla lo evalúa contra su SLA → "
        "(5) el ticket aparece en el panel y en «Alertas inteligentes» con la acción recomendada → (6) un agente lo toma, registra una nota y cambia el estado en una sola transacción → "
        "(7) queda en el historial y en la bitácora."),
      img("p2_panel", "Panel de control del supervisor: KPIs, cumplimiento de SLA, carga por agente, alertas y cuentas en riesgo.", 12.5),
      img("p2_lista", "Listado filtrado por «solo SLA vencido». Cada fila muestra prioridad, estado, responsable y tiempo restante.", 9),
      P("Correcciones del Momento 2 mostradas en este paso: reloj de demostración con tope fijo (el panel conserva 12 tickets «en riesgo» aunque se importen lotes con fechas futuras)."),
      PageBreak()]

# Paso 3
f += [P("Paso 3 · Los datos", "h1"),
      P("<b>Cómo se preparan antes de guardar</b> (importar.py, mismo flujo que el pipeline del Momento 2):"), *B([
          "<b>Limpiar:</b> quita espacios, pasa catálogos a minúscula y IDs a mayúscula, convierte vacíos en nulos y descarta duplicados exactos dentro del archivo.",
          "<b>Validar:</b> formato de ID y fecha, catálogos, región, CSAT entre 0 y 5, tiempo no negativo y reglas entre campos (un ticket resuelto debe traer resolución; uno abierto no).",
          "<b>Guardar sin duplicar:</b> el <i>ticket_id</i> es clave primaria; si ya existe se cuenta como «ya estaba» (y se usa ON CONFLICT DO NOTHING). Todo va en una sola transacción."]),
      img("p3_importar_carga1", "Primera carga del lote de prueba: 4 tickets nuevos guardados, 10 rechazados con su motivo, 1 duplicado exacto eliminado.", 10),
      img("p3_importar_carga2", "Segunda carga del MISMO archivo: 0 nuevos, 4 «ya estaban». No se creó ningún registro doble.", 10),
      *evidencia("PASO 3 ·"), *evidencia("PASO 3b"), PageBreak()]

# Paso 4
f += [P("Paso 4 · Seguridad", "h1"),
      P("<b>Código metido por formularios.</b> Tres capas: (1) todas las consultas SQL usan parámetros (<i>?</i>), nunca se pega texto del usuario dentro del SQL; "
        "(2) las plantillas escapan todo lo que muestran (Jinja con autoescape), de modo que un &lt;script&gt; se ve como texto y no se ejecuta; "
        "(3) la cabecera Content-Security-Policy impide ejecutar scripts que no sean del propio sitio. Además hay token CSRF en cada formulario y límites de longitud."),
      img("p4_xss_detalle", "Ticket cuyo mensaje es &lt;script&gt;alert(...)&lt;/script&gt;&lt;img onerror=...&gt;: se guarda como texto y se muestra como texto (recuadro «Mensaje del cliente»).", 10),
      *evidencia("PASO 4 ·"),
      P("<b>Contraseñas.</b> Nunca se guardan como están escritas: se guarda un hash con sal (scrypt, vía Werkzeug). Cada usuario tiene su propia sal, así que dos claves iguales producen hashes distintos; "
        "no hay forma de leer la clave desde la base, solo de comprobarla. Además el login se bloquea tras 5 intentos fallidos en 5 minutos."),
      img("p4_usuarios", "Gestión de usuarios (solo administrador): la base nunca muestra claves, solo roles y estado.", 8),
      *evidencia("PASO 4b"), PageBreak()]

# Paso 5
f += [P("Paso 5 · Inteligencia: reglas deterministas, no IA", "h1"),
      P("<b>Tarea repetitiva elegida:</b> decidir cada día qué tickets atender primero (vigilar el SLA) y fijar la prioridad al crear un ticket. Hoy un supervisor tendría que revisar a mano miles de tickets."),
      P("<b>Qué hace la app:</b> (a) <i>Alertas inteligentes</i>: clasifica cada ticket abierto como a tiempo, en riesgo (80 % o más del tiempo del SLA consumido) o vencido, y recomienda una acción "
        "(«Tomar el ticket ahora», «Priorizar hoy», «Asignar de inmediato», «Escalar al supervisor») con la razón escrita; además lista las cuentas con 3 o más reaperturas. "
        "(b) <i>Prioridad automática</i> al crear: tabla por tipo de problema, subida por palabras clave del mensaje (bloqueo, cobro doble, brecha) y por reaperturas del cliente, siempre explicando por qué."),
      P("Justificación de la elección", "h2"), *B([
          "<b>Los datos:</b> el SLA es un contrato con un número exacto (horas por plan y prioridad): una regla lo calcula sin error. En cambio, medimos la relación entre la prioridad y el tipo de problema "
          "(V de Cramér = 0,009) y entre la prioridad y el texto del mensaje (0,031): casi nula. Los 100.000 mensajes son solo unas 100 frases repetidas. Un modelo de IA no tendría señal que aprender: sería azar con apariencia de inteligencia.",
          "<b>Lo que necesita quien usa la solución:</b> el agente debe poder explicar al cliente y al supervisor por qué un ticket se priorizó. Una regla muestra su razón; un modelo opaco, no.",
          "<b>Límites:</b> las reglas no entienden mensajes nuevos o ambiguos; por eso solo <i>sugieren</i> y la persona puede cambiar la prioridad. Los umbrales (80 %, 3 reaperturas) son revisables por el equipo.",
          "<b>Alternativa descartada — IA/clasificador de texto:</b> sin relación entre texto y prioridad no mejora la decisión, añade costo, riesgo de enviar datos de clientes a un tercero y respuestas distintas para la misma entrada. "
          "Sería razonable reconsiderarla si hubiera datos reales etiquetados por agentes."]),
      img("p5_alertas", "Alertas inteligentes: primero los tickets «en riesgo» (aún se pueden salvar), luego los vencidos por prioridad, cada uno con acción y motivo.", 11),
      img("p5_prioridad_auto", "Un ticket creado con prioridad «Automática»: la app la fija y explica la razón en el aviso.", 9),
      *evidencia("PASO 5"), PageBreak()]

# Paso 6
f += [P("Paso 6 · Un dato malo", "h1"),
      P("Probamos datos incompletos, fuera de rango y con formato incorrecto, tanto en el formulario como en la importación. La app responde con un mensaje claro, con código HTTP 400, "
        "y no guarda nada: el conteo de tickets es igual antes y después. Si la regla llega a la base (por ejemplo un salto ilegal de estado), un <i>trigger</i> la rechaza y la transacción se revierte."),
      img("p6_dato_malo", "Formulario con ID «CLIENTE-1», mensaje vacío y región «XX»: tres errores explicados y ningún ticket creado.", 11),
      *evidencia("PASO 6"), PageBreak()]

# Paso 7
f += [P("Paso 7 · La decisión", "h1"),
      P("<b>Decisión:</b> automatizar con <b>reglas deterministas explicables</b> (SLA, reaperturas, prioridad por tipo y palabras clave) y poner la defensa en <b>varias capas independientes</b> "
        "(validación en el servidor, consultas parametrizadas, escape al mostrar, CSP, hash de contraseñas y restricciones en la base), en vez de usar un modelo de IA y de confiar en una sola validación."),
      P("<b>Por qué la elegimos:</b>"), *B([
          "Los datos no contienen señal para predecir prioridad (asociación casi cero), pero sí contienen un contrato exacto (el SLA).",
          "Es auditable y repetible: la bitácora y la pantalla muestran la razón de cada recomendación; el equipo puede ajustar umbrales sin reentrenar nada.",
          "Una sola capa de seguridad falla tarde o temprano: si alguien lograra saltarse la validación del formulario, el escape y la CSP aún impiden ejecutar su código, y la base rechaza lo ilegal."]),
      P("<b>Qué otra opción descartamos:</b> (1) un clasificador de IA para la prioridad: sin relación texto-prioridad no aporta, y enviaría datos de clientes a un servicio externo; "
        "(2) quitar etiquetas HTML de lo que escribe el usuario («sanitizar borrando»): pierde información legítima (un mensaje puede citar código) y es más frágil que escapar al mostrar."),
      P("<b>Limitaciones honestas:</b> los datos son sintéticos, el límite de intentos vive en memoria (se reinicia con la app; en producción iría en una base compartida), "
        "y la clave secreta de sesión debe venir de una variable de entorno en producción."),
      Spacer(1, 10), P("Anexo: cómo reproducir", "h2"),
      Preformatted("cd 04_prototipo && pip install flask pandas && python app.py     # http://localhost:5000\n"
                   "cd 06_momento3 && python pruebas_m3.py                           # evidencia de los pasos 3 a 6\n"
                   "Cuentas: admin / supervisor / luis / jose / tomas  (clave = usuario + 123)", S["code"])]


def pie(c, d):
    c.saveState(); c.setFont("A", 8); c.setFillColor(colors.HexColor("#667085"))
    c.drawString(2 * cm, 1.2 * cm, "TicketFlow · Momento 3 · OPL-07"); c.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {d.page}"); c.restoreState()


SimpleDocTemplate(os.path.join(AQUI, "E3_Informe_TicketFlow.pdf"), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                  bottomMargin=2 * cm, title="TicketFlow · Momento 3").build(f, onFirstPage=pie, onLaterPages=pie)
print("PDF listo")
