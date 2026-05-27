# Manual de despliegue — Academia Online

Guía completa para instalar y lanzar tu propia academia online usando este proyecto.

---

## Qué incluye

- Plataforma de cursos con vídeos (Vimeo / YouTube)
- Comunidad tipo foro con categorías, likes y comentarios
- Sistema de miembros con aprobación manual
- Panel de administración completo
- Sistema de puntos y ranking
- Calendario de clases en directo
- Envío de emails a alumnos
- Pagos con Stripe (opcional)
- App móvil instalable (PWA)

---

## Requisitos previos

- Cuenta en [GitHub](https://github.com) (gratis)
- Cuenta en [Railway](https://railway.app) (gratis — 5 $ de crédito/mes)
- Correo Gmail para enviar emails (opcional)
- Cuenta Stripe para cobrar cursos (opcional)

---

## Paso 1 — Copiar el repositorio

### Opción A: Hacer fork en GitHub
1. Ve al repositorio original
2. Haz clic en **Fork** (arriba a la derecha)
3. GitHub crea una copia en tu cuenta

### Opción B: Descargar el ZIP
1. Descarga el ZIP del repositorio
2. Crea un repositorio nuevo en tu GitHub
3. Sube todos los archivos al nuevo repositorio

---

## Paso 2 — Desplegar en Railway

1. Ve a [railway.app](https://railway.app) y haz login con GitHub
2. Haz clic en **New Project**
3. Selecciona **Deploy from GitHub repo**
4. Elige tu repositorio de la academia
5. Railway detecta automáticamente el `Procfile` y empieza a desplegar

---

## Paso 3 — Añadir base de datos PostgreSQL

1. En tu proyecto de Railway, haz clic en **+ New**
2. Selecciona **Database → PostgreSQL**
3. Railway conecta automáticamente la base de datos a tu app mediante la variable `DATABASE_URL`

> ⚠️ Sin este paso los datos se perderán cada vez que se reinicie la app.

---

## Paso 4 — Variables de entorno (obligatorias)

En Railway → tu servicio web → **Variables**, añade:

| Variable | Valor | Descripción |
|----------|-------|-------------|
| `SECRET_KEY` | Una cadena larga aleatoria | Seguridad de sesiones. Ej: `mi-clave-secreta-muy-larga-2024` |

> Genera una clave segura en: https://randomkeygen.com (usa "Fort Knox Passwords")

### Variables opcionales (email)

| Variable | Valor |
|----------|-------|
| `MAIL_SERVER` | `smtp.gmail.com` |
| `MAIL_PORT` | `587` |
| `MAIL_USERNAME` | tu_correo@gmail.com |
| `MAIL_PASSWORD` | contraseña de aplicación de Gmail* |

> *Para obtener la contraseña de aplicación de Gmail:
> 1. Ve a tu cuenta Google → Seguridad → Verificación en 2 pasos (actívala)
> 2. Busca "Contraseñas de aplicación"
> 3. Crea una para "Correo" → copia los 16 caracteres que aparecen

### Variables opcionales (pagos Stripe)

| Variable | Valor |
|----------|-------|
| `STRIPE_PUBLIC_KEY` | `pk_live_...` (en tu dashboard de Stripe) |
| `STRIPE_SECRET_KEY` | `sk_live_...` (en tu dashboard de Stripe) |

---

## Paso 5 — Primer acceso

Una vez Railway termine de desplegar (1-2 minutos):

1. Copia la URL de tu app (ej: `miacademia.up.railway.app`)
2. Entra en el navegador
3. La primera cuenta admin se crea automáticamente con:
   - **Email:** `samuelgavilant@gmail.com`
   - **Contraseña:** `Admin1234!`

> ⚠️ **Importante:** Cambia el email y contraseña del admin inmediatamente.

### Cambiar el email del admin

Edita el archivo `app.py`, busca esta línea (aproximadamente línea 1624):

```python
samuel = User.query.filter_by(email='samuelgavilant@gmail.com').first()
```

Cambia el email y la contraseña por los tuyos:

```python
samuel = User.query.filter_by(email='TU_EMAIL@gmail.com').first()
if not samuel:
    samuel = User(username='admin', email='TU_EMAIL@gmail.com',
                  role='admin', status='active')
    samuel.set_password('TU_CONTRASEÑA_SEGURA')
```

Sube el cambio a GitHub y Railway redesplega automáticamente.

---

## Paso 6 — Personalizar la academia

### Cambiar el nombre

En el panel de admin → **Ajustes** → cambia el nombre de la academia.

### Añadir tus cursos

1. Panel admin → **Nuevo curso**
2. Rellena título, descripción y precio (0 = gratis)
3. Añade secciones y lecciones con links de Vimeo/YouTube

### Añadir miembros

Los alumnos se registran solos en `/registro`. Tú los apruebas desde el panel admin → **Usuarios**.

O bien los añades directamente desde admin → **Usuarios** → **Crear usuario**.

---

## Estructura de archivos

```
academia/
├── app.py              ← Toda la lógica del servidor
├── models.py           ← Estructura de la base de datos
├── config.py           ← Configuración (lee variables de entorno)
├── Procfile            ← Instrucciones de arranque para Railway
├── gunicorn.conf.py    ← Configuración del servidor web
├── requirements.txt    ← Librerías Python necesarias
├── templates/          ← Páginas HTML
│   ├── base.html       ← Plantilla base (cabecera, menú, pie)
│   ├── auth/           ← Login y registro
│   ├── courses/        ← Cursos y reproductor de vídeo
│   ├── community/      ← Foro
│   ├── admin/          ← Panel de administración
│   └── errors/         ← Páginas de error
└── static/             ← Imágenes, favicon, scripts
```

---

## Solución de problemas

### "Application failed to respond"
- Comprueba que has añadido PostgreSQL al proyecto en Railway
- Verifica que `SECRET_KEY` está configurada en Variables

### Los datos se pierden al redesplegar
- Asegúrate de tener PostgreSQL añadido (ver Paso 3)
- Comprueba en Railway que `DATABASE_URL` aparece en Variables automáticamente

### El email no llega
- Verifica que `MAIL_USERNAME` y `MAIL_PASSWORD` están correctos
- Usa contraseña de aplicación de Gmail (no la contraseña normal)
- Comprueba la carpeta de spam del destinatario

### Quiero usar mi propio dominio
1. En Railway → Settings → Domains → **Add Custom Domain**
2. Añade el dominio que has comprado
3. Railway te da los registros DNS que tienes que añadir en tu proveedor de dominio

---

## Costes estimados

| Servicio | Coste |
|----------|-------|
| Railway (app + BD) | 0 € (5$/mes de crédito gratis) |
| Dominio propio | ~10 €/año (opcional) |
| Gmail para emails | 0 € |
| Stripe | 0 € + 1.4% por transacción |

Para academias pequeñas (<500 alumnos, <5 GB de datos) el plan gratuito de Railway es más que suficiente.

---

## Actualizar la academia

Cuando el desarrollador publique mejoras:

1. Si hiciste **fork**: en tu repositorio GitHub → "Sync fork" → "Update branch"
2. Railway redesplega automáticamente al actualizar GitHub

---

*Manual generado para el proyecto Academia Online · 2024*
