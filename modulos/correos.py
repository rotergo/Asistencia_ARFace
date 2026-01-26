import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from configuracion.config import EMAIL_CFG

def enviar_comprobante(nombre, email_destinatario, fecha, hora, tipo, dispositivo):
    """
    Envía un correo al trabajador confirmando su marca.
    Recibe el email dinámico desde la base de datos.
    """
    if not email_destinatario or "@" not in email_destinatario:
        print(f"⚠️ [Email] Omitido: Trabajador {nombre} no tiene correo válido.")
        return False

    asunto = f"Comprobante de Marca - {tipo} - {fecha}"
    
    # Diseño HTML simple del correo
    cuerpo_html = f"""
    <html>
    <body>
        <div style="border: 1px solid #ccc; padding: 20px; font-family: Arial, sans-serif;">
            <h2 style="color: #4e73df;">Comprobante de Asistencia</h2>
            <p>Hola <strong>{nombre}</strong>,</p>
            <p>Se ha registrado exitosamente tu marca en el sistema.</p>
            <table style="width: 100%; border-collapse: collapse;">
                <tr style="background-color: #f8f9fa;">
                    <td style="padding: 8px;">Fecha:</td>
                    <td style="padding: 8px;"><strong>{fecha}</strong></td>
                </tr>
                <tr>
                    <td style="padding: 8px;">Hora:</td>
                    <td style="padding: 8px;"><strong>{hora}</strong></td>
                </tr>
                <tr style="background-color: #f8f9fa;">
                    <td style="padding: 8px;">Tipo:</td>
                    <td style="padding: 8px;"><span style="color: green;">{tipo}</span></td>
                </tr>
                <tr>
                    <td style="padding: 8px;">Dispositivo:</td>
                    <td style="padding: 8px;">{dispositivo}</td>
                </tr>
            </table>
            <p style="font-size: 10px; color: #999; margin-top: 20px;">
                Este es un correo automático generado por el sistema SCAF según la Normativa de la DT.
            </p>
        </div>
    </body>
    </html>
    """

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CFG['sender']
        msg['To'] = email_destinatario  # <--- AQUÍ ESTABA EL ERROR, AHORA ES DINÁMICO
        msg['Subject'] = asunto
        msg.attach(MIMEText(cuerpo_html, 'html'))

        # Conexión al servidor SMTP
        server = smtplib.SMTP(EMAIL_CFG['smtp_server'], EMAIL_CFG['smtp_port'])
        server.starttls()
        server.login(EMAIL_CFG['sender'], EMAIL_CFG['password'])
        text = msg.as_string()
        server.sendmail(EMAIL_CFG['sender'], email_destinatario, text) # <--- ENVÍO DINÁMICO
        server.quit()
        
        print(f"📧 [Email] Comprobante enviado a {nombre} ({email_destinatario})")
        return True
        
    except Exception as e:
        print(f"⚠️ [Email Error] No se pudo enviar correo a {nombre}: {e}")
        return False