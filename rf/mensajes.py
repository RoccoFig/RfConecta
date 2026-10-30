"""Salida de mensajes al cliente. Hoy no hay WhatsApp real: en desarrollo el mensaje sale por la consola;
en producción NO se escribe (el código de ingreso es una credencial y no debe quedar en los registros)."""
import os


def enviar_whatsapp(tel, texto):
    if os.environ.get("RENDER") and not os.environ.get("MOSTRAR_CODIGOS_EN_REGISTRO"):
        print(f"[WHATSAPP -> ****{tel[-4:]}] (mensaje no enviado: WhatsApp real sin configurar)", flush=True)
        return
    print(f"[WHATSAPP -> {tel}] {texto}", flush=True)
