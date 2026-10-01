#!/usr/bin/env bash
#===============================================================================
# create_pjsip_accounts.sh — génère de vrais comptes PJSIP pour softphones
#===============================================================================
# Produit trois fichiers :
#   1. asterisk/config/pjsip.conf      : endpoints + auth + AOR
#   2. asterisk/config/extensions.conf : hints (contextes internal + mcp-internal)
#   3. docs/sip-accounts.md            : table des identifiants softphone
#
# Les blocs sont délimités par des marqueurs ; le script les remplace intégralement,
# il est donc idempotent et peut être rejoué sans cumuler les comptes.
#
# Les mots de passe sont tirés au hasard UNE SEULE FOIS puis conservés : un re-run
# ne casse pas les softphones déjà configurés.
#
# Usage :
#   ./scripts/create_pjsip_accounts.sh                       # 1001-1010 + 1099
#   ./scripts/create_pjsip_accounts.sh --range 1001-1020      # plage personnalisée
#   ./scripts/create_pjsip_accounts.sh --range 1001-1010 --extra 1099,1100
#   ./scripts/create_pjsip_accounts.sh --dry-run             # aucune écriture
#   ./scripts/create_pjsip_accounts.sh --contact 10.0.0.5    # AOR statique (SIPp)
#
# Afterwards: ./scripts/provision_asterisk.sh --yes   puis rechargement AMI.
#===============================================================================
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)

PJSIP_CONF="$REPO_ROOT/asterisk/config/pjsip.conf"
EXT_CONF="$REPO_ROOT/asterisk/config/extensions.conf"
DOC_FILE="$REPO_ROOT/docs/sip-accounts.md"

RANGE="1001-1010"
EXTRA="1099"
DRY_RUN=0
FORCE=0
STATIC_CONTACT=""
CLEAR_PASSWORD=0

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --range)      RANGE="${2:?--range exige une valeur}"; shift 2 ;;
    --extra)      EXTRA="${2:?--extra exige une valeur}"; shift 2 ;;
    --contact)    STATIC_CONTACT="${2:?--contact exige une valeur}"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    --force)      FORCE=1; shift ;;
    --show-passwords) CLEAR_PASSWORD=1; shift ;;
    -h|--help)    usage 0 ;;
    *)            echo "Option inconnue : $1" >&2; usage 2 ;;
  esac
done

#------------------------------------------------------------------------------
# Génération / lecture des mots de passe
#------------------------------------------------------------------------------
gen_password() {
  # 20 caractères, alphabet sans ambiguïté (pas de O/0/l/1)
  LC_ALL=C tr -dc 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789' \
    </dev/urandom | head -c 20
}

# Mot de passe existant pour $1, ou chaîne vide. Évite de casser les softphones
# déjà configurés quand on relance le script.
existing_password() {
  local ext="$1"
  awk -v ext="$ext" '
    /^\[/            { in_auth = ($0 == "[" ext "](auth-userpass)") }
    in_auth && $1 == "password" { print $3; exit }
  ' "$PJSIP_CONF"
}

#------------------------------------------------------------------------------
# Construction de la liste des extensions
#------------------------------------------------------------------------------
EXTENSIONS=()
if [ -n "$RANGE" ]; then
  local_start="${RANGE%%-*}"
  local_end="${RANGE##*-}"
  if ! [[ "$local_start" =~ ^[0-9]+$ && "$local_end" =~ ^[0-9]+$ ]]; then
    echo "Plage invalide : '$RANGE' (format attendu 1001-1010)" >&2
    exit 1
  fi
  if [ "$local_end" -lt "$local_start" ]; then
    echo "Plage inversée : '$RANGE'" >&2
    exit 1
  fi
  for ((i = local_start; i <= local_end; i++)); do EXTENSIONS+=("$i"); done
fi
if [ -n "$EXTRA" ]; then
  IFS=',' read -ra extra_list <<< "$EXTRA"
  for e in "${extra_list[@]}"; do
    [ -z "$e" ] && continue
    EXTENSIONS+=("$e")
  done
fi

if [ ${#EXTENSIONS[@]} -eq 0 ]; then
  echo "Aucune extension demandée." >&2
  exit 1
fi

# Libellé lisible : 1099 est le superviseur, le reste des postes.
label_for() {
  case "$1" in
    1099) echo "Superviseur" ;;
    *)    echo "Poste $1" ;;
  esac
}

#------------------------------------------------------------------------------
# Rendu des blocs
#------------------------------------------------------------------------------
render_pjsip_block() {
  local ext pwd
  echo "; Postes générés par scripts/create_pjsip_accounts.sh ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  for ext in "${EXTENSIONS[@]}"; do
    pwd=$(existing_password "$ext")
    if [ -z "$pwd" ]; then
      pwd=$(gen_password)
    fi
    PASSWORDS["$ext"]="$pwd"
    CREDENTIALS+=("$ext|$pwd|$(label_for "$ext")")
    printf '\n[%s](endpoint-internal)\n' "$ext"
    printf 'auth = %s\n' "$ext"
    printf 'aors = %s\n' "$ext"
    printf 'callerid = %s <%s>\n' "$(label_for "$ext")" "$ext"
    if [ -n "$STATIC_CONTACT" ]; then
      printf 'contact = %s\n' "$STATIC_CONTACT"
    fi
    printf '\n[%s](auth-userpass)\nusername = %s\npassword = %s\n' "$ext" "$ext" "$pwd"
    printf '\n[%s](aor-single)\n' "$ext"
  done
}

render_hints_block() {
  local ext
  for ext in "${EXTENSIONS[@]}"; do
    echo "exten => ${ext},hint,PJSIP/${ext}"
  done
}

render_doc() {
  local ext pwd label
  echo "# Comptes PJSIP pour softphones"
  echo
  echo "Généré par \`scripts/create_pjsip_accounts.sh\` le $(date -u +%Y-%m-%d)."
  echo
  echo "## Paramètres de connexion"
  echo
  echo "| Champ | Valeur |"
  echo "| --- | --- |"
  echo "| Domaine Asterisk | \`${ASTERISK_HOST_IP:-<adresse IP hote>}\` |"
  echo "| Transport | UDP |"
  echo "| Port SIP | 5060 |"
  echo "| Codecs | \`ulaw\` (PCMU), \`alaw\` (PCMA), \`slin16\` |"
  echo "| Authentification | Login / mot de passe (USER) |"
  echo "| Nom d'affichage | *qui que vous soyez* |"
  echo
  echo "## Comptes"
  echo
  echo "| Extension | Identifiant | Mot de passe | Rôle |"
  echo "| --- | --- | --- | --- |"
  for row in "${CREDENTIALS[@]}"; do
    ext="${row%%|*}"; rest="${row#*|}"
    pwd="${rest%%|*}"; label="${rest##*|}"
    if [ "$CLEAR_PASSWORD" -eq 1 ]; then
      echo "| \`${ext}\` | \`${ext}\` | \`${pwd}\` | ${label} |"
    else
      echo "| \`${ext}\` | \`${ext}\` | *(masqué)* | ${label} |"
    fi
  done
  echo
  if [ "$CLEAR_PASSWORD" -eq 1 ]; then
    echo "> Ces identifiants sont en clair dans ce fichier : ne le committez pas."
  else
    echo "> Les mots de passe sont masqués. Pour les afficher :"
    echo "> \`./scripts/create_pjsip_accounts.sh --show-passwords\`"
    echo
    echo "> **Ne committez pas la version en clair de ce fichier.**"
  fi
  echo
  echo "## Vérifier l'enregistrement"
  echo
  echo '```bash'
  echo "asterisk -rx 'pjsip show contacts'"
  echo "asterisk -rx 'pjsip show endpoint 1001'"
  echo '```'
  echo
  echo "Un contact n'apparaît que **si le softphone s'est enregistré** : c'est le"
  echo "critère de succès de la Phase 2. L'endpoint reste \`Unavailable\` tant que"
  echo "rien n'est enregistré — c'est normal et attendu."
}

# Remplace le contenu entre deux marqueurs dans un fichier (in-place).
replace_block() {
  local file="$1" begin="$2" end="$3" content="$4"
  awk -v b="$begin" -v e="$end" -v c="$content" '
    $0 == b { skipping = 1; print $0; print c; next }
    $0 == e { skipping = 0; print $0; next }
    !skipping { print }
  ' "$file" > "$file.tmp" && mv "$file.tmp" "$file"
}

#------------------------------------------------------------------------------
# Contrôles préalables
#------------------------------------------------------------------------------
for f in "$PJSIP_CONF" "$EXT_CONF"; do
  if [ ! -f "$f" ]; then
    echo "Fichier introuvable : $f" >&2
    exit 1
  fi
done
for pair in "$PJSIP_CONF:ACCOUNTS-BEGIN:ACCOUNTS-END" \
            "$EXT_CONF:HINTS-INTERNAL-BEGIN:HINTS-INTERNAL-END" \
            "$EXT_CONF:HINTS-MCP-BEGIN:HINTS-MCP-END"; do
  f="${pair%%:*}"; rest="${pair#*:}"; b="${rest%%:*}"; e="${rest##*:}"
  if ! grep -q "^; >>> ${b}\$" "$f"; then
    echo "Marqueur '; >>> ${b}' absent de $(basename "$f") — regeneratez le gabarit." >&2
    exit 1
  fi
  if ! grep -q "^; <<< ${e}\$" "$f"; then
    echo "Marqueur '; <<< ${e}' absent de $(basename "$f") — regeneratez le gabarit." >&2
    exit 1
  fi
done

# ASTERISK_HOST_IP sert uniquement à la doc ; évite de scanner le réseau ici.
ASTERISK_HOST_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || true)

declare -a CREDENTIALS=()
declare -A PASSWORDS=()

PJ_BLOCK=$(render_pjsip_block)
HINTS_BLOCK=$(render_hints_block)
DOC_BLOCK=$(render_doc)

echo "Postes à générer : ${#EXTENSIONS[@]} (${EXTENSIONS[*]})"
if [ "$DRY_RUN" -eq 1 ]; then
  echo
  echo "----- aperçu pjsip.conf (extrait) -----"
  printf '%s\n' "$PJ_BLOCK" | grep -E '^\[|^password' \
    | sed -E 's/^([[:space:]]*password[[:space:]]*=[[:space:]]*).*/\1<masqué>/' \
    | head -6 | sed 's/^/  /'
  echo "  ... (mots de passe masqués)"
  echo
  echo "----- aperçu extensions.conf (hints) -----"
  printf '%s\n' "$HINTS_BLOCK" | head -4 | sed 's/^/  /'
  echo "  ... $(printf '%s\n' "$HINTS_BLOCK" | wc -l) hints au total"
  echo
  echo "Dry-run : aucun fichier modifié."
  exit 0
fi

replace_block "$PJSIP_CONF" "; >>> ACCOUNTS-BEGIN" "; <<< ACCOUNTS-END" "$PJ_BLOCK"
replace_block "$EXT_CONF" "; >>> HINTS-INTERNAL-BEGIN" "; <<< HINTS-INTERNAL-END" "$HINTS_BLOCK"
replace_block "$EXT_CONF" "; >>> HINTS-MCP-BEGIN" "; <<< HINTS-MCP-END" "$HINTS_BLOCK"
mkdir -p "$(dirname "$DOC_FILE")"
printf '%s\n' "$DOC_BLOCK" > "$DOC_FILE"

echo "Écrit :"
echo "  $PJSIP_CONF"
echo "  $EXT_CONF"
echo "  $DOC_FILE"
echo
echo "Prochaine étape :"
echo "  ./scripts/provision_asterisk.sh --yes"
echo "  asterisk -rx 'pjsip show endpoints'"