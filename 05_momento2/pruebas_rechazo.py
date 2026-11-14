"""TicketFlow · Momento 2 · evidencia de rechazos en la base.

Se ejecuta después de pipeline.py. Trabaja sobre un ticket de prueba nuevo
para no alterar los datos originales.
"""
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).parent / "ticketflow_m2.db"

# Lo que la app le muestra a la persona según el error de la base
MENSAJES = {
    "TRANSICION_INVALIDA": "No se puede pasar el ticket a ese estado desde el estado actual.",
    "RESUELTO_SIN_RESOLUCION": "Para resolver o cerrar el ticket primero registre la resolución.",
    "csat_score": "La calificación del cliente debe estar entre 0 y 5.",
    "FOREIGN KEY": "El cliente no existe.",
    "region": "La región debe ser NA, EU, MEA, LATAM o APAC.",
}


def mensaje(error):
    return next((v for k, v in MENSAJES.items() if k in str(error)), str(error))


def intentar(conn, titulo, pasos):
    print(f"\n> {titulo}")
    try:
        conn.execute("BEGIN")
        for sql, params in pasos:
            conn.execute(sql, params)
        conn.execute("COMMIT")
        print("  ACEPTADO")
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        print(f"  RECHAZADO por la base: {e}")
        print(f"  La app muestra: \"{mensaje(e)}\"")


def estado(conn, t):
    return conn.execute("SELECT status FROM tickets WHERE ticket_id = ?", (t,)).fetchone()[0]


conn = sqlite3.connect(DB, isolation_level=None)
conn.execute("PRAGMA foreign_keys = ON")

T = "TCKT_999999"
conn.execute("DELETE FROM historial_estados WHERE ticket_id = ?", (T,))
existe = conn.execute("SELECT 1 FROM tickets WHERE ticket_id = ?", (T,)).fetchone()
if existe:  # reinicia el ticket de prueba para que la demo se pueda repetir
    conn.execute("DROP TRIGGER trg_no_borrar_resolucion")
    conn.execute("DELETE FROM resoluciones WHERE ticket_id = ?", (T,))
    conn.execute("DELETE FROM tickets WHERE ticket_id = ?", (T,))
    conn.executescript((Path(__file__).parent / "esquema.sql").read_text(encoding="utf-8"))

conn.execute("""INSERT INTO tickets (ticket_id, customer_id, created_at, customer_segment, sla_plan,
                region, channel, product_area, issue_type, priority, status)
                VALUES (?, 'CUST_00861', '2026-10-24T08:00:00', 'enterprise', 'gold',
                'LATAM', 'email', 'billing', 'billing_problem', 'high', 'open')""", (T,))
print(f"Ticket de prueba {T} creado en estado: {estado(conn, T)}")

UPD = "UPDATE tickets SET status = ? WHERE ticket_id = ?"
RES = ("INSERT INTO resoluciones (ticket_id, resolution_summary, resolution_time_hours, "
       "customer_sentiment, csat_score) VALUES (?, ?, ?, ?, ?)")

print("\n=== 1. Saltos ilegales de estado ===")
intentar(conn, "open -> closed (saltando in_progress y resolved)", [(UPD, ("closed", T))])
intentar(conn, "open -> resolved (saltando in_progress)", [(UPD, ("resolved", T))])
intentar(conn, "open -> closed aunque ya se registre una resolución",
         [(RES, (T, "Cierre directo.", 1.0, "neutral", 3)), (UPD, ("closed", T))])

print("\n=== 2. Camino legal ===")
intentar(conn, "open -> in_progress", [(UPD, ("in_progress", T))])
intentar(conn, "in_progress -> resolved SIN registrar resolución", [(UPD, ("resolved", T))])
intentar(conn, "in_progress -> resolved con resolución pero csat_score = 9",
         [(RES, (T, "Se corrigió el cobro doble.", 5.0, "positive", 9)), (UPD, ("resolved", T))])
print(f"  Estado después del rechazo: {estado(conn, T)} (no quedó nada a medias)")
intentar(conn, "in_progress -> resolved con resolución válida (csat 4), en una transacción",
         [(RES, (T, "Se corrigió el cobro doble.", 5.0, "positive", 4)), (UPD, ("resolved", T))])
intentar(conn, "resolved -> closed", [(UPD, ("closed", T))])
intentar(conn, "Borrar la resolución de un ticket cerrado",
         [("DELETE FROM resoluciones WHERE ticket_id = ?", (T,))])
intentar(conn, "closed -> open (reabrir un ticket ya cerrado)", [(UPD, ("open", T))])

print("\n=== 3. Historial que guardó la base ===")
for d, h in conn.execute("SELECT desde, hacia FROM historial_estados WHERE ticket_id = ? ORDER BY id", (T,)):
    print(f"  {d} -> {h}")

print("\n=== 4. Otros rechazos directos en la base (sin pasar por el programa) ===")
intentar(conn, "Ticket de un cliente que no existe",
         [("""INSERT INTO tickets (ticket_id, customer_id, created_at, customer_segment, sla_plan,
              channel, product_area, issue_type, priority, status)
              VALUES ('TCKT_999998','CUST_NO_EXISTE','2026-10-24','individual','standard',
              'email','billing','bug','low','open')""", ())])
intentar(conn, "Región no permitida ('XX')",
         [("""INSERT INTO tickets (ticket_id, customer_id, created_at, customer_segment, sla_plan,
              region, channel, product_area, issue_type, priority, status)
              VALUES ('TCKT_999997','CUST_00861','2026-10-24','individual','standard','XX',
              'email','billing','bug','low','open')""", ())])
intentar(conn, "Insertar un ticket directamente como resolved sin resolución",
         [("""INSERT INTO tickets (ticket_id, customer_id, created_at, customer_segment, sla_plan,
              channel, product_area, issue_type, priority, status)
              VALUES ('TCKT_999996','CUST_00861','2026-10-24','individual','standard',
              'email','billing','bug','low','resolved')""", ())])

print("\n=== 5. Revisión de calidad sobre toda la base ===")
checks = {
    "Tickets resolved/closed sin resolución":
        "SELECT COUNT(*) FROM tickets t LEFT JOIN resoluciones r USING(ticket_id) "
        "WHERE t.status IN ('resolved','closed') AND r.ticket_id IS NULL",
    "Resoluciones con csat fuera de 0-5":
        "SELECT COUNT(*) FROM resoluciones WHERE csat_score NOT BETWEEN 0 AND 5",
    "Tickets con región no permitida":
        "SELECT COUNT(*) FROM tickets WHERE region IS NOT NULL "
        "AND region NOT IN ('NA','EU','MEA','LATAM','APAC')",
    "Tickets abiertos con resolución":
        "SELECT COUNT(*) FROM tickets t JOIN resoluciones r USING(ticket_id) "
        "WHERE t.status IN ('open','in_progress','on_hold')",
    "Tickets con región NA (antes leída como nulo)":
        "SELECT COUNT(*) FROM tickets WHERE region = 'NA'",
    "Tickets con región realmente nula":
        "SELECT COUNT(*) FROM tickets WHERE region IS NULL",
}
for k, q in checks.items():
    print(f"  {k}: {conn.execute(q).fetchone()[0]:,}")

print("\n=== 6. Índice compuesto (reopened, customer_id) ===")
Q = ("SELECT customer_id, COUNT(*) FROM tickets WHERE reopened = 1 "
     "GROUP BY customer_id HAVING COUNT(*) > 1 ORDER BY 2 DESC LIMIT 10")
for fila in conn.execute("EXPLAIN QUERY PLAN " + Q):
    print("  ", fila[-1])
inicio = time.perf_counter()
for _ in range(20):
    conn.execute(Q).fetchall()
print(f"  Tiempo promedio: {(time.perf_counter() - inicio) / 20 * 1000:.1f} ms")
print("  Top 3:", conn.execute(Q).fetchall()[:3])

print("\n=== 7. Volumen ===")
print("  Tickets:", conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
print("  Rango de fechas:", conn.execute("SELECT MIN(created_at), MAX(created_at) FROM tickets "
                                         "WHERE ticket_id NOT LIKE 'TCKT_9999%'").fetchone())
print("  Tickets por año:", conn.execute("SELECT substr(created_at,1,4), COUNT(*) FROM tickets "
                                         "GROUP BY 1 ORDER BY 1").fetchall())
print(f"  Tamaño del archivo .db: {DB.stat().st_size / 1024 / 1024:.1f} MB")
conn.close()
