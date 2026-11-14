"""Crea ticketflow.db (esquema E1 + máquina de estados + notas) y carga el CSV."""
import csv, os, sqlite3, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(AQUI, "..", "01_dataset", "synthetic_it_support_tickets.csv")
DB = os.path.join(AQUI, "ticketflow.db")

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE usuarios (
  user_id INTEGER PRIMARY KEY AUTOINCREMENT,
  usuario TEXT NOT NULL UNIQUE CHECK (length(usuario) > 0),
  nombre TEXT NOT NULL CHECK (length(nombre) > 0),
  rol TEXT NOT NULL CHECK (rol IN ('agente','supervisor','admin')),
  password_hash TEXT NOT NULL,
  activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1))
);
CREATE TABLE clientes (customer_id TEXT PRIMARY KEY);
CREATE TABLE tickets (
  ticket_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES clientes(customer_id),
  created_at TEXT NOT NULL,
  customer_segment TEXT NOT NULL CHECK (customer_segment IN ('individual','small_business','education','enterprise','non_profit')),
  channel TEXT NOT NULL CHECK (length(channel) > 0),
  product_area TEXT NOT NULL,
  issue_type TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('low','medium','high','urgent')),
  status TEXT NOT NULL CHECK (status IN ('open','in_progress','on_hold','resolved','closed_no_action')),
  sla_plan TEXT NOT NULL CHECK (sla_plan IN ('standard','gold','platinum')),
  initial_message TEXT,
  reopened INTEGER NOT NULL DEFAULT 0 CHECK (reopened IN (0,1)),
  region TEXT CHECK (region IS NULL OR region IN ('EU','NA','MEA','LATAM','APAC')),
  asignado_a INTEGER REFERENCES usuarios(user_id)
);
CREATE TABLE resoluciones (
  ticket_id TEXT PRIMARY KEY REFERENCES tickets(ticket_id),
  resolution_summary TEXT,
  resolution_time_hours REAL CHECK (resolution_time_hours >= 0),
  customer_sentiment TEXT,
  csat_score INTEGER CHECK (csat_score BETWEEN 0 AND 5)
);
-- Máquina de estados: única fuente de verdad de las transiciones legales.
CREATE TABLE transiciones (
  desde TEXT NOT NULL, hasta TEXT NOT NULL, PRIMARY KEY (desde, hasta)
);
INSERT INTO transiciones VALUES
  ('open','in_progress'), ('open','on_hold'), ('open','closed_no_action'),
  ('in_progress','on_hold'), ('in_progress','resolved'), ('in_progress','closed_no_action'),
  ('on_hold','in_progress'), ('on_hold','closed_no_action'),
  ('resolved','in_progress');           -- reapertura: vuelve a trabajo, nunca directo a 'open'
-- Un CHECK no ve OLD.status, por eso la regla va en un TRIGGER.
CREATE TRIGGER trg_transicion_estado BEFORE UPDATE OF status ON tickets
WHEN OLD.status <> NEW.status
 AND NOT EXISTS (SELECT 1 FROM transiciones WHERE desde = OLD.status AND hasta = NEW.status)
BEGIN
  SELECT RAISE(ABORT, 'Transicion ilegal de estado');
END;
-- Reapertura: marca el ticket y descarta la resolución anterior.
CREATE TRIGGER trg_reapertura AFTER UPDATE OF status ON tickets
WHEN OLD.status = 'resolved' AND NEW.status = 'in_progress'
BEGIN
  UPDATE tickets SET reopened = 1 WHERE ticket_id = NEW.ticket_id;
  DELETE FROM resoluciones WHERE ticket_id = NEW.ticket_id;
END;
-- Objetivo de resolución (horas) por plan de SLA y prioridad.
CREATE TABLE sla_objetivos (
  sla_plan TEXT NOT NULL CHECK (sla_plan IN ('standard','gold','platinum')),
  priority TEXT NOT NULL CHECK (priority IN ('low','medium','high','urgent')),
  horas_resolucion REAL NOT NULL CHECK (horas_resolucion > 0),
  PRIMARY KEY (sla_plan, priority)
);
CREATE TABLE bitacora (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  usuario TEXT NOT NULL,
  accion TEXT NOT NULL,
  ticket_id TEXT,
  detalle TEXT
);
CREATE TABLE notas (
  nota_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
  agente TEXT NOT NULL CHECK (length(agente) > 0),
  texto TEXT NOT NULL CHECK (length(texto) > 0),
  estado_resultante TEXT NOT NULL,
  creada_en TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INDICES = """
CREATE INDEX idx_tickets_status ON tickets(status);
CREATE INDEX idx_tickets_reopened ON tickets(reopened);
CREATE INDEX idx_tickets_customer ON tickets(customer_id);
CREATE INDEX idx_notas_ticket ON notas(ticket_id);
CREATE INDEX idx_tickets_asignado ON tickets(asignado_a);
CREATE INDEX idx_tickets_created ON tickets(created_at);
"""

USUARIOS = [("admin", "Administrador", "admin"), ("supervisor", "Coordinador TI", "supervisor"),
            ("luis", "Luis Ramos", "agente"), ("jose", "José Enamorado", "agente"),
            ("tomas", "Tomás Tordecilla", "agente")]
BASE_HORAS = {"urgent": 36, "high": 48, "medium": 72, "low": 96}
FACTOR_PLAN = {"standard": 1.0, "gold": 0.75, "platinum": 0.5}


def sembrar(con):
    """Usuarios demo (clave = usuario + '123'), objetivos SLA y asignación inicial de tickets."""
    from werkzeug.security import generate_password_hash
    with con:
        con.executemany("INSERT INTO usuarios (usuario,nombre,rol,password_hash) VALUES (?,?,?,?)",
                        [(u, n, r, generate_password_hash(u + "123")) for u, n, r in USUARIOS])
        con.executemany("INSERT INTO sla_objetivos VALUES (?,?,?)",
                        [(pl, pr, h * f) for pl, f in FACTOR_PLAN.items() for pr, h in BASE_HORAS.items()])
        ids = [r[0] for r in con.execute("SELECT user_id FROM usuarios WHERE rol='agente' ORDER BY user_id")]
        con.execute(f"""UPDATE tickets SET asignado_a = CASE rowid % 3 WHEN 0 THEN {ids[0]} WHEN 1 THEN {ids[1]} ELSE {ids[2]} END
                       WHERE NOT (status = 'open' AND rowid % 2 = 1)""")  # la mitad de los 'open' queda sin asignar


def main():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    with open(CSV, encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    n = lambda v: v if v != "" else None
    with con:
        con.executemany("INSERT INTO clientes VALUES (?)", [(c,) for c in sorted({r["customer_id"] for r in filas})])
        con.executemany(
            """INSERT INTO tickets (ticket_id,customer_id,created_at,customer_segment,channel,product_area,issue_type,
               priority,status,sla_plan,initial_message,reopened,region) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(r["ticket_id"], r["customer_id"], r["created_at"], r["customer_segment"], r["channel"],
              r["product_area"], r["issue_type"], r["priority"], r["status"], r["sla_plan"],
              r["initial_message"], int(r["reopened"]), n(r["region"])) for r in filas])
        con.executemany(
            "INSERT INTO resoluciones VALUES (?,?,?,?,?)",
            [(r["ticket_id"], r["resolution_summary"], float(r["resolution_time_hours"]),
              r["customer_sentiment"], int(r["csat_score"]))
             for r in filas if r["status"] in ("resolved", "closed_no_action") and r["resolution_time_hours"] != ""])
    sembrar(con)
    con.executescript(INDICES)
    print("tickets:", con.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
          "| resoluciones:", con.execute("SELECT COUNT(*) FROM resoluciones").fetchone()[0])
    con.close()

if __name__ == "__main__":
    sys.exit(main())
