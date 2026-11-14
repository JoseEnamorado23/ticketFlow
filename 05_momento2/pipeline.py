"""TicketFlow · Momento 2 · proceso de datos.

archivo original -> limpiar -> validar -> guardar en clientes / tickets / resoluciones.
Uso:  python pipeline.py <archivo.csv> [<archivo2.csv> ...]
Se puede correr varias veces: lo que ya está guardado no se duplica.
"""
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent
DB = AQUI / "ticketflow_m2.db"

CATALOGOS = {
    "customer_segment": {"individual", "small_business", "education", "enterprise", "non_profit"},
    "sla_plan": {"standard", "gold", "platinum"},
    "channel": {"email", "chat", "in_app", "phone_transcript", "web_form"},
    "priority": {"low", "medium", "high", "urgent"},
    "status": {"open", "in_progress", "on_hold", "resolved", "closed", "closed_no_action"},
}
REGIONES = {"NA", "EU", "MEA", "LATAM", "APAC"}
ABIERTOS = {"open", "in_progress", "on_hold"}
REQUIEREN_RESOLUCION = {"resolved", "closed"}

COLS_TICKET = ["ticket_id", "customer_id", "created_at", "customer_segment", "sla_plan", "region",
               "channel", "product_area", "issue_type", "priority", "status", "initial_message",
               "agent_first_reply", "platform", "has_attachment", "reopened"]
COLS_RESOL = ["ticket_id", "resolution_summary", "resolution_time_hours",
              "customer_sentiment", "csat_score"]


def conectar():
    # isolation_level=None: las transacciones se abren y cierran a mano (BEGIN/COMMIT)
    conn = sqlite3.connect(DB, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((AQUI / "esquema.sql").read_text(encoding="utf-8"))
    return conn


def leer(ruta):
    # keep_default_na=False: si no, pandas convierte la región 'NA' (Norteamérica) en nulo
    return pd.read_csv(ruta, dtype=str, keep_default_na=False, na_values=[""])


def limpiar(df):
    df = df.copy()
    for c in df.columns:
        df[c] = df[c].str.strip()
    for c in ["customer_segment", "sla_plan", "channel", "priority", "status",
              "product_area", "issue_type", "platform", "customer_sentiment"]:
        df[c] = df[c].str.lower()
    for c in ["ticket_id", "customer_id", "region"]:
        df[c] = df[c].str.upper()
    df = df.replace({"": None})
    df = df.astype(object).where(pd.notnull(df), None)
    df["fila"] = range(2, len(df) + 2)  # fila del archivo (la 1 es el encabezado)
    duplicadas = df.duplicated(subset=[c for c in df.columns if c != "fila"])
    return df[~duplicadas], int(duplicadas.sum())


def numero(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validar(r):
    """Devuelve la lista de motivos de rechazo de una fila (vacía si es válida)."""
    m = []
    if not r["ticket_id"] or not re.fullmatch(r"TCKT_\d+", r["ticket_id"]):
        m.append("ticket_id vacío o con formato inválido")
    if not r["customer_id"]:
        m.append("ticket sin cliente")
    try:
        datetime.fromisoformat(r["created_at"])
    except (TypeError, ValueError):
        m.append(f"fecha de creación inválida ({r['created_at']})")
    for col, valores in CATALOGOS.items():
        if r[col] not in valores:
            m.append(f"{col} fuera de catálogo ({r[col]})")
    if r["region"] is not None and r["region"] not in REGIONES:
        m.append(f"región no permitida ({r['region']})")
    if r["csat_score"] is not None:
        c = numero(r["csat_score"])
        if c is None or c != int(c) or not 0 <= c <= 5:
            m.append(f"csat_score fuera de 0-5 ({r['csat_score']})")
    t = numero(r["resolution_time_hours"])
    if r["resolution_time_hours"] is not None and (t is None or t < 0):
        m.append(f"tiempo de resolución negativo o inválido ({r['resolution_time_hours']})")
    tiene_resolucion = r["resolution_summary"] is not None and r["resolution_time_hours"] is not None
    if r["status"] in REQUIEREN_RESOLUCION and not tiene_resolucion:
        m.append(f"ticket {r['status']} sin resolución")
    if r["status"] in ABIERTOS and r["resolution_time_hours"] is not None:
        m.append(f"ticket {r['status']} con datos de resolución")
    return m


def guardar(conn, archivo, df):
    nuevos = existentes = rech_base = 0
    rechazos = []
    vistos = set()
    conn.execute("BEGIN")
    try:
        for r in df.to_dict("records"):
            motivos = validar(r)
            if not motivos and r["ticket_id"] in vistos:
                motivos = ["ticket_id repetido dentro del archivo"]
            if motivos:
                rechazos += [(archivo, r["fila"], r["ticket_id"], mo) for mo in motivos]
                continue
            vistos.add(r["ticket_id"])
            if conn.execute("SELECT 1 FROM tickets WHERE ticket_id = ?", (r["ticket_id"],)).fetchone():
                existentes += 1
                continue
            conn.execute("SAVEPOINT fila")
            try:
                conn.execute("INSERT INTO clientes (customer_id) VALUES (?) "
                             "ON CONFLICT(customer_id) DO NOTHING", (r["customer_id"],))
                # primero la resolución (FK diferida) para que el trigger encuentre la resolución
                if r["resolution_time_hours"] is not None:
                    vals = [r[c] for c in COLS_RESOL]
                    vals[4] = None if vals[4] is None else int(float(vals[4]))
                    conn.execute(f"INSERT INTO resoluciones ({','.join(COLS_RESOL)}) "
                                 f"VALUES ({','.join('?' * len(COLS_RESOL))})", vals)
                vals = [r[c] for c in COLS_TICKET]
                vals[-2] = int(vals[-2] or 0)
                vals[-1] = int(vals[-1] or 0)
                conn.execute(f"INSERT INTO tickets ({','.join(COLS_TICKET)}) "
                             f"VALUES ({','.join('?' * len(COLS_TICKET))}) "
                             f"ON CONFLICT(ticket_id) DO NOTHING", vals)
                conn.execute("RELEASE fila")
                nuevos += 1
            except sqlite3.IntegrityError as e:
                conn.execute("ROLLBACK TO fila")
                conn.execute("RELEASE fila")
                rech_base += 1
                rechazos.append((archivo, r["fila"], r["ticket_id"], f"rechazado por la base: {e}"))
        conn.executemany("INSERT INTO rechazos (archivo, fila, ticket_id, motivo) VALUES (?,?,?,?) "
                         "ON CONFLICT DO NOTHING", rechazos)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    filas_rechazadas = len({(a, f) for a, f, _, _ in rechazos})
    return nuevos, existentes, filas_rechazadas, rechazos


def procesar(conn, ruta):
    ruta = Path(ruta)
    crudo = leer(ruta)
    df, dup = limpiar(crudo)
    nuevos, existentes, n_rech, rechazos = guardar(conn, ruta.name, df)
    print(f"\n== {ruta.name} ==")
    print(f"Registros que entran:          {len(crudo):>7,}")
    print(f"Duplicados exactos eliminados: {dup:>7,}")
    print(f"Aceptados (nuevos guardados):  {nuevos:>7,}")
    print(f"Ya estaban en la base:         {existentes:>7,}")
    print(f"Rechazados:                    {n_rech:>7,}")
    for a, f, t, mo in rechazos[:20]:
        print(f"   fila {f:>3} {t or '(sin id)':<14} -> {mo}")


if __name__ == "__main__":
    conn = conectar()
    for ruta in sys.argv[1:]:
        procesar(conn, ruta)
    tot = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ["clientes", "tickets", "resoluciones", "rechazos"]}
    print("\nTotales en la base:", tot)
    conn.close()
