"""TicketFlow · Momento 3 · reglas deterministas (sin IA).

Cada regla es una función pura: mismos datos de entrada -> misma respuesta, y siempre devuelve
la razón, para que el agente pueda ver POR QUÉ la app le recomienda algo.
"""
import re

UMBRAL_SLA_RIESGO = 0.80          # a partir de 80 % del tiempo del SLA consumido, el ticket está "en riesgo"
REAPERTURAS_CRITICAS = 3          # cuentas con 3+ reaperturas se contactan de forma proactiva

ORDEN = ["low", "medium", "high", "urgent"]
# Prioridad base por tipo de problema (tabla simple y revisable por el equipo de soporte)
BASE_POR_TIPO = {"security_concern": "urgent", "account_access": "high", "billing_problem": "high",
                 "bug": "medium", "performance": "medium", "feature_request": "low", "how_to": "low", "other": "low"}
# Palabras del mensaje que suben la prioridad (inglés: así vienen los mensajes del dataset)
PALABRAS_URGENTES = re.compile(r"\b(breach|outage|data loss|unauthorized|hacked|down for everyone)\b", re.I)
PALABRAS_ALTAS = re.compile(r"\b(locked|cannot log|can't log|charged twice|still being billed|not working|incorrect)\b", re.I)


def sugerir_prioridad(tipo, mensaje, reaperturas_cliente=0):
    """Prioridad sugerida + lista de motivos legibles."""
    nivel = ORDEN.index(BASE_POR_TIPO.get(tipo, "low"))
    motivos = [f"tipo de problema «{tipo}»"]
    if PALABRAS_URGENTES.search(mensaje or ""):
        nivel, motivos = max(nivel, 3), motivos + ["el mensaje menciona una caída, fuga o acceso no autorizado"]
    elif PALABRAS_ALTAS.search(mensaje or ""):
        nivel, motivos = max(nivel, 2), motivos + ["el mensaje indica bloqueo de acceso o cobro incorrecto"]
    if reaperturas_cliente >= 2 and nivel < 3:
        nivel, motivos = nivel + 1, motivos + [f"el cliente tiene {reaperturas_cliente} tickets reabiertos"]
    return ORDEN[nivel], motivos


def consumo_sla(horas_abierto, objetivo):
    """Fracción del tiempo del SLA ya gastada (1.0 = vence ahora)."""
    return horas_abierto / objetivo if objetivo else 0.0


def clasificar_sla(horas_abierto, objetivo):
    c = consumo_sla(horas_abierto, objetivo)
    return "vencido" if c > 1 else ("en_riesgo" if c >= UMBRAL_SLA_RIESGO else "a_tiempo")


def recomendar(ticket):
    """ticket: dict con prioridad, horas_abierto, objetivo, asignado_a, status. Devuelve (accion, motivo) o None."""
    estado = clasificar_sla(ticket["horas_abierto"], ticket["objetivo"])
    if estado == "a_tiempo":
        return None
    sin_dueno = ticket["asignado_a"] is None
    if estado == "vencido":
        if sin_dueno:
            return "Asignar de inmediato", "SLA vencido y nadie lo tiene asignado"
        return "Escalar al supervisor", "SLA vencido: ya superó el tiempo prometido al cliente"
    if sin_dueno:
        return "Tomar el ticket ahora", f"ha consumido {consumo_sla(ticket['horas_abierto'], ticket['objetivo']):.0%} del SLA y no tiene responsable"
    return "Priorizar hoy", f"ha consumido {consumo_sla(ticket['horas_abierto'], ticket['objetivo']):.0%} del tiempo del SLA"
