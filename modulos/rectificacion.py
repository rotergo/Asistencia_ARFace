# Archivo: modulos/rectificacion.py
from configuracion.base_datos import obtener_conexion_oracle
import modulos.turnos as turnos
from modulos.seguridad import generar_hash_fila
import datetime

def rectificar_asistencia(id_original, rut, nuevas_horas, admin_user, motivo):
    conn = obtener_conexion_oracle()
    if not conn: return {"status": "error", "msg": "Sin conexión DB"}
    
    cursor = conn.cursor()
    
    try:
        try:
            id_orig_int = int(id_original)
        except:
            return {"status": "error", "msg": "ID inválido"}

        # 1. LEER TODOS LOS DATOS ORIGINALES (Necesarios para el Hash de Anulación)
        sql_leer = """
            SELECT 
                ID_TRABAJADOR, NOMBRE_TRABAJADOR, TO_CHAR(FECHA_DIA, 'YYYY-MM-DD'), 
                DIA_SEMANA, AREA,
                ENTRADA_AM, SALIDA_AM, ENTRADA_PM, SALIDA_PM
            FROM ERPG_PASO_CAMARA 
            WHERE ID_SECUENCIA = :1
        """
        cursor.execute(sql_leer, [id_orig_int])
        datos_orig = cursor.fetchone()
        
        if not datos_orig: return {"status": "error", "msg": "Registro no encontrado"}
        
        bd_rut, bd_nombre, bd_fecha_str, bd_dia, bd_area = datos_orig[0], datos_orig[1], datos_orig[2], datos_orig[3], datos_orig[4]
        # Guardamos las horas viejas para recalcular el hash del anulado
        old_e_am, old_s_am, old_e_pm, old_s_pm = datos_orig[5], datos_orig[6], datos_orig[7], datos_orig[8]

        # 2. CALCULAR ATRASOS Y EXTRAS (Para el NUEVO registro)
        calculos = turnos.calcular_detalles_manuales(bd_rut, bd_fecha_str, nuevas_horas)
        
        # Preparar horas nuevas
        new_e_am = nuevas_horas.get('entrada_am') or None
        new_s_am = nuevas_horas.get('salida_am') or None
        new_e_pm = nuevas_horas.get('entrada_pm') or None
        new_s_pm = nuevas_horas.get('salida_pm') or None
        estado_nuevo = 'RECTIFICADO MANUAL'

        # 3. GENERAR LOS DOS HASHES DE SEGURIDAD
        
        # A) Hash para el registro ANULADO (Con datos viejos + estado 'ANULADO_RECT')
        hash_anulacion = generar_hash_fila(
            rut=bd_rut, nombre=bd_nombre, fecha=bd_fecha_str,
            e_am=old_e_am, s_am=old_s_am, e_pm=old_e_pm, s_pm=old_s_pm,
            estado='ANULADO_RECT', # <--- El cambio clave
            area=bd_area
        )

        # B) Hash para el registro NUEVO (Con datos nuevos + estado 'RECTIFICADO MANUAL')
        hash_nuevo = generar_hash_fila(
            rut=bd_rut, nombre=bd_nombre, fecha=bd_fecha_str,
            e_am=new_e_am, s_am=new_s_am, e_pm=new_e_pm, s_pm=new_s_pm,
            estado=estado_nuevo,
            area=bd_area
        )

        # 4. ANULAR ORIGINAL (Actualizando también su Hash)
        sql_anular = """
            UPDATE ERPG_PASO_CAMARA 
            SET ESTADO='ANULADO_RECT', 
                HASH_SHA256=:1,        -- <--- Actualizamos el hash aquí
                MODIFICADO_POR=:2, 
                FECHA_MODIFICACION=SYSDATE, 
                MOTIVO_MODIFICACION=:3 
            WHERE ID_SECUENCIA=:4
        """
        cursor.execute(sql_anular, [hash_anulacion, admin_user, motivo, id_orig_int])
        
        # 5. INSERTAR NUEVO REGISTRO
        sql_insert = """
            INSERT INTO ERPG_PASO_CAMARA (
                ID_SECUENCIA, ID_TRABAJADOR, NOMBRE_TRABAJADOR, FECHA_DIA, DIA_SEMANA,
                ENTRADA_AM, SALIDA_AM, ENTRADA_PM, SALIDA_PM,
                DIFF_ENT_AM, DIFF_SAL_AM, DIFF_ENT_PM, DIFF_SAL_PM,
                ESTADO, AREA, HASH_SHA256,
                TIPO_REGISTRO, MODIFICADO_POR, FECHA_MODIFICACION, MOTIVO_MODIFICACION, ID_REGISTRO_ORIGINAL
            ) VALUES (
                SEQ_ERPG_PASO_CAMARA.NEXTVAL, :rut, :nombre, TO_DATE(:fecha, 'YYYY-MM-DD'), :dia,
                :e_am, :s_am, :e_pm, :s_pm,
                :d_e_am, :d_s_am, :d_e_pm, :d_s_pm,
                :estado, :area, :hash_new,
                'MANUAL', :admin, SYSDATE, :motivo, :id_orig
            )
        """
        
        params = {
            'rut': bd_rut, 'nombre': bd_nombre, 'fecha': bd_fecha_str, 'dia': bd_dia,
            'e_am': new_e_am, 's_am': new_s_am, 'e_pm': new_e_pm, 's_pm': new_s_pm,
            'd_e_am': calculos.get('diff_e_am'),
            'd_s_am': calculos.get('diff_s_am'),
            'd_e_pm': calculos.get('diff_e_pm'),
            'd_s_pm': calculos.get('diff_s_pm'),
            'estado': estado_nuevo, 'area': bd_area, 
            'hash_new': hash_nuevo,
            'admin': admin_user, 'motivo': motivo, 'id_orig': id_orig_int
        }

        cursor.execute(sql_insert, params)
        conn.commit()
        return {"status": "ok", "msg": "Rectificado seguro exitoso."}

    except Exception as e:
        conn.rollback()
        print(f"Error Rectificación: {e}")
        return {"status": "error", "msg": str(e)}
    finally:
        cursor.close()
        conn.close()