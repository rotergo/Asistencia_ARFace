# modulos/reloj_shoa.py
import socket
import struct
import time
from datetime import datetime, timedelta

def obtener_hora_oficial():
    """
    Intenta obtener la hora atómica desde el servidor del SHOA (ntp.shoa.cl).
    Si falla, retorna la hora del PC pero avisa que no es oficial.
    """
    servidor_ntp = "ntp.shoa.cl"
    # Fecha base de NTP es 1900, Unix es 1970. Diferencia en segundos:
    NTP_DELTA = 2208988800 

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(2) # Esperar máximo 2 segundos

    data = b'\x1b' + 47 * b'\0' # Paquete de solicitud NTP v3

    try:
        print(f"📡 Consultando hora oficial en {servidor_ntp}...")
        client.sendto(data, (servidor_ntp, 123))
        data, address = client.recvfrom(1024)
        
        if data:
            # Desempaquetar los 64 bits de la marca de tiempo
            unpacked = struct.unpack('!12I', data)
            t = unpacked[10] # Transmit timestamp
            t -= NTP_DELTA
            
            # Hora oficial UTC
            hora_utc = datetime.fromtimestamp(t)
            
            # Ajuste Chile (Aprox UTC-3 o UTC-4 según verano/invierno)
            # Para ser exactos, lo mejor es dejar que el OS maneje la zona horaria
            # o calcularlo manualmente. Por simplicidad, asumimos hora local del sistema
            # pero corregida por el 'tick' del servidor.
            
            print("✅ Hora SHOA obtenida con éxito.")
            return datetime.fromtimestamp(t) # Retorna hora corregida
            
    except Exception as e:
        print(f"⚠️ No se pudo conectar al SHOA ({e}). Usando hora local del PC.")
    finally:
        client.close()

    # Si todo falla, devolvemos la hora del PC
    return datetime.now()