"""TicketFlow · Momento 3 · importación de tickets desde CSV dentro de la app.

Flujo: leer -> limpiar -> validar -> guardar. Repetir la carga NO duplica: un ticket_id que ya
existe se cuenta como «ya estaba» y se ignora (clave primaria + ON CONFLICT DO NOTHING).
"""
import csv
import io
import re
from datetime import datetime

CATALOGOS = {
    "customer_segment": {"individual", "small_business", "education", "enterprise", "non_profit"},
    "sla_plan": {"standard", "gold", "platinum"},
    "channel": {"email", "chat", "in_app", "phone_transcript", "web_form"},
    "priority": {"low", "medium", "high", "urgent"},
    "status": {"open", "in_progress", "on_hold", "resolved", "closed_no_action"},
}
REGIONES = {"NA", "EU", "MEA", "LATAM", "APAC"}
ABIERTOS = {"open", "in_progress", "on_hold"}
OBLIGATORIAS = ["ticket_id", "customer_id", "created_at", "customer_segment", "sla_plan", "channel",
                "product_area", "issue_type", "priority", "status"]
MAX_BYTES = 5 * 1024 * 1024
MAX_MENSAJE = 2000

DDL_RECHAZOS = """CREATE TABLE IF NOT EXISTS rechazos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  archivo TEXT NOT NULL, fila INTEGER NOT NULL, ticket_id TEXT, motivo TEXT NOT NULL,
  UNIQUE (archivo, fila, motivo))"""


def limpiar_fila(r):
    """Quita espacios, normaliza mayúsculas/minúsculas y convierte vacío en None."""
    r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items() if k}
    for c in ("customer_segment", "sla_plan", "channel", "priority", "status", "product_area", "issue_type"):
        if r.get(c):
            r[c] = r[c].lower()
    for c in ("ticket_id", "customer_id", "region"):
        if r.get(c):
            r[c] = r[c].upper()
    return {k: (v if v not in ("", None) else None) for k, v in r.items()}


def validar(r):
    m = []
    for c in OBLIGATORIAS:
        if not r.get(c):
            m.append(f"{c} vacío")
    if r.get("ticket_id") and not re.fullmatch(r"TCKT_\d{1,12}", r["ticket_id"]):
        m.append(f"ticket_id con formato inválido ({r['ticket_id'][:30]})")
    if r.get("customer_id") and not re.fullmatch(r"CUST_\d{5}", r["customer_id"]):
        m.append(f"customer_id con formato inválido ({r['customer_id'][:30]})")
    try:
        datetime.fromisoformat(r["created_at"] or "")
    except ValueError:
        m.append(f"fecha inválida ({(r['created_at'] or '')[:30]})")
    for col, ok in CATALOGOS.items():
        if r.get(col) and r[col] not in ok:
            m.append(f"{col} fuera de catálogo ({r[col][:30]})")
    if r.get("region") and r["region"] not in REGIONES:
        m.append(f"región no permitida ({r['region'][:20]})")
    if r.get("initial_message") and len(r["initial_message"]) > MAX_MENSAJE:
        m.append(f"mensaje demasiado largo (> {MAX_MENSAJE} caracteres)")
    if r.get("csat_score") is not None:
        try:
            c = float(r["csat_score"])
            if c != int(c) or not 0 <= c <= 5:
                raise ValueError
        except ValueError:
            m.append(f"csat_score fuera de 0–5 ({r['csat_score']})")
    if r.get("resolution_time_hours") is not None:
        try:
            if float(r["resolution_time_hours"]) < 0:
                raise ValueError
        except ValueError:
            m.append(f"tiempo de resolución inválido ({r['resolution_time_hours']})")
    tiene_res = r.get("resolution_summary") and r.get("resolution_time_hours") is not None
    if r.get("status") in ("resolved", "closed_no_action") and not tiene_res:
        m.append(f"ticket {r['status']} sin resolución")
    if r.get("status") in ABIERTOS and r.get("resolution_time_hours") is not None:
        m.append(f"ticket {r['status']} con datos de resolución")
    return m


def importar(conn, contenido, nombre):
    """contenido: bytes del CSV. Devuelve un dict con el resumen. Todo en una transacción."""
    if len(contenido) > MAX_BYTES:
        raise ValueError("El archivo supera el máximo de 5 MB.")
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("El archivo no está en UTF-8.")
    lector = csv.DictReader(io.StringIO(texto, newline=""))
    faltan = [c for c in OBLIGATORIAS if c not in (lector.fieldnames or [])]
    if faltan:
        raise ValueError("Faltan columnas obligatorias: " + ", ".join(faltan))
    nombre = nombre[:80]
    res = dict(entran=0, repetidas_archivo=0, nuevos=0, existentes=0, rechazados=0, motivos=[])
    vistos, exactas = set(), set()
    conn.execute(DDL_RECHAZOS)
    with conn:
        for n, fila in enumerate(lector, start=2):
            res["entran"] += 1
            r = limpiar_fila(fila)
            clave = tuple(sorted((k, v) for k, v in r.items()))
            if clave in exactas:                     # duplicado exacto dentro del archivo
                res["repetidas_archivo"] += 1
                continue
            exactas.add(clave)
            motivos = validar(r)
            if not motivos and r["ticket_id"] in vistos:
                motivos = ["ticket_id repetido con datos distintos dentro del archivo"]
            if motivos:
                res["rechazados"] += 1
                for mo in motivos:
                    conn.execute("INSERT OR IGNORE INTO rechazos (archivo,fila,ticket_id,motivo) VALUES (?,?,?,?)",
                                 (nombre, n, (r.get("ticket_id") or "")[:30] or None, mo))
                res["motivos"].append((n, r.get("ticket_id"), "; ".join(motivos)))
                continue
            vistos.add(r["ticket_id"])
            if conn.execute("SELECT 1 FROM tickets WHERE ticket_id = ?", (r["ticket_id"],)).fetchone():
                res["existentes"] += 1
                continue
            conn.execute("INSERT OR IGNORE INTO clientes (customer_id) VALUES (?)", (r["customer_id"],))
            conn.execute("""INSERT INTO tickets (ticket_id,customer_id,created_at,customer_segment,channel,product_area,
                              issue_type,priority,status,sla_plan,initial_message,reopened,region)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticket_id) DO NOTHING""",
                         (r["ticket_id"], r["customer_id"], r["created_at"], r["customer_segment"], r["channel"],
                          r["product_area"], r["issue_type"], r["priority"], r["status"], r["sla_plan"],
                          r.get("initial_message"), 1 if r.get("reopened") in ("1", "true", "True") else 0, r.get("region")))
            if r.get("resolution_time_hours") is not None:
                conn.execute("INSERT OR IGNORE INTO resoluciones VALUES (?,?,?,?,?)",
                             (r["ticket_id"], r["resolution_summary"], float(r["resolution_time_hours"]),
                              r.get("customer_sentiment"), None if r.get("csat_score") is None else int(float(r["csat_score"]))))
            res["nuevos"] += 1
    return res
