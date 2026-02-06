from flask import render_template
from rutas_frontend import web_bp
from configuracion.base_datos import obtener_conexion_oracle
import modulos.turnos as turnos
import modulos.camaras as camaras
from flask import session, render_template, redirect, url_for

@web_bp.route('/')
def dashboard():
    total_empleados = 0
    presentes = 0
    
    # 1. Consultar Oracle para totales
    conn = obtener_conexion_oracle()
    if conn:
        try:
            cur = conn.cursor()
            # Total Plantilla Activa
            cur.execute("SELECT COUNT(DISTINCT RUT) FROM ERPG_VTURNOS_PROGRAMADOS WHERE ISACTIVE='Y' AND FECHA_TERMINO_TURNO >= TRUNC(SYSDATE)")
            res_total = cur.fetchone()
            if res_total: total_empleados = res_total[0]

            # Presentes Hoy (Entrada marcada en Oracle)
            cur.execute("SELECT COUNT(DISTINCT ID_TRABAJADOR) FROM ERPG_PASO_CAMARA WHERE TRUNC(FECHA_DIA) = TRUNC(SYSDATE) AND ENTRADA_AM IS NOT NULL")
            res_presentes = cur.fetchone()
            if res_presentes: presentes = res_presentes[0]
            conn.close()
        except: pass
    
    # Cálculo de Ausentes
    ausentes = total_empleados - presentes
    if ausentes < 0: ausentes = 0

    # Obtener áreas disponibles desde la configuración de cámaras
    # (Asumimos que camaras.LISTA_CAMARAS existe y se carga al inicio)
    lista_areas = sorted(list(set(c.get('area', 'General') for c in camaras.LISTA_CAMARAS if c.get('area'))))

    return render_template('index.html', 
                           presentes=presentes, 
                           ausentes=ausentes, 
                           total=total_empleados, 
                           areas=lista_areas)

@web_bp.route('/devices')
def devices():
    return render_template('devices.html', camaras=camaras.LISTA_CAMARAS)

@web_bp.route('/reports')
def reports_page():
    return render_template('reports.html')

@web_bp.route('/data')
def data_page():
    return render_template('data.html')

# --- CORRECCIÓN EN rutas_frontend/web.py ---

@web_bp.route('/fiscalizador/login', methods=['GET'])
def fiscalizador_login_page():
    if session.get('rol') == 'FISCALIZADOR':
        # CAMBIO AQUÍ: 'web_bp' en lugar de 'web'
        return redirect(url_for('web_bp.fiscalizador_dashboard')) 
    return render_template('login_fiscalizador.html')

@web_bp.route('/fiscalizador', methods=['GET'])
def fiscalizador_dashboard():
    if session.get('rol') != 'FISCALIZADOR':
        # CAMBIO AQUÍ: 'web_bp' en lugar de 'web'
        return redirect(url_for('web_bp.fiscalizador_login_page'))
    
    return render_template('fiscalizador.html')

@web_bp.route('/fiscalizador/logout')
def logout_fiscalizador():
    session.pop('rol', None)
    # CAMBIO AQUÍ: 'web_bp' en lugar de 'web'
    return redirect(url_for('web_bp.fiscalizador_login_page'))