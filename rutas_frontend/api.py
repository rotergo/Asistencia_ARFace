from flask import Blueprint, jsonify, request, Response, stream_with_context, session, url_for
from rutas_frontend import api_bp 
from modulos.seguridad import generar_hash_fila 
import secrets 
import string
from datetime import datetime, timedelta
from configuracion.base_datos import obtener_conexion_oracle
from modulos import rectificacion
import modulos.camaras as camaras_mod
from modulos.camaras import distribuir_foto_a_camaras
import modulos.turnos as turnos
import io
import csv
import traceback
import re

# Almacén temporal de tokens en memoria RAM (Email: Token)
TOKENS_FISCALIZACION = {}

# --- DASHBOARD EN VIVO ---
@api_bp.route('/dashboard/live')
def dashboard_live():
    area_filter = request.args.get('area', 'todas')
    lista_completa = list(turnos.BUFFER_VISUAL)
    
    if area_filter != 'todas':
        registros_filtrados = [r for r in lista_completa if r.get('area') == area_filter]
    else:
        registros_filtrados = lista_completa

    presentes = 0
    total_plantilla = 0 
    
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(DISTINCT RUT) FROM ERPG_VTURNOS_PROGRAMADOS WHERE ISACTIVE='Y' AND FECHA_TERMINO_TURNO >= TRUNC(SYSDATE)")
            res = cur.fetchone()
            if res: total_plantilla = res[0]
            conn.close()
        except: pass

    if area_filter == 'todas':
        presentes = len(turnos.CACHE_UBICACION)
    else:
        presentes = sum(1 for a in turnos.CACHE_UBICACION.values() if a == area_filter)

    ausentes = total_plantilla - presentes
    if ausentes < 0: ausentes = 0

    return jsonify({
        "registros": registros_filtrados,
        "stats": {"presentes": presentes, "total": total_plantilla, "ausentes": ausentes}
    })

# --- TRABAJADORES ---
@api_bp.route('/workers/list')
def list_workers():
    workers = []
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            sql = """
            SELECT DISTINCT RUT, NOMBRE, ISACTIVE 
            FROM ERPG_VTURNOS_PROGRAMADOS 
            WHERE FECHA_TERMINO_TURNO >= TRUNC(SYSDATE) - 30 
            ORDER BY NOMBRE
            """
            cur.execute(sql)
            for row in cur:
                workers.append({"rut": str(row[0]).strip(), "nombre": str(row[1]).strip(), "activo": str(row[2]).strip()})
            conn.close()
        except: pass
    return jsonify(workers)

# --- SINCRONIZACIÓN ---
@api_bp.route('/sync/execute', methods=['POST'])
def sync_execute():
    data = request.json
    usuarios = data.get('users', [])
    camaras_ids = data.get('cameras', []) 

    log_resultados = []
    ids_seleccionados = [str(id_cam) for id_cam in camaras_ids]
    camaras_destino = [c for c in camaras_mod.LISTA_CAMARAS if str(c['id']) in ids_seleccionados]

    if not camaras_destino:
        return jsonify({"status": "error", "logs": ["⚠️ Error: No se encontraron cámaras seleccionadas (ID Mismatch)."]})

    if not usuarios:
        return jsonify({"status": "error", "logs": ["⚠️ Error: No hay usuarios seleccionados."]})

    for cam in camaras_destino:
        for user in usuarios:
            resultado = camaras_mod.enviar_usuario_a_camara(cam, user['id'], user['name'])
            estado_visual = "✅" if "OK" in resultado else "❌"
            log_resultados.append(f"{estado_visual} {user['name']} -> {cam.get('nombre')}: {resultado}")

    return jsonify({"status": "ok", "logs": log_resultados})

@api_bp.route('/subir_foto/<rut_trabajador>', methods=['POST'])
def api_subir_foto(rut_trabajador):
    if 'archivo_foto' not in request.files:
        return jsonify({'ok': False, 'error': 'No se envió imagen.'}), 400
    
    file = request.files['archivo_foto']
    nombre_trabajador = request.form.get('nombre', 'Desconocido')
    
    try:
        imagen_bytes = file.read()
        
        # Ahora llamamos a la función que SOLO guarda en disco
        resultado = camaras_mod.guardar_foto_local(rut_trabajador, nombre_trabajador, imagen_bytes, file.filename)
        
        if resultado['ok']:
             mensaje = "Foto guardada. Usa 'Sincronizar Ahora' para enviarla a los equipos."
             return jsonify({'ok': True, 'mensaje': mensaje})
        else:
             return jsonify({'ok': False, 'error': resultado['error']}), 500

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# --- GESTIÓN DE DISPOSITIVOS ---
@api_bp.route('/devices/list_simple')
def list_devices_simple():
    return jsonify(camaras_mod.LISTA_CAMARAS)

@api_bp.route('/devices/save', methods=['POST'])
def save_device():
    data = request.json
    if 'port' in data: data['puerto'] = data['port']
    camaras_mod.guardar_camara(data)
    return jsonify({"status": "ok"})

@api_bp.route('/devices/delete/<int:id>', methods=['DELETE'])
def delete_device(id):
    camaras_mod.eliminar_camara(id)
    return jsonify({"status": "ok"})

@api_bp.route('/devices/test', methods=['POST'])
def test_device():
    data = request.json
    puerto = data.get('puerto', data.get('port', 80))
    exito = camaras_mod.login_camara(data['ip'], puerto, data['user'], data['pass'])
    if exito: return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Fallo autenticación"})

# --- REPORTES: BÚSQUEDA ---
@api_bp.route('/reports/search', methods=['POST'])
def reports_search():
    data = request.json
    f_inicio = data.get('start')
    f_fin = data.get('end')
    worker_id = data.get('worker_id')

    resultados = []
    conn = obtener_conexion_oracle()
    
    if conn:
        try:
            cur = conn.cursor()
            sql = """
                SELECT ID_SECUENCIA, ID_TRABAJADOR, NOMBRE_TRABAJADOR, TO_CHAR(FECHA_DIA, 'YYYY-MM-DD'), 
                       DIA_SEMANA, HORA_MARCA, AREA
                FROM ERPG_PASO_CAMARA
                WHERE FECHA_DIA BETWEEN TO_DATE(:1, 'YYYY-MM-DD') AND TO_DATE(:2, 'YYYY-MM-DD')
            """
            params = [f_inicio, f_fin]

            if worker_id and worker_id != 'all':
                sql += " AND ID_TRABAJADOR = :3"
                params.append(worker_id)

            sql += " ORDER BY FECHA_DIA DESC, HORA_MARCA DESC, NOMBRE_TRABAJADOR ASC"
            
            cur.execute(sql, params)
            
            for row in cur:
                # AHORA SÍ MANDAMOS LOS NOMBRES QUE ESPERA EL HTML NUEVO
                resultados.append({
                    "id_secuencia": row[0],
                    "rut": row[1], 
                    "nombre": row[2], 
                    "fecha": row[3], 
                    "dia": row[4],
                    "hora_marca": row[5] or "--:--:--", 
                    "area": row[6] or "General"
                })
            conn.close()
        except Exception as e: 
            print(f"Error Report Search: {e}")
            return jsonify({"error": str(e)}), 500
    return jsonify(resultados)

# --- REPORTES: EXPORTACIÓN LEGAL DT ---
@api_bp.route('/reports/export_dt', methods=['GET'])
def export_dt():
    """
    Reporte Legal Art. 27 - ADAPTADO
    """
    fecha_ini = request.args.get('start', datetime.now().strftime('%Y-%m-01'))
    fecha_fin = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    
    def generate():
        conn = obtener_conexion_oracle()
        if not conn: return
        cursor = conn.cursor()
        
        # SOLUCIÓN: Quitamos las columnas muertas y las sumas raras
        sql = """
            SELECT 
                C.ID_TRABAJADOR,
                C.NOMBRE_TRABAJADOR,
                C.FECHA_DIA,
                NVL(T.TURNO_INICIO_DESDE, '') AS RAW_INICIO,
                NVL(T.TURNO_FINAL_HASTA, '') AS RAW_TERMINO,
                C.HORA_MARCA,
                C.AREA
            FROM ERPG_PASO_CAMARA C
            LEFT JOIN ERPG_VTURNOS_PROGRAMADOS T 
                ON UPPER(REPLACE(REPLACE(C.ID_TRABAJADOR, '.', ''), '-', '')) 
                   = UPPER(REPLACE(REPLACE(T.RUT, '.', ''), '-', ''))
                AND TRUNC(C.FECHA_DIA) BETWEEN TRUNC(T.FECHA_INICIO_TURNO) AND TRUNC(T.FECHA_TERMINO_TURNO)
                AND T.ISACTIVE = 'Y'
            WHERE TRUNC(C.FECHA_DIA) BETWEEN TO_DATE(:1, 'YYYY-MM-DD') AND TO_DATE(:2, 'YYYY-MM-DD')
            ORDER BY C.NOMBRE_TRABAJADOR, C.FECHA_DIA ASC, C.HORA_MARCA ASC
        """
        
        try:
            cursor.execute(sql, [fecha_ini, fecha_fin])
            data = io.StringIO()
            w = csv.writer(data, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            
            yield '\ufeff'
            w.writerow(['RAZÓN SOCIAL:', 'GEMINIS']) 
            w.writerow(['RUT EMPRESA:', '76.XXX.XXX-X'])
            w.writerow([]) 
            
            # Encabezados simplificados para el modelo transaccional
            headers = [
                'RUT', 'Nombre', 'Fecha', 'Jornada pactada', 'Hora de Marca', 'Lugar/Área'
            ]
            w.writerow(headers)
            yield data.getvalue(); data.seek(0); data.truncate(0)

            rows = cursor.fetchall()
            
            for row in rows:
                rut, nombre, fecha_dt, raw_ini, raw_fin, hora_marca, area = row
                
                fecha_str = fecha_dt.strftime('%d/%m/%Y') if fecha_dt else ""
                
                h_ini = raw_ini.split(' ')[-1] if raw_ini else "00:00:00"
                h_fin = raw_fin.split(' ')[-1] if raw_fin else "00:00:00"
                rango_pactado = f"{h_ini} - {h_fin}"

                w.writerow([
                    rut, nombre, fecha_str, rango_pactado, hora_marca, area
                ])
                yield data.getvalue(); data.seek(0); data.truncate(0)

        except Exception as e:
            yield f"ERROR: {str(e)}"
        finally:
            conn.close()

    response = Response(generate(), mimetype='text/csv')
    response.headers.set("Content-Disposition", "attachment", filename=f"Asistencia_Legal_{fecha_ini}.csv")
    return response

# --- OFFBOARDING AUTOMÁTICO (LIMPIEZA DE EX-EMPLEADOS) ---
@api_bp.route('/sync/offboarding', methods=['POST'])
def sync_offboarding():
    conn = obtener_conexion_oracle()
    if not conn:
        return jsonify({"status": "error", "message": "Sin conexión a Oracle"}), 500

    log_eliminados = []
    
    try:
        cursor = conn.cursor()
        sql = """
            SELECT DISTINCT RUT, NOMBRE 
            FROM ERPG_VTURNOS_PROGRAMADOS 
            WHERE ISACTIVE = 'N'
            AND RUT NOT IN (
                SELECT RUT FROM ERPG_VTURNOS_PROGRAMADOS WHERE ISACTIVE = 'Y'
            )
        """
        cursor.execute(sql)
        inactivos = cursor.fetchall()
        
        if not inactivos:
            return jsonify({"status": "ok", "message": "No hay usuarios para dar de baja."})

        camaras_activas = [c for c in camaras_mod.LISTA_CAMARAS if c.get('ip')]
        
        for row in inactivos:
            rut = row[0]
            nombre = row[1]
            borrado_exitoso = False
            
            for cam in camaras_activas:
                if camaras_mod.eliminar_usuario_de_camara(cam, rut):
                    borrado_exitoso = True
            
            if borrado_exitoso:
                log_eliminados.append(f"🚫 Acceso revocado: {nombre} ({rut})")

        return jsonify({
            "status": "ok", 
            "eliminados": len(log_eliminados),
            "detalle": log_eliminados
        })

    except Exception as e:
        print(f"❌ Error Offboarding: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

# --- RUTA PARA RECTIFICACIÓN (RES. 38) ---
@api_bp.route('/reports/rectify', methods=['POST'])
def rectify_attendance():
    try:
        data = request.json
        
        id_original = data.get('id_original')
        rut = data.get('rut')
        nuevas_horas = data.get('nuevas_horas') 
        admin = data.get('admin_user')
        motivo = data.get('motivo')
        
        if not id_original or not admin or not motivo:
             return jsonify({'status': 'error', 'msg': 'Faltan datos obligatorios (ID, Admin o Motivo)'}), 400

        resultado = rectificacion.rectificar_asistencia(id_original, rut, nuevas_horas, admin, motivo)
        
        return jsonify(resultado)

    except Exception as e:
        print(f"Error en API rectify: {e}")
        return jsonify({'status': 'error', 'msg': 'Error interno del servidor'}), 500

# --- RESUMEN MENSUAL ---
@api_bp.route('/reports/summary', methods=['GET'])
def reports_summary():
    mes_filtro = request.args.get('month') 
    resultados = []
    
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            
            # SOLUCIÓN: Eliminado cálculo de DIFF_ENT_AM que ya no existe
            sql = r"""
                SELECT 
                    ID_TRABAJADOR,
                    NOMBRE_TRABAJADOR,
                    TO_CHAR(FECHA_DIA, 'YYYY-MM') AS MES,
                    COUNT(DISTINCT FECHA_DIA) AS DIAS_TRABAJADOS,
                    0 AS TOTAL_ATRASO,
                    0 AS TOTAL_EXTRA
                FROM ERPG_PASO_CAMARA
            """
            
            params = []
            if mes_filtro:
                sql += " WHERE TO_CHAR(FECHA_DIA, 'YYYY-MM') = :1"
                params.append(mes_filtro)
            
            sql += " GROUP BY ID_TRABAJADOR, NOMBRE_TRABAJADOR, TO_CHAR(FECHA_DIA, 'YYYY-MM')"
            sql += " ORDER BY NOMBRE_TRABAJADOR ASC"
            
            cur.execute(sql, params)
            for row in cur:
                resultados.append({
                    "rut": row[0], "nombre": row[1], "mes": row[2],
                    "dias": row[3], "atraso_min": row[4], "extra_min": row[5]
                })
            conn.close()
            
        except Exception as e: 
            print(f"⚠️ Error SQL Resumen: {e}")
            return jsonify({"error": str(e)}), 500
            
    return jsonify(resultados)


# --- AUDITORÍA DE SEGURIDAD ---
@api_bp.route('/security/audit', methods=['GET'])
def security_audit():
    conn = obtener_conexion_oracle()
    if not conn: return jsonify({"status": "error", "message": "Sin DB"}), 500

    reporte_fraude = []
    total_revisados = 0
    total_corruptos = 0

    try:
        cursor = conn.cursor()
        
        # SOLUCIÓN: Buscar columnas nuevas
        sql = """
            SELECT * FROM (
                SELECT 
                    ID_SECUENCIA, ID_TRABAJADOR, NOMBRE_TRABAJADOR, TO_CHAR(FECHA_DIA, 'YYYY-MM-DD'), 
                    HORA_MARCA, AREA, HASH_SHA256
                FROM ERPG_PASO_CAMARA 
                WHERE HASH_SHA256 IS NOT NULL 
                ORDER BY ID_SECUENCIA DESC 
            ) WHERE ROWNUM <= 50
        """
        cursor.execute(sql)
        filas = cursor.fetchall()

        for fila in filas:
            total_revisados += 1
            
            id_seq = fila[0]
            rut = fila[1]
            nombre = fila[2]
            fecha = fila[3]
            hora_marca = fila[4]
            area = fila[5]
            hash_guardado = fila[6]

            # Rellenamos el generador de hash con los mismos dummy que usamos al insertar
            hash_calculado = generar_hash_fila(rut, nombre, fecha, hora_marca, "LOG", "-", "-", "OK", area)
            
            if str(hash_guardado).strip() != str(hash_calculado).strip():
                total_corruptos += 1
                reporte_fraude.append({
                    "id": id_seq,
                    "trabajador": rut,
                    "fecha": fecha,
                    "tipo": "DATA_CORRUPTA",
                    "detalle": "La fila ha sido manipulada. El contenido no coincide con la firma."
                })

        estado_global = "OK" if total_corruptos == 0 else "PELIGRO"
        
        return jsonify({
            "status": estado_global,
            "revisados": total_revisados,
            "corruptos": total_corruptos,
            "detalles": reporte_fraude
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

# --- HEALTH CHECK ---
@api_bp.route('/status/health')
def status_health():
    oracle_ok = False
    try:
        conn = obtener_conexion_oracle()
        if conn: 
            oracle_ok = True
            conn.close()
    except: pass
        
    active_cams = 0
    total_cams = len(camaras_mod.LISTA_CAMARAS)
    for cam in camaras_mod.LISTA_CAMARAS:
        puerto = cam.get('puerto', cam.get('port', 80))
        if camaras_mod.login_camara(cam['ip'], puerto, cam['user'], cam['pass']):
            active_cams += 1

    return jsonify({
        "oracle": oracle_ok, 
        "cameras": (active_cams > 0) if total_cams > 0 else False,
        "active_count": active_cams,
        "total_cams": total_cams 
    })


@api_bp.route('/auth/request_token', methods=['POST'])
def request_dt_token():
    email = request.form.get('email', '').strip().lower()
    
    if not email.endswith('@dt.gob.cl') and "test" not in email:
        return jsonify({"status": "error", "message": "Dominio no autorizado. Use correo institucional."}), 403

    token = ''.join(secrets.choice(string.digits) for i in range(6))
    TOKENS_FISCALIZACION[email] = token
    
    print("\n" + "="*50)
    print(f"[SIMULACRO EMAIL] Para: {email}")
    print(f"SU CLAVE DE ACCESO TEMPORAL ES: {token}")
    print("="*50 + "\n")
    
    return jsonify({"status": "ok", "message": "Clave enviada a su correo institucional."})

@api_bp.route('/auth/login_fiscalizador', methods=['POST'])
def login_dt_token():
    email = request.form.get('email', '').strip().lower()
    token_ingresado = request.form.get('token', '').strip()
    
    token_real = TOKENS_FISCALIZACION.get(email)
    
    if token_real and token_real == token_ingresado:
        session['rol'] = 'FISCALIZADOR'
        session['usuario'] = email
        del TOKENS_FISCALIZACION[email]
        return jsonify({"status": "ok", "redirect": url_for('web_bp.fiscalizador_dashboard')})
    else:
        return jsonify({"status": "error", "message": "Clave incorrecta o expirada."}), 401