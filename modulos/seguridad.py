import hashlib
from configuracion.config import SECRET_KEY_HASH

def generar_hash_asistencia(rut, fecha, hora, tipo, dispositivo):
    """
    Genera una firma única SHA-256 para cada marcaje.
    Cadena = RUT + FECHA + HORA + TIPO + DISPOSITIVO + SALT_SECRETO
    """
    # 1. Normalizamos los datos (Todo a string y mayúsculas para evitar errores)
    cadena_base = f"{str(rut).upper()}|{fecha}|{hora}|{tipo}|{dispositivo}|{SECRET_KEY_HASH}"
    
    # 2. Generamos el Hash
    hash_obj = hashlib.sha256(cadena_base.encode('utf-8'))
    firma_digital = hash_obj.hexdigest()
    
    return firma_digital