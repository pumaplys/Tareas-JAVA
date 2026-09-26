#!/usr/bin/env bash
# Demostracion completa del modulo 5, entera en modo SIMULADO.
#
# No necesita credenciales, no toca la red y no publica nada: el publicador
# simulado no tiene cliente HTTP. El reloj se fija con --now para que la
# demostracion no dependa de la fecha en que se ejecute; ese reloj de ensayo
# se rechaza si hay algun destino real vencido.
#
# Uso: tools/demo_publicacion.sh [DIRECTORIO_DE_DATOS]
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATOS="${1:-$(mktemp -d)}"
PY="${PYTHON:-python3}"
EJEMPLOS="$RAIZ/examples"
PLAN="$DATOS/simulation/publicaciones/demo-001/publication_plan.json"
mkdir -p "$DATOS"

paso() { printf '\n=== %s ===\n' "$1" >&2; }
vg() { "$PY" -m viralgen --data-dir "$DATOS" "$@"; }

paso "1. Plan en modo simulado (sin red, sin credenciales)"
vg publish plan \
  --script "$EJEMPLOS/visuales_simulados/script.json" \
  --voice "$EJEMPLOS/visuales_simulados/voz/voice.json" \
  --media "$EJEMPLOS/visuales_simulados/medios/media.json" \
  --render "$EJEMPLOS/montaje_preview/render.json" \
  --accounts "$EJEMPLOS/publicacion_simulada/accounts.json" \
  --mode mock --publish-key demo-001 \
  --destination "id=yt,platform=youtube_shorts,account=canal_demo,at=2026-09-24T18:30:00,tz=Europe/Madrid,visibility=private,notify=false" \
  --destination "id=ig,platform=instagram_reels,account=reels_demo,at=2026-09-24T18:45:00,tz=Europe/Madrid,visibility=public,share=true" \
  --destination "id=tk,platform=tiktok,account=tiktok_demo,at=2026-09-24T19:00:00,tz=Europe/Madrid,visibility=public" \
  > "$DATOS/01_plan.json" || true

paso "2. El operador completa lo que el guion no traia"
"$PY" "$EJEMPLOS/publicacion_simulada/completar_plan_demo.py" "$PLAN"

paso "3. Autorizacion de esa revision"
vg publish approve --plan "$PLAN" --operator operador_demo > "$DATOS/02_approve.json"

paso "4. Cola con el horario autorizado"
vg publish enqueue --plan "$PLAN" > "$DATOS/03_enqueue.json"

paso "5. Trabajador: 18:35 local, solo YouTube esta en ventana"
vg publish worker --once --publish-key demo-001 --now 2026-09-24T16:35:00Z \
  > "$DATOS/04_worker.json" || true

paso "6. Trabajador: 18:46, YouTube se verifica e Instagram arranca"
vg publish worker --once --publish-key demo-001 --now 2026-09-24T16:46:00Z \
  > "$DATOS/05_worker.json" || true

paso "7. Trabajador: 18:50, Instagram se verifica"
vg publish worker --once --publish-key demo-001 --now 2026-09-24T16:52:00Z \
  > "$DATOS/06_worker.json" || true

paso "8. Repetir no vuelve a enviar nada"
vg publish worker --once --publish-key demo-001 --now 2026-09-24T17:10:00Z \
  > "$DATOS/07_worker_repetido.json" || true

paso "9. Exportacion manual de TikTok"
vg publish export-manual --publish-key demo-001 --destination tk \
  > "$DATOS/08_export_manual.json"

paso "10. El operador registra lo que publico a mano"
vg publish record-manual --publish-key demo-001 --destination tk \
  --url "https://www.tiktok.com/@cuenta_demo/video/0000000000000000000" \
  --at 2026-09-24T19:20:00+02:00 > "$DATOS/09_record_manual.json"

paso "11. Estado final y recibo"
vg publish status --publish-key demo-001 --out "$DATOS/publication.json" \
  > "$DATOS/10_status.json" || true

paso "12. El mismo paquete en modo real se rechaza ANTES de cualquier llamada"
vg publish plan \
  --script "$EJEMPLOS/visuales_simulados/script.json" \
  --voice "$EJEMPLOS/visuales_simulados/voz/voice.json" \
  --media "$EJEMPLOS/visuales_simulados/medios/media.json" \
  --render "$EJEMPLOS/montaje_preview/render.json" \
  --accounts "$EJEMPLOS/publicacion_simulada/accounts.json" \
  --mode real --publish-key demo-real-001 \
  --destination "id=yt,platform=youtube_shorts,account=canal_demo,at=2026-09-24T18:30:00,tz=Europe/Madrid,visibility=private" \
  > "$DATOS/11_plan_real.json" || true
vg publish approve --plan "$DATOS/publicaciones/demo-real-001/publication_plan.json" \
  --operator operador_demo > "$DATOS/12_approve_real.json" 2> "$DATOS/12_approve_real.err" \
  && echo "ERROR: el modo real no deberia haberse autorizado" >&2 && exit 1

printf '\nDatos de la demostracion en: %s\n' "$DATOS" >&2
