"""Verifica el link que genera un tag NTAG 424 DNA con SDM (Secure Dynamic Messaging).

Cada vez que alguien apoya el celular, el tag arma un link distinto:
    https://tudominio/t?picc_data=<32 hex>&cmac=<16 hex>
- picc_data: UID del tag + contador de lecturas, cifrados con la clave "meta".
- cmac: firma del mensaje, calculada con una clave derivada de la clave "file".
Sin las claves nadie puede fabricar un link válido, y el contador impide reusar uno viejo.
"""
import hmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC


def _cmac(clave, datos):
    c = CMAC(algorithms.AES(clave))
    c.update(datos)
    return c.finalize()


def verificar(picc_hex, cmac_hex, meta_key, file_key, enc_hex=""):
    """Devuelve (uid_hex, contador) si el escaneo es auténtico. Si no, lanza ValueError.

    enc_hex: si el tag también manda datos cifrados (&enc=...), la firma cubre el texto
    "<enc>&cmac=" tal como aparece en el link; sin enc, la firma es sobre un texto vacío.
    """
    try:
        picc = bytes.fromhex(picc_hex)
        recibido = bytes.fromhex(cmac_hex)
        enc = bytes.fromhex(enc_hex)
    except ValueError:
        raise ValueError("formato inválido")
    if len(picc) != 16 or len(recibido) != 8 or len(enc) % 16 or len(enc) > 128:
        raise ValueError("largo inválido")
    d = Cipher(algorithms.AES(meta_key), modes.CBC(bytes(16))).decryptor()
    datos = d.update(picc) + d.finalize()
    marca = datos[0]
    if not (marca & 0x80 and marca & 0x40 and (marca & 0x0F) == 7):
        raise ValueError("datos inesperados")
    uid, contador = datos[1:8], datos[8:11]
    sv2 = bytes([0x3C, 0xC3, 0x00, 0x01, 0x00, 0x80]) + uid + contador
    entrada = f"{enc_hex}&cmac=".encode() if enc_hex else b""
    completo = _cmac(_cmac(file_key, sv2), entrada)
    esperado = bytes(completo[i] for i in range(1, 16, 2))
    if not hmac.compare_digest(esperado, recibido):
        raise ValueError("firma inválida")
    return uid.hex().upper(), int.from_bytes(contador, "little")
