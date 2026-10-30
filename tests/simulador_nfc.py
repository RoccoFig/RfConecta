"""Simulador de cartel NTAG 424 DNA para pruebas: arma los mismos links firmados que escribe un cartel real.
SOLO para tests: vive fuera del paquete `rf`, no se despliega y necesita las claves de prueba (todo ceros).
No reemplaza la prueba con el cartel físico: simula lo que llega por HTTP, no la lectura de radio."""
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from rf import sdm

CLAVE = bytes(16)


def link(contador, uid="04DE5F1EACC040", clave_meta=CLAVE, clave_archivo=CLAVE):
    plano = bytes([0xC7]) + bytes.fromhex(uid) + contador.to_bytes(3, "little") + os.urandom(5)
    e = Cipher(algorithms.AES(clave_meta), modes.CBC(bytes(16))).encryptor()
    picc = e.update(plano) + e.finalize()
    sv = bytes([0x3C, 0xC3, 0, 1, 0, 0x80]) + bytes.fromhex(uid) + contador.to_bytes(3, "little")
    completo = sdm._cmac(sdm._cmac(clave_archivo, sv), b"")
    firma = bytes(completo[i] for i in range(1, 16, 2))
    return f"/t?picc_data={picc.hex().upper()}&cmac={firma.hex().upper()}"
