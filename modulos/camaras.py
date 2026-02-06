import requests
import json
import hashlib
import os
import base64
from datetime import datetime
from configuracion.config import ARCHIVO_CAMARAS  
from modulos.reloj_shoa import obtener_hora_oficial
import modulos.biometria as biometria
from modulos.validaciones import validar_rut
from requests.auth import HTTPDigestAuth

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
    Sube un usuario a la cámara, PERO PRIMERO LO VALIDA MATEMÁTICAMENTE.
    """
    # 1. VALIDACIÓN MATEMÁTICA DEL RUT (MÓDULO 11) 🛡️
    es_valido, resultado = validar_rut(uid)
    
    if not es_valido:
        print(f"⛔ [RUT Inválido] Se omitió subir a {nombre} ({uid}): {resultado}")
        return f"Error: RUT Inválido ({resultado})"
    
    # Si es válido, usamos el RUT limpio y formateado que nos devolvió la función
    # (La función devuelve ej: '12345678-K', la cámara suele necesitar '12345678K' o sin guión)
    # Tu sistema usa sin guión para el ID de la cámara, así que lo limpiamos de nuevo:
    uid_clean = resultado.replace("-", "") 

    # --- INICIO LÓGICA ORIGINAL ---
    session = _crear_sesion()
    headers = {'Content-Type': 'application/json'}
    url_dispatch = f"http://{cam_config['ip']}:{cam_config.get('puerto', 80)}/fcgi-bin/dispatch1.fcgi"
    
    if not login_camara(cam_config['ip'], cam_config.get('puerto', 80), cam_config['user'], cam_config['pass']):
        return "Error: Auth Fallida"

    nombre_safe = str(nombre)[:24]

    # --- BÚSQUEDA DE BIOMETRÍA SEGURA ---
    lista_imagenes = []
    
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    directorio_raiz = os.path.dirname(directorio_actual)
    ruta_fotos_dir = os.path.join(directorio_raiz, "statics", "fotos")

    rut_con_guion = f"{uid_clean[:-1]}-{uid_clean[-1]}"

    # Prioridad: Archivos Encriptados (.bio)
    nombres_posibles = [uid, uid_clean, rut_con_guion]
    
    bytes_imagen = None

    for n in nombres_posibles:
        ruta_bio = os.path.join(ruta_fotos_dir, f"{n}.bio")
        ruta_jpg = os.path.join(ruta_fotos_dir, f"{n}.jpg") 
        
        if os.path.exists(ruta_bio):
            print(f"🔒 [Privacidad] Usando biometría encriptada: {n}.bio")
            bytes_imagen = biometria.desencriptar_en_memoria(ruta_bio)
            break
        elif os.path.exists(ruta_jpg):
            print(f"⚠️ [Aviso] Usando imagen NO encriptada: {n}.jpg")
            try:
                with open(ruta_jpg, "rb") as f: bytes_imagen = f.read()
                break
            except: pass

    if bytes_imagen:
        b64 = base64.b64encode(bytes_imagen).decode('utf-8')
        lista_imagenes.append({"pose": "normal", "format": ".jpg", "data": f"data:image/jpeg;base64,{b64}"})
        del bytes_imagen 
    else:
        print(f"⚠️ No se encontró biometría para {nombre}")

    payload = {
        "name": nombre_safe,
        "userId": uid_clean,     # RUT validado
        "personId": uid_clean,   # RUT validado
        "images": lista_imagenes
    }
    
    try:
        r = session.post(url_dispatch, json={"cmd": "ar_cmd_add_person", "payload": json.dumps(payload)}, headers=headers, timeout=10)
        resp = r.json()
        if resp.get('status') == 0: return "OK: Subido"
        
        if "duplicate" in str(resp).lower() or "exist" in str(resp).lower():
            # Si ya existe, intentamos actualizar
            session.post(url_dispatch, json={"cmd": "ar_cmd_remove_person", "payload": json.dumps({"personId": uid_clean})}, headers=headers)
            r2 = session.post(url_dispatch, json={"cmd": "ar_cmd_add_person", "payload": json.dumps(payload)}, headers=headers)
            if r2.json().get('status') == 0: return "OK: Actualizado"
            
        return f"Error Cámara: {resp.get('detail', 'Desconocido')}"
    except Exception as e:
        return f"Error Red: {e}"

# --- NUEVA FUNCIÓN DE OFFBOARDING ---
def eliminar_usuario_de_camara(cam_config, uid):
    """
    Elimina físicamente a un usuario de la cámara usando su RUT (personId).
    """
    session = _crear_sesion()
    url_dispatch = f"http://{cam_config['ip']}:{cam_config.get('puerto', 80)}/fcgi-bin/dispatch1.fcgi"
    headers = {'Content-Type': 'application/json'}
    
    if not login_camara(cam_config['ip'], cam_config.get('puerto', 80), cam_config['user'], cam_config['pass']):
        return False

    uid_clean = str(uid).replace(".", "").replace("-", "").strip()
    
    payload = {
        "cmd": "ar_cmd_remove_person",
        "payload": json.dumps({"personId": uid_clean})
    }
    
    try:
        r = session.post(url_dispatch, json=payload, headers=headers, timeout=5)
        resp = r.json()
        if resp.get('status') == 0: return True
        else:
            if "not found" in str(resp).lower(): return True
            return False
    except Exception as e:
        print(f"⚠️ Error borrando {uid} en {cam_config.get('nombre')}: {e}")
        return False

# --- GESTIÓN DE ESTADO (LISTA DE CÁMARAS) ---
LISTA_CAMARAS = []
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
            nuevo_id = max(int(c['id']) for c in LISTA_CAMARAS) + 1
        data['id'] = nuevo_id
        LISTA_CAMARAS.append(data)
    guardar_configuracion()

def eliminar_camara(id_camara):
    global LISTA_CAMARAS
    nueva_lista = [c for c in LISTA_CAMARAS if int(c['id']) != int(id_camara)]
    LISTA_CAMARAS[:] = nueva_lista 
    guardar_configuracion()
    print(f"🗑️ Cámara ID {id_camara} eliminada de memoria y disco.")

# Cargar al importar el módulo
cargar_configuracion()

def sincronizar_reloj_camara(cam_config):
    """
    Configura la cámara para que se sincronice AUTOMÁTICAMENTE con el SHOA
    usando el comando nativo descubierto: 'ar_cmd_set_ntpparam'.
    """
    session = _crear_sesion()
    base_url = f"http://{cam_config['ip']}:{cam_config.get('puerto', 80)}/fcgi-bin/dispatch.fcgi"
    headers = {'Content-Type': 'application/json'}
    
    print(f"⏳ [Reloj] Configurando NTP SHOA en {cam_config.get('nombre')}...")

    # 1. LOGIN
    try:
        payload_login = {
            "cmd": "ar_cmd_login",
            "payload": json.dumps({
                "username": _encriptar_md5(cam_config['user']),
                "password": _encriptar_md5(cam_config['pass'])
            })
        }
        r = session.post(base_url, json=payload_login, headers=headers, timeout=4)
        if r.status_code != 200: 
            print("   ⚠️ Error HTTP en Login")
            return False
    except Exception as e:
        print(f"   ⚠️ Error conexión: {e}")
        return False

    # 2. ENVIAR COMANDO DE NTP (Payload exacto capturado del navegador)
    try:
        # Construimos el objeto interno NtpParam
        # Nota: 'update_cycle' está en segundos. Ponemos 3600 (1 hora) para mayor precisión.
        # 'time_zone': "CST+3:00:00" corresponde a GMT-3 (Chile Continental/Verano aprox)
        ntp_params = {
            "NtpParam": {
                "enabled": "true",
                "server_addr": "ntp.shoa.cl",
                "server_port": 123,
                "update_cycle": 3600, 
                "time_zone": "CST+3:00:00" 
            }
        }

        payload_ntp = {
            "cmd": "ar_cmd_set_ntpparam",
            "payload": json.dumps(ntp_params)
        }

        r = session.post(base_url, json=payload_ntp, headers=headers, timeout=5)
        resp = r.json()

        # Verificamos la respuesta
        if resp.get('status') == 0 or str(resp.get('ret')).upper() == 'OK':
            print(f"✅ [Reloj] ÉXITO. Cámara sincronizada con ntp.shoa.cl (GMT-3).")
            return True
        else:
            print(f"   ⚠️ La cámara rechazó el comando: {resp}")

    except Exception as e:
        print(f"   ⚠️ Error enviando configuración NTP: {e}")

    return False