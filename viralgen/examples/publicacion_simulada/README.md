# Paquete de demostración del módulo 5

Todo lo que hay aquí es **ficticio y no publicable**.

| Archivo | Qué es |
| --- | --- |
| `accounts.json` | Catálogo de cuentas de demostración: alias → identificador. Los identificadores **no corresponden a ninguna cuenta real**. Un ID de canal o de usuario no es un secreto (no autoriza nada por sí solo), pero sin él no se autoriza ningún envío real. |
| `completar_plan_demo.py` | Hace, para la demostración, lo que haría una persona editando el borrador: escribir el texto de YouTube que el guion de ejemplo no trae y decidir la audiencia. Existe para que la demostración sea reproducible sin abrir un editor, **no** para tomar la decisión en lugar de nadie. |

El vídeo que usa la demostración es
[`../montaje_preview/preview.mp4`](../montaje_preview/), un **preview simulado
con marca incrustada**. Por eso el mismo paquete en modo real se rechaza antes de
cualquier llamada, que es el último paso de `tools/demo_publicacion.sh`.

No hay credenciales en este directorio, y no hacen falta: la demostración
completa funciona sin ellas.
