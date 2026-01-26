from flask import Blueprint, jsonify, request, Response, stream_with_context
# --- Importamos el blueprint existente ---
from rutas_frontend import api_bp 
# --- CORRECCIÓN 1: Importamos la NUEVA función de seguridad ---
from modulos.seguridad import generar_hash_fila 
# --------------------------------------------------------------
from configuracion.base_datos import obtener_conexion_oracle
import modulos.camaras as camaras_mod
import modulos.turnos as turnos
from datetime import datetime
import io
import csv
import traceback

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
                workers.append({"id": str(row[0]).strip(), "nombre": str(row[1]).strip(), "activo": str(row[2]).strip()})
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
    resultados = []
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            sql = """
                SELECT ID_TRABAJADOR, NOMBRE_TRABAJADOR, TO_CHAR(FECHA_DIA, 'YYYY-MM-DD'), 
                       DIA_SEMANA, ENTRADA_AM, SALIDA_AM, ENTRADA_PM, SALIDA_PM, ESTADO
                FROM ERPG_PASO_CAMARA
                WHERE FECHA_DIA BETWEEN TO_DATE(:1, 'YYYY-MM-DD') AND TO_DATE(:2, 'YYYY-MM-DD')
                ORDER BY FECHA_DIA DESC, NOMBRE_TRABAJADOR ASC
            """
            cur.execute(sql, [f_inicio, f_fin])
            for row in cur:
                resultados.append({
                    "id": row[0], "nombre": row[1], "fecha": row[2], "dia": row[3],
                    "e_am": row[4] or "-", "s_am": row[5] or "-",
                    "e_pm": row[6] or "-", "s_pm": row[7] or "-", "estado": row[8]
                })
            conn.close()
        except Exception as e: return jsonify({"error": str(e)}), 500
    return jsonify(resultados)

# --- REPORTES: EXPORTACIÓN LEGAL DT ---
@api_bp.route('/reports/export_dt')
def export_dt():
    conn = obtener_conexion_oracle()
    if not conn:
        return jsonify({"error": "Sin conexión a BD"}), 500

    cursor = conn.cursor()
    
    sql = """
        SELECT 
            p.ID_TRABAJADOR AS RUT,
            p.NOMBRE_TRABAJADOR AS NOMBRE,
            TO_CHAR(p.FECHA_DIA, 'DD/MM/YYYY') AS FECHA,
            p.FECHA_DIA AS FECHA_RAW, 
            
            NVL(SUBSTR(t.TURNO_INICIO_DESDE, 12, 5) || ' - ' || SUBSTR(t.TURNO_FINAL_HASTA, 12, 5), 'SIN TURNO') AS JORNADA_PACTADA,
            t.DIA_DEL_TURNO,
            
            NVL(p.ENTRADA_AM, '--:--') || ' - ' || NVL(p.SALIDA_PM, '--:--') AS MARCACIONES,
            NVL(p.SALIDA_AM, '--:--') || ' - ' || NVL(p.ENTRADA_PM, '--:--') AS COLACION,
            
            CASE WHEN p.DIFF_SAL_PM LIKE '%EXTRA%' THEN SUBSTR(p.DIFF_SAL_PM, 7) ELSE '0' END AS HORAS_EXTRAS,
            TRIM(CASE WHEN p.DIFF_ENT_AM LIKE '%ATRASO%' THEN p.DIFF_ENT_AM || ' / ' ELSE '' END || CASE WHEN p.DIFF_SAL_PM LIKE '%TEMPRANA%' THEN p.DIFF_SAL_PM ELSE '' END) AS ANOMALIAS
        
        FROM ERPG_PASO_CAMARA p
        LEFT JOIN ERPG_VTURNOS_PROGRAMADOS t 
            ON TRIM(p.ID_TRABAJADOR) = TRIM(t.RUT)
            AND TRUNC(p.FECHA_DIA) BETWEEN TRUNC(t.FECHA_INICIO_TURNO) AND TRUNC(t.FECHA_TERMINO_TURNO)
            AND (
                UPPER(TRIM(p.DIA_SEMANA)) = UPPER(TRIM(t.DIA_DEL_TURNO)) 
                OR 
                t.DIA_DEL_TURNO IS NULL OR TRIM(t.DIA_DEL_TURNO) = ''
            )
            
        ORDER BY p.FECHA_DIA DESC, p.NOMBRE_TRABAJADOR ASC
    """
    
    try:
        cursor.execute(sql)
        raw_rows = cursor.fetchall()
    except Exception as e:
        print(f"❌ Error SQL Export: {e}")
        return jsonify({"error": f"Error SQL: {str(e)}"}), 500
    finally:
        conn.close()

    processed_map = {} 

    for r in raw_rows:
        rut = r[0]
        fecha_str = r[2] 
        dia_turno_bd = r[5] 
        
        clave_unica = (rut, fecha_str)
        puntaje_actual = 10 if (dia_turno_bd and len(str(dia_turno_bd).strip()) > 0) else 1
        
        if clave_unica in processed_map:
            puntaje_existente = processed_map[clave_unica]['score']
            if puntaje_actual > puntaje_existente:
                processed_map[clave_unica] = {'data': r, 'score': puntaje_actual}
        else:
            processed_map[clave_unica] = {'data': r, 'score': puntaje_actual}

    final_rows = sorted(processed_map.values(), key=lambda x: (x['data'][3], x['data'][1]), reverse=True)

    def generate():
        data = io.StringIO()
        w = csv.writer(data, delimiter=';')
        yield '\ufeff' 
        w.writerow(('RUT', 'Nombre Completo', 'Fecha', 'Jornada Pactada', 'Marcaciones Jornada', 'Colación', 'Horas Extras', 'Anomalías'))
        yield data.getvalue()
        data.seek(0)
        data.truncate(0)

        for item in final_rows:
            row = item['data']
            clean_row = [
                str(row[0]), str(row[1]), str(row[2]), str(row[4]), 
                str(row[6]), str(row[7]), str(row[8]), str(row[9])
            ]
            w.writerow(clean_row)
            yield data.getvalue()
            data.seek(0)
            data.truncate(0)

    filename = f"Reporte_Asistencia_DT_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(stream_with_context(generate()), mimetype='text/csv; charset=utf-8', 
                    headers={"Content-Disposition": f"attachment; filename={filename}"})

# --- RESUMEN MENSUAL ---
@api_bp.route('/reports/summary', methods=['GET'])
def reports_summary():
    mes_filtro = request.args.get('month') 
    resultados = []
    
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            
            # --- CORRECCIÓN 2: Agregamos r antes de las comillas triples para evitar SyntaxWarning con \d ---
            sql = r"""
                SELECT 
                    ID_TRABAJADOR,
                    NOMBRE_TRABAJADOR,
                    TO_CHAR(FECHA_DIA, 'YYYY-MM') AS MES,
                    COUNT(*) AS DIAS_TRABAJADOS,
                    SUM(CASE 
                        WHEN DIFF_ENT_AM LIKE '%ATRASO%' 
                        THEN TO_NUMBER(REGEXP_SUBSTR(DIFF_ENT_AM, '\d+')) 
                        ELSE 0 
                    END) AS TOTAL_ATRASO,
                    SUM(CASE 
                        WHEN DIFF_SAL_PM LIKE '%EXTRA%' 
                        THEN TO_NUMBER(REGEXP_SUBSTR(DIFF_SAL_PM, '\d+')) 
                        ELSE 0 
                    END) AS TOTAL_EXTRA
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


# --- AUDITORÍA DE SEGURIDAD (ACTUALIZADA A FILA COMPLETA) ---
@api_bp.route('/security/audit', methods=['GET'])
def security_audit():
    """
    Versión actualizada: Verifica la integridad de la fila COMPLETA.
    """
    conn = obtener_conexion_oracle()
    if not conn: return jsonify({"status": "error", "message": "Sin DB"}), 500

    reporte_fraude = []
    total_revisados = 0
    total_corruptos = 0

    try:
        cursor = conn.cursor()
        
        # --- CORRECCIÓN 3: SQL para traer TODOS los campos necesarios para el hash nuevo ---
        sql = """
            SELECT * FROM (
                SELECT 
                    ID_SECUENCIA, ID_TRABAJADOR, NOMBRE_TRABAJADOR, TO_CHAR(FECHA_DIA, 'YYYY-MM-DD'), 
                    ENTRADA_AM, SALIDA_AM, ENTRADA_PM, SALIDA_PM, 
                    ESTADO, AREA, HASH_SHA256
                FROM ERPG_PASO_CAMARA 
                WHERE HASH_SHA256 IS NOT NULL 
                ORDER BY ID_SECUENCIA DESC 
            ) WHERE ROWNUM <= 50
        """
        cursor.execute(sql)
        filas = cursor.fetchall()

        for fila in filas:
            total_revisados += 1
            
            # Mapeo exacto de columnas
            id_seq = fila[0]
            rut = fila[1]
            nombre = fila[2]
            fecha = fila[3]
            e_am = fila[4]
            s_am = fila[5]
            e_pm = fila[6]
            s_pm = fila[7]
            estado = fila[8]
            area = fila[9]
            hash_guardado = fila[10]

            # --- CORRECCIÓN 4: Usamos la nueva función generar_hash_fila ---
            hash_calculado = generar_hash_fila(rut, nombre, fecha, e_am, s_am, e_pm, s_pm, estado, area)
            
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