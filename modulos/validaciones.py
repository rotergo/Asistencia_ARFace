import re

def validar_rut(rut_raw):
    """
    Recibe un RUT (con o sin puntos/guión) y verifica si es matemáticamente válido
    usando el algoritmo de Módulo 11 (Chile).
    Retorna: (True, rut_limpio) si es válido.
             (False, msg_error) si es inválido.
    """
    if not rut_raw:
        return False, "RUT vacío"

    # 1. Limpieza básica
    rut_limpio = str(rut_raw).upper().replace(".", "").replace("-", "").strip()
    
    # Validaciones de formato mínimo
    if len(rut_limpio) < 2:
        return False, "RUT muy corto"
    
    # Separar cuerpo y dígito verificador
    cuerpo = rut_limpio[:-1]
    dv_ingresado = rut_limpio[-1]

    # Validar que el cuerpo sean solo números
    if not cuerpo.isdigit():
        return False, "Cuerpo no numérico"

    # 2. ALGORITMO MÓDULO 11
    # Invertimos el cuerpo para multiplicar de derecha a izquierda
    cuerpo_invertido = cuerpo[::-1]
    multiplicador = 2
    suma = 0

    for digito in cuerpo_invertido:
        suma += int(digito) * multiplicador
        multiplicador += 1
        if multiplicador > 7:
            multiplicador = 2

    resto = suma % 11
    resultado = 11 - resto

    # Convertir resultado numérico a carácter ('K', '0' o número)
    if resultado == 11:
        dv_calculado = '0'
    elif resultado == 10:
        dv_calculado = 'K'
    else:
        dv_calculado = str(resultado)

    # 3. Comparación Final
    if dv_ingresado == dv_calculado:
        # Devolvemos el RUT formateado estándar: 12345678-K
        rut_formateado = f"{cuerpo}-{dv_ingresado}"
        return True, rut_formateado
    else:
        return False, f"Dígito inválido (Esperado: {dv_calculado}, Recibido: {dv_ingresado})"