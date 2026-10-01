#!/usr/bin/env bash
# Provisionne Asterisk sur la MACHINE SYSTÈME (bare metal) pour le serveur MCP :
#   - installe Asterisk LTS et les modules requis (res_ari, res_prometheus, …)
#   - rend asterisk/config/*.conf (placeholders {{...}} -> secrets du .env)
#   - corrige l'ACL AMI et peuple le contexte mcp-internal
#   - active et démarre le service systemd, puis vérifie
#
#   sudo ./scripts/provision_asterisk.sh [--dry-run] [--yes] [--force]
#
# Idempotent : rejouable sans effet de bord. Non destructif : /etc/asterisk est
# sauvegardé avant chaque écriture, et un fichier déjà conforme n'est pas réécrit
# sauf --force.
#
# Secrets : lus dans le .env du dépôt (ASTERISK_AMI_SECRET, ASTERISK_ARI_PASSWORD,
# ASTERISK_METRICS_PASSWORD). Absents ou en placeholder `changeme*` -> générés
# puis RÉÉCRITS dans le .env, pour que le serveur MCP et Prometheus lisent la
# MÊME valeur que celle appliquée ici. Aucun secret n'est affiché.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_SRC="$ROOT/asterisk/config"
# Surchargeable pour les tests (jamais en production).
ETC_ASTERISK="${ETC_ASTERISK:-/etc/asterisk}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

DRY_RUN=0
ASSUME_YES=0
FORCE=0
PACKAGES=0

ASTERISK_LTS="20"
ASTERISK_PKG_REPO_URL="https://downloads.asterisk.org/pub/telephony/asterisk"

usage() {
  sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'USAGE'

Options :
  --dry-run   affiche le plan (détection, secrets, fichiers) sans rien écrire
  --yes       ne demande pas de confirmation interactive
  --force     réécrit les fichiers même s'ils sont déjà conformes
  --packages  installe seulement les dépendances système, sans toucher Asterisk
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --force) FORCE=1 ;;
    --packages) PACKAGES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# ─────────────────────────────── helpers ───────────────────────────────
say()  { printf '==> %s\n' "$*"; }
warn() { printf 'ATTENTION : %s\n' "$*" >&2; }
die()  { printf 'ERREUR : %s\n' "$*" >&2; exit 1; }

# Exécute une commande mutante, ou l'affiche en --dry-run.
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

confirm() {
  [ "$DRY_RUN" -eq 1 ] && return 0
  [ "$ASSUME_YES" -eq 1 ] && return 0
  printf '%s [oui/N] ' "$1"
  read -r reply
  case "$reply" in o*|O*|y*|Y*) return 0 ;; *) say "Annulé."; exit 1 ;; esac
}

# ─────────────────────────── secrets (.env) ───────────────────────────
# Même contrat que setup_test_asterisk.sh : env > .env > génération.
env_file_get() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\(.*\)$/\1/p" "$ENV_FILE" \
    | sed 's/[[:space:]]*$//' | tail -1
}

env_file_set() {
  if [ "$DRY_RUN" -eq 1 ]; then
    # Jamais la valeur en clair : elle irait dans l'historique du shell.
    printf '    [dry-run] %s=<secret> (dans %s)\n' "$1" "$ENV_FILE" >&2
    return 0
  fi
  [ -f "$ENV_FILE" ] || return 0
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v val="$val" '
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" { print key "=" val; seen=1; next }
    { print }
    END { if (!seen) print key "=" val }
  ' "$ENV_FILE" >"$tmp"
  # cat (et non mv) pour conserver l'inode et les permissions du .env.
  cat "$tmp" >"$ENV_FILE"
  rm -f "$tmp"
}

gen_secret() { openssl rand -hex 24; }

# $1 = clé .env, $2 = nom lisible. Échoie le secret résolu.
resolve_secret() {
  local key="$1" label="$2" val
  val="${!key:-}"
  [ -n "$val" ] || val="$(env_file_get "$key")"
  case "$(printf '%s' "$val" | tr '[:upper:]' '[:lower:]')" in
    ""|changeme*|changeme)
      val="$(gen_secret)"
      say "secret $label généré (écrit dans $(basename "$ENV_FILE"))"
      ;;
  esac
  env_file_set "$key" "$val"
  printf '%s' "$val"
}

# ─────────────────────────── préflight ───────────────────────────
detect_distro() {
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    printf '%s|%s|%s' "${ID:-unknown}" "${VERSION_ID:-}" "${ID_LIKE:-}"
  else
    printf 'unknown||'
  fi
}

preflight() {
  say "Préflight"
  local id ver like
  IFS='|' read -r id ver like <<<"$(detect_distro)"

  case "$id" in
    debian|ubuntu)
      PKG_INSTALL="apt-get install -y"
      PKG_UPDATE="apt-get update"
      ;;
    rhel|centos|rocky|almalinux|fedora)
      PKG_INSTALL="dnf install -y"
      PKG_UPDATE="true"
      ;;
    *)
      if printf '%s' "$like" | grep -q debian; then
        id=debian; PKG_INSTALL="apt-get install -y"; PKG_UPDATE="apt-get update"
      elif printf '%s' "$like" | grep -qi rhel; then
        id=rhel; PKG_INSTALL="dnf install -y"; PKG_UPDATE="true"
      else
        die "distribution non supportée : $id $ver ($like). Debian/Ubuntu ou RHEL attendu."
      fi
      ;;
  esac
  printf '    distribution : %s %s\n' "$id" "$ver"
  DISTRO_ID="$id"

  if [ "$(id -u)" -ne 0 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      warn "root requis — poursuite en dry-run (rien ne sera écrit)"
    else
      die "à exécuter en root : sudo $0"
    fi
  else
    printf '    privileges    : root\n'
  fi

  command -v asterisk >/dev/null 2>&1 \
    && say "Asterisk déjà installé : $(asterisk -V 2>/dev/null | head -1)" \
    || printf '    Asterisk      : absent (sera installé)\n'
  command -v systemctl >/dev/null 2>&1 || die "systemd absent : distribution non gérée."

  # Ports requis — on n'échoue pas (Asterisk peut déjà tourner), on alerte.
  local busy=()
  for p in 5060 5038 8088; do
    if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$p\$"; then
      busy+=("$p")
    fi
  done
  [ ${#busy[@]} -gt 0 ] && printf '    ports déjà en écoute : %s (Asterisk tourne peut-être déjà)\n' "${busy[*]}"
  return 0
}

# ─────────────────────────── dépendances ───────────────────────────
install_packages() {
  say "Dépendances système"
  local pkgs=(curl ca-certificates xmlstarlet openssl)
  case "$DISTRO_ID" in
    debian) pkgs+=(python3 build-essential libncurses-dev uuid-dev libssl-dev libcurl4-openssl-dev libxml2-dev libsqlite3-dev libedit-dev libjansson-dev) ;;
    rhel)   pkgs+=(python3 gcc gcc-c++ make ncurses-devel libuuid-devel openssl-devel libcurl-devel libxml2-devel sqlite-devel jansson-devel) ;;
  esac
  run $PKG_UPDATE >/dev/null 2>&1 || true
  run $PKG_INSTALL "${pkgs[@]}" || warn "installation des dépendances échouée (poursuite)"
}

install_asterisk() {
  command -v asterisk >/dev/null 2>&1 && { say "Asterisk déjà présent, installation ignorée"; return 0; }
  say "Installation d'Asterisk $ASTERISK_LTS"
  case "$DISTRO_ID" in
    debian)
      run $PKG_UPDATE
      run $PKG_INSTALL asterisk asterisk-dahdi linux-image-asterisk 2>/dev/null \
        || run $PKG_INSTALL asterisk
      ;;
    rhel)
      run dnf install -y "https://downloads.asterisk.org/pub/telephony/asterisk/rpm/redhat/epel/asterisk-${ASTERISK_LTS}-current.el$(rpm -E '%{rhel}').noarch.rpm" \
        || run $PKG_INSTALL asterisk
      ;;
  esac
  command -v asterisk >/dev/null 2>&1 || warn "asterisk toujours absent après installation — à installer manuellement."
}

# ─────────────────────────── secrets ───────────────────────────
load_secrets() {
  say "Secrets (source de vérité : $(basename "$ENV_FILE"))"
  AMI_SECRET="$(resolve_secret ASTERISK_AMI_SECRET 'AMI')"
  ARI_PASSWORD="$(resolve_secret ASTERISK_ARI_PASSWORD 'ARI')"
  METRICS_PASSWORD="$(resolve_secret ASTERISK_METRICS_PASSWORD 'métriques')"

  # Réseaux autorisés pour AMI : RFC1918 par défaut, surchargeable.
  TRUSTED="${ASTERISK_TRUSTED_CIDR:-127.0.0.1/255.255.255.255 10.0.0.0/255.0.0.0 172.16.0.0/255.240.0.0 192.168.0.0/255.255.0.0}"
  AMI_PERMIT_LINES="$(printf '%s\n' $TRUSTED | sed 's/^/permit = /')"
  printf '    ACL AMI       : %s\n' "$TRUSTED"

  # Trunk : livré seulement si les 3 variables sont présentes.
  TRUNK_HOST="$(env_file_get ASTERISK_TRUNK_HOST)"
  TRUNK_USER="$(env_file_get ASTERISK_TRUNK_USER)"
  TRUNK_SECRET="$(env_file_get ASTERISK_TRUNK_SECRET)"
  if [ -n "$TRUNK_HOST" ] && [ -n "$TRUNK_USER" ] && [ -n "$TRUNK_SECRET" ]; then
    DELIVER_TRUNK=1
    printf '    trunk         : livré (%s)\n' "$TRUNK_HOST"
  else
    DELIVER_TRUNK=0
    printf '    trunk         : NON livré (renseigner ASTERISK_TRUNK_HOST/USER/SECRET pour l''activer)\n'
  fi
}

# ─────────────────────────── rendu ───────────────────────────
# Retire le bloc encadré par « >>> TRUNK-BEGIN » … « ; <<< TRUNK-END ».
strip_trunk() {
  awk '
    /^; >>> TRUNK-BEGIN/ { skip=1; print "; (bloc trunk retiré : ASTERISK_TRUNK_HOST non configuré)"; next }
    /^; <<< TRUNK-END/   { skip=0; next }
    !skip
  ' "$1"
}

# Rendu d'un gabarit : substitution {{...}} + retrait éventuel du trunk.
# awk est utilisé (et non sed s///) car AMI_PERMIT_LINS est multi-lignes :
# une valeur contenant un saut de ligne rend l'expression sed invalide. awk
# gère aussi sans risque les caractères spéciaux des secrets (&, |, \).
render_template() {
  local src="$1" trunk_enabled="$2"
  if [ "$trunk_enabled" -eq 0 ]; then
    strip_trunk "$src"
  else
    cat "$src"
  fi | awk \
    -v ari="$ARI_PASSWORD" \
    -v ami="$AMI_SECRET" \
    -v met="$METRICS_PASSWORD" \
    -v acl="$AMI_PERMIT_LINES" \
    -v t_host="$TRUNK_HOST" \
    -v t_user="$TRUNK_USER" \
    -v t_secret="$TRUNK_SECRET" '
      {
        line = $0
        gsub(/\{\{ARI_PASSWORD\}\}/,        ari,     line)
        gsub(/\{\{AMI_SECRET\}\}/,         ami,     line)
        gsub(/\{\{METRICS_PASSWORD\}\}/,    met,     line)
        gsub(/\{\{TRUNK_HOST\}\}/,         t_host,  line)
        gsub(/\{\{TRUNK_USER\}\}/,         t_user,  line)
        gsub(/\{\{TRUNK_SECRET\}\}/,       t_secret, line)
        # ACL multi-lignes : substitution à part (sinon gsub tronque) — on
        # réinjecte explicitement le saut de ligne entre chaque entrée, sinon
        # les règles « permit = … » se concaténeraient sur une seule ligne.
        if (line ~ /\{\{AMI_PERMIT_LINES\}\}/) {
          n = split(line, parts, /\{\{AMI_PERMIT_LINES\}\}/)
          out = parts[1]
          m = split(acl, rows, "\n")
          for (i = 1; i <= m; i++) { out = out rows[i]; if (i < m) out = out "\n" }
          for (i = 2; i <= n; i++) out = out parts[i]
          print out
          next
        }
        print line
      }
    '
}

# Écrit si absent, ou si différent (idempotence) — sauf --force.
install_conf() {
  # Déclarations séparées : sous `set -u`, `local a=.. b="$a"` échoue car bash
  # n'ordonne pas les affectations d'un même `local`.
  local name="$1"
  local src="$CONF_SRC/$name"
  local dst="$ETC_ASTERISK/$name"
  local tmp
  [ -f "$src" ] || { warn "gabarit absent : $src"; return 0; }

  tmp="$(mktemp)"
  render_template "$src" "$DELIVER_TRUNK" >"$tmp"

  if [ "$DRY_RUN" -eq 1 ]; then
    if [ -f "$dst" ] && cmp -s "$tmp" "$dst"; then
      printf '    [dry-run] %-22s déjà conforme\n' "$name"
    else
      printf '    [dry-run] %-22s -> %s\n' "$name" "$dst"
    fi
    rm -f "$tmp"; return 0
  fi

  if [ -f "$dst" ] && cmp -s "$tmp" "$dst" && [ "$FORCE" -eq 0 ]; then
    printf '    %-22s déjà conforme (inchangé)\n' "$name"
    rm -f "$tmp"; return 0
  fi

  install -D -m 0640 "$tmp" "$dst"
  chown asterisk:asterisk "$dst" 2>/dev/null || true
  printf '    %-22s écrit\n' "$name"
  rm -f "$tmp"
}

# Le modèle asterisk.conf impose `runuser/rungroup = asterisk` : sans ce compte
# système, le binaire refuse de démarrer (« No such group 'asterisk'! »).
# Certains hôtes ont Asterisk installé sans l'utilisateur (paquet incomplete,
# image minimal) : on le crée ici. Les fichiers de /etc/asterisk restent
# root:root (lecture seule par Asterisk), seuls les répertoires d'état sont
# attribués à l'utilisateur.
ensure_asterisk_user() {
  if getent group asterisk >/dev/null 2>&1; then
    :
  else
    say "création du groupe système asterisk"
    groupadd --system asterisk || die "groupadd asterisk a échoué"
  fi

  if ! getent passwd asterisk >/dev/null 2>&1; then
    say "création de l'utilisateur système asterisk"
    useradd --system --gid asterisk --home-dir /var/lib/asterisk \
            --shell /usr/sbin/nologin --no-create-home asterisk \
            || die "useradd asterisk a échoué"
  fi

  local d
  for d in /var/lib/asterisk /var/log/asterisk /var/spool/asterisk \
           /var/run/asterisk /var/cache/asterisk; do
    [ -d "$d" ] || mkdir -p "$d"
    # -R : les fichiers DEJÀ présents (messages.log, queue_log...) sont
    # souvent root:root après une installation faite en root. Asterisk tourne
    # maintenant sous `asterisk` et ne peut plus les écrire, ce qui produit
    # « Errors detected in logger.conf » puis un démarrage en console.
    chown -R asterisk:asterisk "$d" || warn "chown -R $d a échoué"
  done
}

# Vrai si au moins un gabarit rendu diffère de sa cible (ou --force).
configs_need_writing() {
  [ "$FORCE" -eq 1 ] && return 0
  local name src dst tmp
  for name in asterisk.conf http.conf ari.conf prometheus.conf cdr.conf \
              cdr_manager.conf manager.conf pjsip.conf extensions.conf \
              queues.conf rtp.conf modules.conf; do
    src="$CONF_SRC/$name"
    [ -f "$src" ] || continue
    dst="$ETC_ASTERISK/$name"
    [ -f "$dst" ] || return 0
    tmp="$(mktemp)"
    render_template "$src" "$DELIVER_TRUNK" >"$tmp"
    if ! cmp -s "$tmp" "$dst"; then rm -f "$tmp"; return 0; fi
    rm -f "$tmp"
  done
  return 1
}

deploy_configs() {
  say "Déploiement de la configuration dans $ETC_ASTERISK"

  # Sauvegarde : seulement si au moins un fichier va réellement changer
  # (évite d'empiler des .bak identiques à chaque exécution idempotente).
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] sauvegarde de %s omise\n' "$ETC_ASTERISK"
  elif [ -d "$ETC_ASTERISK" ] && configs_need_writing; then
    local backup="$ETC_ASTERISK.bak.$(date +%Y%m%d%H%M%S)"
    cp -a "$ETC_ASTERISK" "$backup"
    say "sauvegarde : $backup"
  fi

  # NB : le modèle définit [mcp_ami] directement dans manager.conf (et non via
  # manager.d/). Aucun #include n'est ajouté ici : le comptes AMI est ainsi
  # auto-suffisant et idempotent. Un déploiement antérieur ayant utilisé
  # manager.d/ reste compatible (les deux fichiers se complètent).
  local f
  for f in asterisk.conf http.conf ari.conf prometheus.conf cdr.conf \
           cdr_manager.conf manager.conf pjsip.conf extensions.conf \
           queues.conf rtp.conf modules.conf; do
    install_conf "$f"
  done

  # Permissions.
  #
  # Le RÉPERTOIRE doit rester traversable (0755, défaut Debian) : Asterisk y lit
  # ses propres fichiers de configuration. Un 0750 root:root — pourtant
  # « plus sécurisé » — rend /etc/asterisk ILLISIBLE pour l'utilisateur
  # `asterisk`, qui n'est pas dans le groupe root. Conséquence réelle observée :
  #
  #   WARNING loader.c: 'modules.conf' invalid or missing.
  #   ERROR   asterisk.c: Module initialization failed.  ASTERISK EXITING!
  #
  # ...et en mode daemon, une sortie silencieuse sans le moindre log : le
  # service paraît "active (exited)" alors qu'aucun processus ne tourne.
  #
  # La protection des secrets est assurée par les FICHIERS, en 0640
  # asterisk:asterisk (install -m 0640 ci-dessus) : lisibles par Asterisk et
  # root seulement.
  if [ "$DRY_RUN" -eq 0 ]; then
    chmod 0755 "$ETC_ASTERISK"
  fi
}

# ─────────────────────────── service ───────────────────────────
reload_asterisk() {
  say "Service systemd"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] systemctl enable --now asterisk + rechargements\n'
    return 0
  fi
  command -v asterisk >/dev/null 2>&1 || { warn "asterisk absent : service non démarré"; return 0; }

  run systemctl daemon-reload
  run systemctl enable asterisk

  # --- Redémarrage fiable -------------------------------------------------
  # Le script d'init SysV détecte un état périmé (pidfile / asterisk.ctl
  # laissés par un arrêt sale) et répond « already running » EN SORTANT AVEC
  # LE CODE 0 : systemd annonce alors « Started » alors qu'aucun daemon ne
  # tourne, et l'ancienne configuration reste active en silence. On compare
  # donc le PID avant/après, et on purge l'état périmé si besoin.
  pid_before="$(cat /var/run/asterisk/asterisk.pid 2>/dev/null || true)"
  run systemctl restart asterisk
  sleep 3
  pid_after="$(cat /var/run/asterisk/asterisk.pid 2>/dev/null || true)"

  if [ -n "$pid_before" ] && [ "$pid_before" = "$pid_after" ] \
     && ! pgrep -x asterisk >/dev/null 2>&1; then
    warn "systemctl restart n'a pas redémarré le daemon (état périmé détecté)."
    warn "Purge de /var/run/asterisk puis nouvel essai…"
    systemctl stop asterisk >/dev/null 2>&1 || true
    rm -f /var/run/asterisk/asterisk.ctl /var/run/asterisk/asterisk.pid
    run systemctl start asterisk
    sleep 3
    pid_after="$(cat /var/run/asterisk/asterisk.pid 2>/dev/null || true)"
  fi

  if ! pgrep -x asterisk >/dev/null 2>&1; then
    die "le daemon Asterisk ne tourne pas après restart — voir journalctl -u asterisk"
  fi
  printf '    daemon asterisk      : pid %s\n' "${pid_after:-inconnu}"

  # Rechargements ciblés (idempotents, sans couper les appels en cours).
  # pjsip reload est indispensable après une modification de pjsip.conf :
  # sans lui, les nouveaux endpoints restent invisibles.
  for cmd in "pjsip reload" \
             "module reload manager" "module reload res_ari.so" \
             "module reload cdr_manager.so" "module reload res_prometheus.so" \
             "module reload app_queue.so" "dialplan reload" "core reload"; do
    run asterisk -rx "$cmd" >/dev/null 2>&1 || true
  done
}

open_firewall() {
  say "Pare-feu (5038 AMI, 8088 ARI/métriques)"
  command -v ufw >/dev/null 2>&1 || { printf '    ufw absent — ignoré\n'; return 0; }
  [ "$DRY_RUN" -eq 0 ] || { printf '    [dry-run] ufw allow 5038/tcp 8088/tcp\n'; return 0; }
  ufw allow from "$TRUSTED_FIRST" to any port 5038 proto tcp >/dev/null 2>&1 || true
  ufw allow from "$TRUSTED_FIRST" to any port 8088 proto tcp >/dev/null 2>&1 || true
  printf '    règles ouvertes pour %s\n' "$TRUSTED_FIRST"
}

# ─────────────────────────── vérification ───────────────────────────
verify() {
  say "Vérification"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] contrôles AMI/ARI/HTTP omis\n'
    return 0
  fi
  command -v asterisk >/dev/null 2>&1 || { warn "asterisk absent : vérification impossible"; return 0; }

  asterisk -rx "manager show users" 2>/dev/null | sed 's/^/    /' || true
  asterisk -rx "ari show users"      2>/dev/null | sed 's/^/    /' || true
  asterisk -rx "http show status"   2>/dev/null | grep -iE "server|enabled" | sed 's/^/    /' || true
  asterisk -rx "module show like res_prometheus" 2>/dev/null | tail -2 | sed 's/^/    /' || true

  printf '\n'
  say "Paquets concernés par la supervision :"
  cat <<'EOF'
    manager.conf    -> AMI    (read: call,cdr,reporting ; write: call,originate)
    ari.conf        -> ARI    (utilisateur mcp_ari)
    prometheus.conf -> /metrics (Basic Auth, res_prometheus)
    mcp-internal    -> contexte par défaut de originate_call / redirect_call
EOF
  say "Renseignez ASTERISK_HOST dans $(basename "$ENV_FILE") avec l'IP joignable par le serveur MCP."
}

# ─────────────────────────── main ───────────────────────────
main() {
  command -v openssl >/dev/null 2>&1 || { echo "openssl est requis." >&2; exit 1; }
  [ -d "$CONF_SRC" ] || die "gabarits introuvables : $CONF_SRC"

  preflight
  # Premier réseau de confiance (pour le pare-feu) ; repli sur loopback.
  TRUSTED_FIRST="${ASTERISK_TRUSTED_CIDR:-127.0.0.1/255.255.255.255}"
  TRUSTED_FIRST="${TRUSTED_FIRST%% *}"

  load_secrets

  if [ "$PACKAGES" -eq 1 ]; then
    install_packages
    say "Fin (--packages : Asterisk non provisionné)."
    return 0
  fi

  install_packages
  install_asterisk

  say "Plan"
  printf '    Asterisk    -> %s\n' "$ETC_ASTERISK"
  printf '    .env        -> %s\n' "$ENV_FILE"
  printf '    ACL AMI     -> %s\n' "$TRUSTED_FIRST"
  printf '    trunk       -> %s\n' "$([ "$DELIVER_TRUNK" -eq 1 ] && echo 'livré' || echo 'non livré')"

  confirm "Appliquer la configuration sur cette machine ?"
  ensure_asterisk_user
  deploy_configs
  reload_asterisk
  open_firewall
  verify

  say "Terminé."
}

main "$@"