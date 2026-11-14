# TicketFlow — Sistema de Priorización y Seguimiento de Tickets de Soporte TI

Proyecto Integrador · Momento Evaluativo E1
Universidad Cooperativa de Colombia — Ingeniería de Sistemas, 9° semestre (2026-II)

**Autores:** Luis Ramos, José Enamorado Tomas Tordecilla
**Profesor:** Joseph David Gari Bustos

## Problema

Los equipos de soporte técnico reciben un volumen alto y constante de tickets, con distintos
niveles de prioridad y planes de servicio (SLA). Sin una herramienta que consolide esta
información, es difícil detectar a tiempo qué tickets llevan demasiado tiempo sin atención,
qué áreas del producto concentran más carga, o qué clientes reportan el mismo problema de
forma recurrente (tickets reabiertos).

**Usuario:** agentes de soporte técnico y su supervisor/coordinador de equipo.

**Propósito:** consultar de forma centralizada y confiable el estado de los tickets por
prioridad y área, el cumplimiento real de los SLA, y los clientes con reaperturas frecuentes.

## Dataset

- **Fuente:** Kaggle — [Synthetic IT Support Tickets](https://www.kaggle.com/datasets/ahsanneural/synthetic-it-support-tickets) (ahsanneural).
- **Contenido:** 100,000 tickets sintéticos de soporte de una empresa SaaS: cliente, canal,
  área del producto, tipo de problema, prioridad, SLA, estado, y (si ya fue resuelto) tiempo
  de resolución, sentimiento y CSAT.
- Elegido por su estructura tabular rica en variables categóricas y numéricas, ideal para
  modelar un esquema relacional con reglas de integridad defendibles, sin necesidad de ML.

## Estructura del repositorio

```
E1_TicketFlow/
├── 01_dataset/
│   └── synthetic_it_support_tickets.csv   # dataset original (100k filas)
├── 02_notebook/
│   └── E1_TicketFlow.ipynb                # perfilado, modelo, carga a SQLite, consultas
├── 03_informe/
│   └── E1_Informe.pdf                     # informe escrito del E1
└── README.md
```

## Principales hallazgos del perfilado

- `ticket_id` no tiene duplicados → clave primaria confiable.
- `resolution_summary` y `resolution_time_hours` tienen 39,887 nulos cada una, siempre en
  tickets `open`, `in_progress` u `on_hold` → nulo lógico, no error de calidad.
- `region` tiene 20% de nulos reales, sin relación con el canal de entrada.
- **Hallazgo clave para el diseño:** `customer_segment`, `sla_plan` y `region` **no son
  atributos fijos del cliente** — prácticamente todos los clientes (9,987–9,900 de 9,999)
  presentan más de un valor distinto entre sus tickets. Por eso se modelan como datos del
  momento del ticket, no del cliente.
- Las columnas categóricas clave (`priority`, `status`, `sla_plan`, `customer_segment`) no
  presentan valores fuera de catálogo → candidatas directas a restricciones `CHECK`.

## Modelo relacional

Tres tablas normalizadas en SQLite:

- **clientes** — `customer_id` (PK).
- **tickets** — un registro por ticket, con datos operativos (canal, área, tipo, prioridad,
  estado) y `customer_segment` / `sla_plan` / `region` como atributos del momento del ticket.
- **resoluciones** — una fila solo para tickets ya resueltos (resumen, tiempo, sentimiento, CSAT).

Relación: `clientes` 1—N `tickets`; `tickets` 1—1 `resoluciones`.

Separar `resoluciones` evita que ~40% de las filas de `tickets` tengan campos vacíos por
diseño, y refleja que esos datos solo existen tras el evento de resolución.

## Reglas de integridad implementadas

| Hallazgo | Regla implementada |
|---|---|
| `ticket_id` sin duplicados | `PRIMARY KEY` en `tickets.ticket_id` |
| Todo ticket pertenece a un cliente | `FOREIGN KEY tickets.customer_id → clientes.customer_id` |
| Resolución solo existe si el ticket se resolvió | Tabla `resoluciones` separada; PK/FK `resoluciones.ticket_id → tickets.ticket_id` |
| Categorías dentro de catálogo fijo | `CHECK IN (...)` en `priority`, `status`, `sla_plan`, `customer_segment` |
| Campos operativos siempre presentes | `NOT NULL` en `channel`, `priority`, `status`, `customer_segment`, `sla_plan`, `created_at` |
| `region` con nulos reales | Permite `NULL`, con `CHECK` de dominio cuando sí tiene valor |
| Rangos de sentido | `CHECK csat_score BETWEEN 0 AND 5`; `CHECK resolution_time_hours >= 0` |

Probado insertando deliberadamente 3 registros inválidos (cliente inexistente, prioridad
`'urgentt'`, canal vacío) — los tres fueron rechazados por la base de datos.

> **Pendiente / punto crítico para la sustentación:** el modelo actual valida que `status`
> esté dentro del catálogo, pero **no valida que la transición entre estados sea legal**
> (ej. `resolved → open` directo). Eso requiere un `TRIGGER BEFORE UPDATE` que compare
> `OLD.status` con `NEW.status`, ya que un `CHECK` no puede ver el valor anterior de la fila.
> Esta es la pregunta de rúbrica asignada al grupo ("Prioridad/estados con integridad") y debe
> resolverse antes de la exposición.

## Preguntas de negocio respondidas

1. **¿Se cumple la promesa de prioridad?** Tiempo promedio de resolución por prioridad:
   urgent 26.71h, high 31.56h, medium 43.28h, low 55.61h — patrón coherente, sin inversiones.
2. **¿Dónde enfocar recursos hoy?** Tickets abiertos (`open`/`in_progress`/`on_hold`) por área
   del producto — carga relativamente pareja entre las 7 áreas, sin punto crítico.
3. **¿Qué clientes requieren atención prioritaria?** Clientes con más de un ticket reabierto —
   el caso máximo presentó 5 reaperturas, candidato a marcarse como cuenta de riesgo.

## Rendimiento

Consulta de clientes con tickets reabiertos (`reopened = 1`, 5.05% de las filas):

- **Antes:** `SCAN tickets` (recorrido completo de 100,000 filas).
- **Índice creado:** `CREATE INDEX idx_tickets_reopened ON tickets(reopened)`.
- **Después:** `SEARCH tickets USING INDEX idx_tickets_reopened`.
- El `GROUP BY customer_id` sigue usando `TEMP B-TREE` porque el índice es sobre `reopened`,
  no sobre `customer_id` — la mejora es real pero parcial.

## Transaccionalidad

Se probó que `UPDATE tickets.status` + `INSERT` en `resoluciones` ocurren como una sola
operación (`with conn:` maneja `BEGIN/COMMIT/ROLLBACK`): si el `INSERT` viola una restricción
(`UNIQUE`, `CHECK`), el `UPDATE` también se revierte — no quedan cambios a medias.

## Cómo reproducir

1. Abrir `02_notebook/E1_TicketFlow.ipynb` en Google Colab.
2. Montar Google Drive y ubicar `synthetic_it_support_tickets.csv` en
   `MyDrive/E1_TicketsTI/` (o ajustar `ruta_csv`).
3. Ejecutar las celdas en orden: perfilado → creación de esquema SQLite → carga de datos →
   pruebas de integridad → consultas de negocio → análisis de rendimiento → transacciones.

## Próximos pasos

- **E2 — Integración e ingeniería de datos:** conectar el núcleo de datos con un servicio real
  que reciba tickets nuevos, validando las mismas reglas de integridad.
- **E3 — Solución funcional:** panel funcional sobre las 3 consultas de negocio, más el
  registro de notas de seguimiento del agente como primera transacción recurrente real del
  proyecto (agregar nota + actualizar estado en una sola operación inseparable).
