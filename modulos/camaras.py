import requests
import json
import hashlib
import os
import base64
from datetime import datetime
from configuracion.config import ARCHIVO_CAMARAS  
from modulos.reloj_shoa import obtener_hora_oficial
import modulos.biometria as biometria

# --- UTILIDADES INTERNAS ---
def _crear_sesion():
    return requests.Session()

def _encriptar_md5(texto):
    return hashlib.md5(texto.encode('utf-8')).hexdigest()

def login_camara(ip, puerto, usuario, password):
    """Autentica con la cámara y devuelve True/False. Usado para el Health Check."""
    url = f"http://{ip}:{puerto}/fcgi-bin/dispatch.fcgi"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "cmd": "ar_cmd_login",
        "payload": json.dumps({
            "username": _encriptar_md5(usuario),
            "password": _encriptar_md5(password)
        })
    }
    try:
        r = _crear_sesion().post(url, json=payload, headers=headers, timeout=3)
        if r.status_code == 200:
            data = r.json()
            return data.get('status') == 0 or str(data.get('ret')).upper() == 'OK'
    except:
        pass
    return False

def descargar_logs_asistencia(cam_config):
    """
    Se conecta a la cámara y descarga los registros de HOY.
    Retorna una lista de diccionarios crudos.
    """
    session = _crear_sesion()
    base_url = f"http://{cam_config['ip']}:{cam_config['puerto']}/fcgi-bin"
    headers = {'Content-Type': 'application/json'}
    
    try:
        # 1. Login
        payload_login = {
            "cmd": "ar_cmd_login",
            "payload": json.dumps({
                "username": _encriptar_md5(cam_config['user']),
                "password": _encriptar_md5(cam_config['pass'])
            })
        }
        r = session.post(f"{base_url}/dispatch.fcgi", json=payload_login, headers=headers, timeout=3)
        if r.status_code != 200: return []

        # 2. Descargar Registros
        inicio_dia = datetime.now().strftime("%Y-%m-%d 00:00:00")
        payload_query = {
            "cmd": "ar_cmd_query_attend_record",
            "payload": json.dumps({
                "startTime": inicio_dia,
                "endTime": "2030-12-31 23:59:59",
                "pageIndex": 0,
                "pageSize": 50, # Traemos bloques pequeños para ser ágiles
                "needImg": False,
                "sort": "desc"
            })
        }
        r2 = session.post(f"{base_url}/dispatch1.fcgi", json=payload_query, headers=headers, timeout=5)
        registros = r2.json().get('data', [])
        
        # Invertimos para procesar del más antiguo al más nuevo
        if registros: registros.reverse()
        return registros

    except Exception as e:
        print(f"⚠️ Error conectando con {cam_config.get('nombre', 'Cámara')}: {e}")
        return []

def enviar_usuario_a_camara(cam_config, uid, nombre):
    """
    Sube un usuario a la cámara desencriptando su foto en vuelo.
    Cumple normativa de Privacidad (No storage of plain images).
    """
    session = _crear_sesion()
    headers = {'Content-Type': 'application/json'}
    url_dispatch = f"http://{cam_config['ip']}:{cam_config['puerto']}/fcgi-bin/dispatch1.fcgi"
    
    if not login_camara(cam_config['ip'], cam_config['puerto'], cam_config['user'], cam_config['pass']):
        return "Error: Auth Fallida"

    # Limpieza de RUT
    uid_clean = str(uid).replace(".", "").replace("-", "").strip()
    nombre_safe = str(nombre)[:24]

    # --- BÚSQUEDA DE BIOMETRÍA SEGURA ---
    lista_imagenes = []
    
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    directorio_raiz = os.path.dirname(directorio_actual)
    ruta_fotos_dir = os.path.join(directorio_raiz, "statics", "fotos")

    rut_con_guion = uid_clean
    if len(uid_clean) > 1:
        rut_con_guion = f"{uid_clean[:-1]}-{uid_clean[-1]}"

    # Prioridad: Archivos Encriptados (.bio)
    nombres_posibles = [uid, uid_clean, rut_con_guion]
    
    bytes_imagen = None
    archivo_encontrado = ""

    for n in nombres_posibles:
        ruta_bio = os.path.join(ruta_fotos_dir, f"{n}.bio")
        ruta_jpg = os.path.join(ruta_fotos_dir, f"{n}.jpg") # Soporte legado
        
        if os.path.exists(ruta_bio):
            print(f"🔒 [Privacidad] Usando biometría encriptada: {n}.bio")
            bytes_imagen = biometria.desencriptar_en_memoria(ruta_bio)
            archivo_encontrado = ruta_bio
            break
        elif os.path.exists(ruta_jpg):
            print(f"⚠️ [Aviso] Usando imagen NO encriptada: {n}.jpg (Se recomienda migrar)")
            try:
                with open(ruta_jpg, "rb") as f: bytes_imagen = f.read()
                archivo_encontrado = ruta_jpg
                break
            except: pass

    if bytes_imagen:
        b64 = base64.b64encode(bytes_imagen).decode('utf-8')
        lista_imagenes.append({"pose": "normal", "format": ".jpg", "data": f"data:image/jpeg;base64,{b64}"})
        # Limpiamos RAM
        del bytes_imagen 
    else:
        print(f"⚠️ No se encontró biometría para {nombre}")

    payload = {
        "name": nombre_safe,
        "userId": uid_clean,
        "personId": uid_clean,
        "images": lista_imagenes
    }
    
    # ... (El resto de la función sigue igual: Intento 1 Agregar, Intento 2 Actualizar) ...
    try:
        r = session.post(url_dispatch, json={"cmd": "ar_cmd_add_person", "payload": json.dumps(payload)}, headers=headers, timeout=10)
        resp = r.json()
        if resp.get('status') == 0: return "OK: Subido"
        
        if "duplicate" in str(resp).lower() or "exist" in str(resp).lower():
            session.post(url_dispatch, json={"cmd": "ar_cmd_remove_person", "payload": json.dumps({"personId": uid_clean})}, headers=headers)
            r2 = session.post(url_dispatch, json={"cmd": "ar_cmd_add_person", "payload": json.dumps(payload)}, headers=headers)
            if r2.json().get('status') == 0: return "OK: Actualizado"
            
        return f"Error: {resp.get('detail')}"
    except Exception as e:
        return f"Error Red: {e}"

# --- GESTIÓN DE ESTADO (LISTA DE CÁMARAS) ---
LISTA_CAMARAS = []
# Usamos la ruta absoluta que definimos en config.py
ARCHIVO_CONFIG = ARCHIVO_CAMARAS

def cargar_configuracion():
    global LISTA_CAMARAS
    if os.path.exists(ARCHIVO_CONFIG):
        try:
            with open(ARCHIVO_CONFIG, 'r') as f:
                LISTA_CAMARAS = json.load(f)
            print(f"📂 [Cámaras] {len(LISTA_CAMARAS)} dispositivos cargados.")
        except: LISTA_CAMARAS = []

def guardar_configuracion():
    try:
        with open(ARCHIVO_CONFIG, 'w') as f:
            json.dump(LISTA_CAMARAS, f, indent=4)
    except Exception as e: print(f"⚠️ Error guardando config: {e}")

def guardar_camara(data):
    global LISTA_CAMARAS
    dev_id = data.get('id')
    if dev_id: # Actualizar
        for cam in LISTA_CAMARAS:
            if str(cam['id']) == str(dev_id):
                cam.update(data)
                break
    else: # Crear Nuevo
        nuevo_id = 1
        if LISTA_CAMARAS: 
            # AGREGAMOS int() AQUÍ PARA EVITAR EL ERROR
            nuevo_id = max(int(c['id']) for c in LISTA_CAMARAS) + 1
        
        data['id'] = nuevo_id
        LISTA_CAMARAS.append(data)
    guardar_configuracion()

def eliminar_camara(id_camara):
    global LISTA_CAMARAS
    # FILTRAR: Creamos la nueva lista sin la cámara borrada
    nueva_lista = [c for c in LISTA_CAMARAS if int(c['id']) != int(id_camara)]
    
    # TRUCO DE MEMORIA: Usamos [:] para reemplazar el CONTENIDO de la lista original
    # sin cambiar la referencia de memoria. Así main.py ve el cambio al instante.
    LISTA_CAMARAS[:] = nueva_lista 
    
    guardar_configuracion()
    print(f"🗑️ Cámara ID {id_camara} eliminada de memoria y disco.")

# Cargar al importar el módulo
cargar_configuracion()

def sincronizar_reloj_camara(cam_config):
    """
    Fuerza a la cámara a tener la misma hora que el servidor Python.
    Vital para cumplir la Resolución N° 38 y corregir el error del 'Año 2000'.
    """
    session = _crear_sesion()
    base_url = f"http://{cam_config['ip']}:{cam_config.get('puerto', 80)}/fcgi-bin/dispatch.fcgi"
    headers = {'Content-Type': 'application/json'}
    
    # 1. Obtenemos la hora actual del servidor (formato YYYY-MM-DD HH:MM:SS)
    hora_shoa = obtener_hora_oficial()
    hora_actual = hora_shoa.strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Autenticación previa
        payload_login = {
            "cmd": "ar_cmd_login",
            "payload": json.dumps({
                "username": _encriptar_md5(cam_config['user']),
                "password": _encriptar_md5(cam_config['pass'])
            })
        }
        r_login = session.post(base_url, json=payload_login, headers=headers, timeout=3)
        if r_login.status_code != 200: return False

        # 2. Enviamos el comando de ajuste de hora
        payload_time = {
            "cmd": "ar_cmd_set_time",
            "payload": json.dumps({
                "time": hora_actual
            })
        }
        r_time = session.post(base_url, json=payload_time, headers=headers, timeout=3)
        
        if r_time.json().get('status') == 0:
            print(f"⏰ [Cámara {cam_config.get('nombre')}] Hora sincronizada a: {hora_actual}")
            return True
        else:
            print(f"⚠️ No se pudo sincronizar hora en {cam_config.get('nombre')}")
            
    except Exception as e:
        print(f"⚠️ Error sincronizando reloj: {e}")
    
    return False