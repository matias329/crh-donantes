# Cambios y seguridad

Esta copia conserva todos los archivos del proyecto original e incorpora:

- gestión CRUD de campañas con publicación controlada;
- panel estadístico sin dependencias externas de JavaScript;
- constancias PDF con código interno y autorización por sesión;
- enlaces individuales de WhatsApp con filtros por grupo y Rh;
- explicaciones de estados y una guía pública de donación;

- protección CSRF en todos los formularios;
- cierre de sesión mediante POST;
- cookies de sesión endurecidas y vencimiento de sesión;
- encabezados HTTP de seguridad;
- límites básicos de intentos de acceso y recuperación;
- validación de datos en el servidor;
- validación completa de campañas, localidad, fecha, hora y aptitud al reservar;
- enlaces de recuperación de un solo uso;
- ocultamiento del enlace de recuperación fuera de una demo habilitada;
- exigencia de secretos seguros en producción;
- protección contra el borrado accidental al generar datos ficticios;
- respeto de `DB_PATH` por el generador;
- índices para consultas frecuentes;
- configuración de despliegue y documentación actualizadas.

## Antes de producción

Esta versión es un prototipo reforzado, no una certificación de seguridad ni
una validación clínica. Para operar con datos sanitarios reales todavía se
recomienda PostgreSQL, cuentas administrativas individuales, roles, auditoría,
backups cifrados, monitoreo, revisión legal y validación profesional de las
reglas de aptitud.
