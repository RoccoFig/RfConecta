"""Salida de mensajes al cliente. Hoy sale por la consola; acá se conecta la API oficial de WhatsApp."""


def enviar_whatsapp(tel, texto):
    print(f"[WHATSAPP -> {tel}] {texto}", flush=True)
