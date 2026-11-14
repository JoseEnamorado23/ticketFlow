-- TicketFlow · Momento 2 · esquema corregido
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS clientes (
    customer_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id         TEXT PRIMARY KEY,
    customer_id       TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    customer_segment  TEXT NOT NULL CHECK (customer_segment IN
                          ('individual','small_business','education','enterprise','non_profit')),
    sla_plan          TEXT NOT NULL CHECK (sla_plan IN ('standard','gold','platinum')),
    -- 'NA' = Norteamérica (en el E1 se leyó por error como nulo)
    region            TEXT CHECK (region IS NULL OR region IN ('NA','EU','MEA','LATAM','APAC')),
    channel           TEXT NOT NULL CHECK (channel IN
                          ('email','chat','in_app','phone_transcript','web_form')),
    product_area      TEXT NOT NULL,
    issue_type        TEXT NOT NULL,
    priority          TEXT NOT NULL CHECK (priority IN ('low','medium','high','urgent')),
    status            TEXT NOT NULL CHECK (status IN
                          ('open','in_progress','on_hold','resolved','closed','closed_no_action')),
    initial_message   TEXT,
    agent_first_reply TEXT,
    platform          TEXT,
    has_attachment    INTEGER NOT NULL DEFAULT 0 CHECK (has_attachment IN (0,1)),
    reopened          INTEGER NOT NULL DEFAULT 0 CHECK (reopened IN (0,1)),
    -- Corrección 1: llave foránea declarada explícitamente
    CONSTRAINT fk_tickets_clientes FOREIGN KEY (customer_id) REFERENCES clientes(customer_id)
);

CREATE TABLE IF NOT EXISTS resoluciones (
    ticket_id             TEXT PRIMARY KEY,
    resolution_summary    TEXT NOT NULL,
    resolution_time_hours REAL NOT NULL CHECK (resolution_time_hours >= 0),
    customer_sentiment    TEXT CHECK (customer_sentiment IN
                              ('very_negative','negative','neutral','positive','very_positive')),
    csat_score            INTEGER CHECK (csat_score BETWEEN 0 AND 5),
    -- DEFERRABLE: permite guardar la resolución y el ticket en la misma transacción
    CONSTRAINT fk_resoluciones_tickets FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
        DEFERRABLE INITIALLY DEFERRED
);

-- Máquina de estados: solo estas transiciones son legales
CREATE TABLE IF NOT EXISTS transiciones_validas (
    desde TEXT NOT NULL,
    hacia TEXT NOT NULL,
    PRIMARY KEY (desde, hacia)
);
INSERT OR IGNORE INTO transiciones_validas (desde, hacia) VALUES
    ('open','in_progress'),
    ('open','closed_no_action'),
    ('in_progress','on_hold'),
    ('on_hold','in_progress'),
    ('in_progress','resolved'),
    ('in_progress','closed_no_action'),
    ('on_hold','closed_no_action'),
    ('resolved','closed'),
    ('resolved','in_progress');   -- reapertura

CREATE TABLE IF NOT EXISTS historial_estados (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    desde     TEXT NOT NULL,
    hacia     TEXT NOT NULL,
    fecha     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rechazos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    archivo   TEXT NOT NULL,
    fila      INTEGER NOT NULL,
    ticket_id TEXT,
    motivo    TEXT NOT NULL,
    UNIQUE (archivo, fila, motivo)
);

-- Corrección 2: índice compuesto para la consulta de reabiertos por cliente
DROP INDEX IF EXISTS idx_tickets_reopened;
CREATE INDEX IF NOT EXISTS idx_tickets_reopened_cliente ON tickets(reopened, customer_id);

-- Corrección 3: todo ticket resolved/closed debe tener su resolución
CREATE TRIGGER IF NOT EXISTS trg_resuelto_requiere_resolucion_ins
BEFORE INSERT ON tickets
WHEN NEW.status IN ('resolved','closed')
 AND NOT EXISTS (SELECT 1 FROM resoluciones WHERE ticket_id = NEW.ticket_id)
BEGIN
    SELECT RAISE(ABORT, 'RESUELTO_SIN_RESOLUCION');
END;

CREATE TRIGGER IF NOT EXISTS trg_resuelto_requiere_resolucion_upd
BEFORE UPDATE OF status ON tickets
WHEN NEW.status IN ('resolved','closed')
 AND NOT EXISTS (SELECT 1 FROM resoluciones WHERE ticket_id = NEW.ticket_id)
BEGIN
    SELECT RAISE(ABORT, 'RESUELTO_SIN_RESOLUCION');
END;

-- Regla de estados: rechaza cualquier salto que no esté en transiciones_validas
-- (se crea después para que SQLite la revise primero en un UPDATE)
CREATE TRIGGER IF NOT EXISTS trg_transicion_valida
BEFORE UPDATE OF status ON tickets
WHEN OLD.status <> NEW.status
 AND NOT EXISTS (SELECT 1 FROM transiciones_validas
                 WHERE desde = OLD.status AND hacia = NEW.status)
BEGIN
    SELECT RAISE(ABORT, 'TRANSICION_INVALIDA');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_borrar_resolucion
BEFORE DELETE ON resoluciones
WHEN EXISTS (SELECT 1 FROM tickets
             WHERE ticket_id = OLD.ticket_id AND status IN ('resolved','closed'))
BEGIN
    SELECT RAISE(ABORT, 'RESUELTO_SIN_RESOLUCION');
END;

-- Reapertura marca el ticket y todo cambio queda en el historial
CREATE TRIGGER IF NOT EXISTS trg_reapertura
AFTER UPDATE OF status ON tickets
WHEN OLD.status = 'resolved' AND NEW.status = 'in_progress'
BEGIN
    UPDATE tickets SET reopened = 1 WHERE ticket_id = NEW.ticket_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_historial
AFTER UPDATE OF status ON tickets
WHEN OLD.status <> NEW.status
BEGIN
    INSERT INTO historial_estados (ticket_id, desde, hacia)
    VALUES (NEW.ticket_id, OLD.status, NEW.status);
END;
