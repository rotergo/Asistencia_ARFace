import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from configuracion.config import SECRET_KEY_HASH

# --- CONFIGURACIÓN CRIPTOGRÁFICA ---
def _obtener_llave_maestra():
    """
    Deriva una llave AES-128 segura a partir de tu SECRET_KEY_HASH.
    Esto asegura que solo tu servidor pueda leer las fotos.
    """
    salt = b'SCAF_BIOMETRIA_PROTEGIDA_2026' # Sal estática para consistencia
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    # Convertimos a formato URL-safe base64 para Fernet
    key = base64.urlsafe_b64encode(kdf.derive(SECRET_KEY_HASH.encode()))
    return key

def encriptar_imagen(ruta_jpg):
    """ 
    Lee un JPG, lo encripta en AES y lo guarda como .bio.
    Luego BORRA el JPG original para cumplimiento normativo.
    """
    if not os.path.exists(ruta_jpg): return False
    
    try:
        key = _obtener_llave_maestra()
        fernet = Fernet(key)
        
        with open(ruta_jpg, "rb") as file:
            datos_imagen = file.read()
            
        datos_encriptados = fernet.encrypt(datos_imagen)
        
        ruta_bio = ruta_jpg.replace(".jpg", ".bio")
        with open(ruta_bio, "wb") as file:
            file.write(datos_encriptados)
            
        # ¡BORRADO SEGURO!
        os.remove(ruta_jpg)
        return True
    except Exception as e:
        print(f"⚠️ Error encriptando {ruta_jpg}: {e}")
        return False

def desencriptar_en_memoria(ruta_bio):
    """ 
    Lee un archivo .bio y devuelve los bytes de la imagen original.
    NO crea archivo en disco, todo ocurre en RAM.
    """
    if not os.path.exists(ruta_bio): return None
    
    try:
        key = _obtener_llave_maestra()
        fernet = Fernet(key)
        
        with open(ruta_bio, "rb") as file:
            datos_encriptados = file.read()
            
        return fernet.decrypt(datos_encriptados)
    except Exception as e:
        print(f"⚠️ Error desencriptando {ruta_bio}: {e}")
        return None
    
def ejecutar_migracion_automatica():
    """
    Busca archivos .jpg en la carpeta de fotos y los encripta automáticamente.
    Esta función se debe llamar al iniciar el sistema principal.
    """
    # Calculamos la ruta absoluta a statics/fotos
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta_fotos = os.path.join(base_dir, "statics", "fotos")
    
    if not os.path.exists(ruta_fotos):
        print(f"⚠️ [Biometría] No se encontró la carpeta: {ruta_fotos}")
        return

    pendientes = [f for f in os.listdir(ruta_fotos) if f.lower().endswith(".jpg")]
    
    if pendientes:
        print(f"🔒 [Biometría] Se detectaron {len(pendientes)} fotos nuevas sin proteger. Procesando...")
        count = 0
        for archivo in pendientes:
            ruta_completa = os.path.join(ruta_fotos, archivo)
            if encriptar_imagen(ruta_completa):
                count += 1
                print(f"   ➥ Encriptado: {archivo}")
            else:
                print(f"   ❌ Falló: {archivo}")
        print(f"✅ [Biometría] Migración completada. {count} nuevas imágenes aseguradas.")
    else:
        # Silencioso si no hay nada nuevo, para no ensuciar el log
        pass