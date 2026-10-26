#!/usr/bin/env bash
#
# Smart DNS installer - sanction-bypass DNS for Iran, in two halves.
#
#   relay  (inside Iran)  dnsmasq answers a list of blocked domains with its own
#                         address; nginx then carries those connections abroad
#   exit   (outside)      nginx reads the SNI and connects to the real host
#
# Run it on both machines, once each. It asks which side it is on and the
# address of the other. Safe to re-run: configs are backed up, and a step that
# would change nothing does nothing.
#
#   sudo bash master-dns.sh              install or update this machine
#   sudo bash master-dns.sh --uninstall  put the machine back as it was
#
# HTTPS for the panels is optional and asks for nothing but a domain name. A
# certificate is obtained and renewed automatically, proved over port 80 - so
# the name has to point at the machine and port 80 has to be reachable. On a
# relay that port is forwarded to the exit, so it is borrowed for the twenty
# seconds a challenge takes and given straight back; console downloads through
# it stall for that long and resume.
#
# PANEL_CERT and PANEL_KEY use a certificate you already have instead, and
# CF_API_TOKEN proves the domain over DNS without touching port 80. Neither is
# ever prompted for.
#
# Per-client access control ships with this but starts switched off. The relay
# counts each registered address's traffic from the moment it is installed and
# blocks nobody; `smartdns-acl enforce on` is what closes the door, and it is
# meant to be run once there is a way for users to register an address. Turning
# it on before then locks out everyone, including you.

set -euo pipefail

SELF="${BASH_SOURCE[0]}"
STAMP="$(date +%Y%m%d-%H%M%S)"

# What this file is. Written to the machine once an install finishes, so the
# next run can tell whether it is an upgrade, a re-run, or somebody about to
# put an older version over a newer one by accident.
VERSION="0.0.8"

# What this install did, so uninstall can undo exactly that and nothing more.
# Without it, removal would be guesswork: whether dnsmasq was ours or already
# here, whether nginx.conf had a config worth putting back. Guessing wrong on a
# box that was doing something else first is how an uninstall does damage.
STATE_DIR="/var/lib/smart-dns"
STATE="$STATE_DIR/install-state"

# Backups go here, never beside the original. dnsmasq reads *every* file in
# /etc/dnsmasq.d, so a backup left there is loaded as a second copy of the same
# config and the service refuses to start on "illegal repeated keyword". Found
# the hard way: it took a working relay down on the second run.
BACKUP_DIR="/var/backups/smart-dns"

# ---------------------------------------------------------------- output
if [ -t 1 ]; then
    B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; RD=$'\033[31m'; N=$'\033[0m'
    C=$'\033[1;36m'; D=$'\033[2m'
else
    B=""; G=""; Y=""; RD=""; N=""; C=""; D=""
fi
# Steps are numbered and timed, so a long install shows where it is and how
# long each part took, instead of a bare list of headings.
STEP_N=0; T_START="$(date +%s)"; STEP_NAME=(); STEP_AT=()
step() {
    STEP_N=$((STEP_N + 1))
    STEP_NAME[STEP_N]="$*"; STEP_AT[STEP_N]="$(date +%s)"
    printf '\n  %s%02d%s  %s%s%s  %s+%ss%s\n' "$C" "$STEP_N" "$N" "$B" "$*" "$N" \
           "$D" "$(( $(date +%s) - T_START ))" "$N"
}

# Where the time went, as bars: the last thing printed before the ready box, so
# a slow install says which part was slow.
timeline() {
    local i d max=1 w=20 n k bar now; local -a took=()
    now="$(date +%s)"
    [ "$STEP_N" -gt 0 ] || return 0
    for ((i = 1; i <= STEP_N; i++)); do
        d=$(( ${STEP_AT[i + 1]:-$now} - STEP_AT[i] )); took[i]=$d
        [ "$d" -gt "$max" ] && max=$d
    done
    printf '\n  %sTimeline%s\n' "$B" "$N"
    for ((i = 1; i <= STEP_N; i++)); do
        n=$(( took[i] * w / max )); [ "$n" -lt 1 ] && [ "${took[i]}" -gt 0 ] && n=1
        bar=""; for ((k = 0; k < n; k++)); do bar+="#"; done
        printf '    %s%02d%s  %-30.30s %s%-*s%s %3ss\n' "$C" "$i" "$N" "${STEP_NAME[i]}" "$C" "$w" "$bar" "$N" "${took[i]}"
    done
}
info() { printf '    %s\n' "$*"; }
warn() { printf '    %s%s%s\n' "$Y" "$*" "$N"; }
die()  { printf '\n%sERROR:%s %s\n\n' "$RD" "$N" "$*" >&2; exit 1; }

# ---------------------------------------------------------------- payloads
# Configs live at the bottom of this file, after exit 0, between markers, with
# every line prefixed by '#' so the whole script stays valid bash. awk copies
# them out and strips that prefix - no shell expansion anywhere, so nginx's
# $variables and dnsmasq's syntax survive untouched.
payload() {
    awk -v name="$1" '
        $0 == "#__BEGIN_" name "__" { on = 1; next }
        $0 == "#__END_"   name "__" { on = 0 }
        on { sub(/^#/, ""); print }
    ' "$SELF"
}

backup_file() {
    [ -f "$1" ] || return 0
    mkdir -p "$BACKUP_DIR"
    cp -a "$1" "$BACKUP_DIR/$(basename "$1").$STAMP"
    info "backed up $1 -> $BACKUP_DIR"
}

# Write payload $1 to file $2, substituting the two addresses. Backs up whatever
# was there, and skips the write when the content is identical so re-runs do not
# churn files or trigger needless restarts. Returns 0 only if it changed.
# A single machine (ROLE=single) is both ends at once: the relay's DNS and
# gate in front of the exit's proxy, with the panel beside them. Most of this
# file asks which parts a machine has, not which role it is.
is_relay() { [ "$ROLE" = relay ] || [ "$ROLE" = single ]; }
# A node (ROLE=node) is another exit, joined to the panel on an exit: the
# exit's nginx and nothing of the panel.
is_exit()  { [ "$ROLE" = exit ] || [ "$ROLE" = single ] || [ "$ROLE" = node ]; }
# Where a single machine's sync API listens: loopback only, because 8443 is
# its customer panel, and the only relay it has is itself.
SINGLE_API_PORT=8449

# Whose name the exit's address is registered in. An address block registered
# to an Iranian company is still Iranian to Google, even on a server in
# Germany: EA FC's Ultimate Team runs on Google Cloud and does not answer such
# an exit at all (a user's report, 2026-09: Leaseweb Germany, netname IR-...),
# nor do other services on Google. Only a warning - the registry may be wrong,
# or it may not matter for what this exit is for - and silence when the
# registry cannot be asked.
exit_owner_check() {
    command -v python3 >/dev/null 2>&1 || return 0
    local found
    found="$(curl -fsSL -m 10 "https://rdap.org/ip/$1" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
why = []
if str(d.get("name") or "").upper().startswith("IR-"):
    why.append("the network is named %s" % d["name"])
def walk(entities):
    for e in entities or []:
        card = (e.get("vcardArray") or [None, []])[1]
        if "registrant" in (e.get("roles") or []):
            for f in card:
                if f[0] == "adr" and "iran" in json.dumps(f, ensure_ascii=False).lower():
                    names = [x[3] for x in card if x[0] == "fn"]
                    why.append("it is registered to %s, in Iran" % (names[0] if names else "a company"))
        walk(e.get("entities"))
walk(d.get("entities"))
print("; ".join(dict.fromkeys(why)))
' 2>/dev/null || true)"
    [ -n "$found" ] || return 0
    warn "this address is registered as Iranian: $found."
    warn "Google treats it as Iran, wherever the server is - EA FC Ultimate Team (on"
    warn "Google Cloud) and other Google-hosted services will not answer this exit."
    warn "an exit whose address is registered outside Iran avoids that."
}

# Where the relay's certificate is, and so whether it can serve DoH. The
# name is the customer panel's: one certificate, one name, for the panel on
# 8443 and for DNS on 443 and 853.
doh_paths() {
    DOH_ON=""; NO_DOH=1; DOH_HOST=""; DOH_CERT=""; DOH_KEY=""
    HTTPS_TARGET='$upstream'
    is_relay && [ -n "${PANEL_DOMAIN:-}" ] || return 0
    if [ -n "${PANEL_CERT:-}" ]; then
        DOH_CERT="$PANEL_CERT"; DOH_KEY="${PANEL_KEY:-}"
    else
        DOH_CERT="/etc/letsencrypt/live/$PANEL_DOMAIN/fullchain.pem"
        DOH_KEY="/etc/letsencrypt/live/$PANEL_DOMAIN/privkey.pem"
    fi
    if [ -f "$DOH_CERT" ] && [ -f "$DOH_KEY" ]; then
        DOH_ON=1; NO_DOH=""; DOH_HOST="$PANEL_DOMAIN"
        # A single machine's 443 sends its own name to the DoH server. An if,
        # not &&: the last command of a function is its status, and under
        # set -e a relay would have stopped here.
        if [ "$ROLE" = single ]; then HTTPS_TARGET='$https_target'; fi
    fi
}

# The relays an exit lets in, for nginx to include on every port it proxies:
# every one on the panel's list and the one this run was given. The admin
# panel rewrites the file when a relay is added or taken off there.
RELAYS_CONF=/etc/nginx/smartdns-relays.conf
relay_allows() {
    local ip out="" have=""
    # A node has no panel here: its list is the one smartdns-sync last had
    # from the panel, and is kept as it is.
    [ "$ROLE" = node ] && have="$(sed -n 's/^allow \(.*\);$/\1/p' "$RELAYS_CONF" 2>/dev/null || true)"
    for ip in $(sed -n 's/^RELAY_IP=//p' /etc/smart-dns/panel.env 2>/dev/null | head -1 | tr ',' ' ') \
              $have $RELAY_IP; do
        valid_ip "$ip" || continue
        case " $out " in *" $ip "*) continue ;; esac
        out="$out $ip"
    done
    note_file "$RELAYS_CONF"
    {
        printf '# The relays this exit lets in: written by the installer and the admin panel.\n'
        for ip in $out; do printf 'allow %s;\n' "$ip"; done
        # And where each would arrive from over a layer-3 tunnel: an address
        # only that tunnel's interface can carry.
        if [ "$ROLE" != node ]; then
            for ip in $out; do l3_block "$ip"; printf 'allow %s;\n' "$L3_RELAY"; done
        fi
    } > "$RELAYS_CONF"
    chmod 644 "$RELAYS_CONF"
}

# What nginx's DoH blocks include: the certificate, and the names that reach
# the DoH server. Written here with the machine's own name; once the admin
# panel has given DoH a name of its own and smartdns-sync has a certificate
# for it (/etc/smart-dns/doh-name), they are smartdns-sync's, and an upgrade
# leaves them be. Needed before nginx -t whenever DoH is on.
DOH_CERT_CONF=/etc/nginx/smartdns-doh-cert.conf
DOH_NAMES_MAP=/etc/nginx/smartdns-doh-names.map
doh_includes() {
    if [ -s /etc/smart-dns/doh-name ] && [ -s "$DOH_CERT_CONF" ] && [ -s "$DOH_NAMES_MAP" ]; then
        return 0
    fi
    note_file "$DOH_CERT_CONF"; note_file "$DOH_NAMES_MAP"
    printf 'ssl_certificate     %s;\nssl_certificate_key %s;\n' "$DOH_CERT" "$DOH_KEY" \
        > "$DOH_CERT_CONF"
    printf '%s 127.0.0.1:8453;\n' "$DOH_HOST" > "$DOH_NAMES_MAP"
    chmod 644 "$DOH_CERT_CONF" "$DOH_NAMES_MAP"
}

# Once the certificate exists: nginx with the DoH blocks in, and the flag
# that tells the customer panel to offer the addresses. On a first install
# this is what turns DoH on; on an upgrade the nginx step already had the
# certificate and this only confirms it.
relay_doh() {
    is_relay || return 0
    local was="$DOH_ON"
    doh_paths
    if [ -z "$DOH_ON" ]; then
        rm -f /etc/smart-dns/doh
        return 0
    fi
    doh_includes
    if [ -z "$was" ]; then
        if [ "$ROLE" = single ]; then
            install_payload EXIT_NGINX /etc/nginx/nginx.conf || true
        else
            install_payload RELAY_NGINX /etc/nginx/nginx.conf || true
        fi
        nginx -t || die "nginx rejected the DoH config; the previous one is in $BACKUP_DIR"
        systemctl reload nginx 2>/dev/null || systemctl restart nginx
    fi
    touch /etc/smart-dns/doh
    info "DNS over HTTPS on https://$DOH_HOST/dns-query, over TLS on $DOH_HOST:853"
}

install_payload() {
    local name="$1" dest="$2" tmp solo=1
    # A single machine's own blocks, and its dropping of the "only the relay"
    # lines, happen on a single machine only (ROLE=single sets SINGLE).
    [ -n "${SINGLE:-}" ] && solo=""
    tmp="$(mktemp)"
    # MODULE_PATH is filled in here, not with a sed -i afterwards, so that what
    # we compare against the installed file is the finished article. Doing it
    # after the comparison meant every run saw a difference and rewrote
    # nginx.conf - the same needless-restart trap epic-pin fell into. MOD is
    # empty for the payloads written before it is discovered, and none of those
    # contain the placeholder.
    payload "$name" \
        | sed -e "${SINGLE:+/smartdns-relays\\.conf;\$/d}" \
              -e "${SINGLE:+/^ *allow 127\.0\.0\.1;\$/d}" \
              -e "${SINGLE:+/^ *deny all;\$/d}" \
              -e "${solo:+/# single begin/,/# single end/d}" \
              -e "s#__HTTPS_TARGET__#${HTTPS_TARGET:-\$upstream}#g" \
              -e "s#__LISTEN_IP__#${LISTEN_IP:-$RELAY_IP}#g" \
              -e "s#__TURN_EXTERNAL__#${TURN_EXTERNAL:-$RELAY_IP}#g" \
              -e "s#__RELAY_IP__#${RELAY_IP}#g" \
              -e "s#__EXIT_IP__#${EXIT_IP}#g" \
              -e "s#__MODULE_PATH__#${MOD:-__MODULE_PATH__}#g" \
              -e "${NO_GOOGLE_V6:+/# google-v6 begin/,/# google-v6 end/d}" \
              -e "s#__DOH_HOST__#${DOH_HOST:-doh.invalid}#g" \
              -e "s#__DOH_CERT__#${DOH_CERT:-/nonexistent}#g" \
              -e "s#__DOH_KEY__#${DOH_KEY:-/nonexistent}#g" \
              -e "${NO_DOH:+/# doh begin/,/# doh end/d}" \
              -e "${DOH_ON:+/# nodoh begin/,/# nodoh end/d}" \
              -e "s#__RESOLVERS__#${RESOLVERS:-1.1.1.1 9.9.9.9}#g" \
        > "$tmp"
    [ -s "$tmp" ] || die "payload $name is empty - is this file complete?"
    # Whether this file was ours or already here decides what uninstall does
    # with it: delete, or put the original back. Work it out before writing.
    note_file "$dest"
    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
        rm -f "$tmp"; info "$dest unchanged"; return 1
    fi
    backup_file "$dest"
    mv "$tmp" "$dest"; chmod 644 "$dest"; info "wrote $dest"
    return 0
}
# The panels' font, served by the panels themselves: every font host worth
# using is blocked or slow from Iran. The one payload that is not text, so it
# travels as base64; its licence goes with it, as the licence asks.
install_font() {
    local dir=/usr/local/share/smart-dns tmp
    mkdir -p "$dir"
    tmp="$(mktemp)"
    if payload FONT | base64 -d > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
        note_file "$dir/Vazirmatn.woff2"
        mv "$tmp" "$dir/Vazirmatn.woff2"; chmod 644 "$dir/Vazirmatn.woff2"
    else
        rm -f "$tmp"
        warn "the panel font could not be unpacked - the pages use the system font"
    fi
    note_file "$dir/Vazirmatn-OFL.txt"
    payload FONT_LICENSE > "$dir/Vazirmatn-OFL.txt"
    chmod 644 "$dir/Vazirmatn-OFL.txt"
    # The pages' and the bot's English, for whoever presses EN.
    note_file "$dir/i18n-en.json"
    payload I18N_EN > "$dir/i18n-en.json"
    chmod 644 "$dir/i18n-en.json"
}

# Set KEY=VALUE in a shell-style config file, replacing the line if it is
# already there and appending it if not. Used for the panel's config, which the
# operator is expected to edit by hand as well.
set_env_key() {
    local file="$1" key="$2" value="$3" tmp
    tmp="$(mktemp)"
    grep -v "^${key}=" "$file" > "$tmp" 2>/dev/null || true
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    cat "$tmp" > "$file"
    rm -f "$tmp"
}

# Record a fact about this install, one "key value" per line.
remember() { mkdir -p "$STATE_DIR"; printf '%s %s\n' "$1" "$2" >> "$STATE"; }
recall()   { [ -f "$STATE" ] && awk -v k="$1" '$1 == k { $1 = ""; sub(/^ /, ""); print }' "$STATE"; }
# The same, on one line. The membership tests below look for a value with a
# space on either side, so a list separated by newlines would only ever match
# its first entry - which is exactly what went wrong: on the second run every
# file this script had created was reclassified as somebody else's.
recall_flat() { recall "$1" | tr '\n' ' '; }

# Enable a service, but only record it as ours if it was not already enabled.
# Uninstall stops what is on that list, and stopping an nginx that was serving
# somebody's website before we arrived would be a real outage caused by our
# cleanup. Restoring its config is ours to undo; its running state is not.
#
# The second argument names the package that provides the unit. Leave it out
# for units this script writes itself, which are ours by construction.
enable_service() {
    local svc="$1" pkg="${2:-}" was ours=no

    # A second run finds our own services already enabled, so the test at the
    # bottom would decide they belong to someone else and uninstall would leave
    # dnsmasq and coturn running for ever. What *this* run enabled is not the
    # question; what any run of this script enabled is.
    case " ${PREV_SERVICES:-} " in *" $svc "*) ours=yes ;; esac

    # Debian enables dnsmasq and coturn the moment they are unpacked, so by the
    # time we get here the "was it already enabled" test says yes even though
    # the package arrived thirty seconds ago on our own apt-get line. If we
    # installed the package, the service is ours.
    if [ -n "$pkg" ]; then
        case " ${NEW_PACKAGES:-} " in *" $pkg "*) ours=yes ;; esac
    else
        ours=yes
    fi

    was="$(systemctl is-enabled "$svc" 2>/dev/null || true)"
    systemctl enable "$svc" >/dev/null 2>&1 || true
    { [ "$ours" = yes ] || [ "$was" != enabled ]; } && remember services-enabled "$svc"
    return 0
}

# ------------------------------------------------------------------ tunnel
# An optional tunnel between the relay and the exit, carried by BackPack - the
# work of Amin Mohammadi (github.com/AminMGMT/BackPack, AGPL-3.0). Its binary is
# fetched from his own releases when asked for and checked against the hashes
# pinned here - never copied into this project, and never a version nobody here
# has tried.
BACKPACK_VERSION="v1.8.5"
BACKPACK_SHA_amd64="e80c4da9bdcece9e7a82305e572fdfde9a395a68a985fdd835c1af8ff248b395"
BACKPACK_SHA_arm64="9d9eaa951f8bd3f7b786be94916517c4ec900af0eeaac893b89d03d9867643f4"
BACKPACK_BIN=/usr/local/lib/smart-dns/backpack
TUNNEL_DIR=/etc/smart-dns/tunnel
TUNNEL_NFT=/etc/nftables.d/40-smartdns-tunnel.conf
# The tunnel's end on the relay, on loopback only: nginx points here, and
# nothing outside the machine can reach either port.
TUNNEL_LOCAL_HTTPS=18443
TUNNEL_LOCAL_HTTP=18080
# The sync API's end of the tunnel on the relay. Filtering between Iran and an
# exit kills a large upload on the direct path - a customer's receipt never
# arrived, while the same bytes went through the tunnel untouched - so the
# sync goes through the tunnel too when there is one.
TUNNEL_LOCAL_API=18843
# Spotify's access point, which the PlayStation app reaches on 4070 and
# nowhere else - see the nginx configs for why. It goes through the tunnel
# with everything else, because on a line where 4070 is filtered the direct
# path is exactly what is broken.
TUNNEL_LOCAL_SPOTIFY=14070
# Battle.net's launcher, on 1119 - the same story as Spotify's port.
TUNNEL_LOCAL_BLIZZARD=11119
# Which transports each direction has. A direct tunnel has four. l3 is
# BackPack's layer-3 direct tunnel - the relay dials, as in direct, but the two
# machines get a small network of their own and the ports ride on it - and
# what it has are carriers rather than transports. Its spoofing carrier wants
# addresses forged on purpose and is not offered.
TUNNEL_REVERSE_TRANSPORTS="stealth wss wssmux tcp tcpmux kcp pck quic ws wsmux xdi udp"
TUNNEL_DIRECT_TRANSPORTS="stealth wss tcp ws"
TUNNEL_L3_TRANSPORTS="xdi pck sni udp quic"

tunnel_transport_ok() {
    local list="$TUNNEL_REVERSE_TRANSPORTS"
    [ "$1" = direct ] && list="$TUNNEL_DIRECT_TRANSPORTS"
    [ "$1" = l3 ] && list="$TUNNEL_L3_TRANSPORTS"
    case " $list " in *" $2 "*) return 0 ;; esac
    return 1
}

# BackPack's performance presets - the admin panel's and smartdns-sync's
# TUNNEL_PRESETS and tunnel_tuning: the same numbers, BackPack v1.8.5's own,
# and the very same lines. None picked is the tunnel as it always was.
tunnel_preset_ok() {
    case "$3" in "") return 0 ;; esac
    case "$1:$3" in
        reverse:balance|reverse:turbo|reverse:aggressive) return 0 ;;
        reverse:throughput) [ "$2" = kcp ] ;;
        direct:balance|direct:turbo|direct:throughput) return 0 ;;
        l3:balance|l3:turbo|l3:aggressive) return 0 ;;
        *) return 1 ;;
    esac
}

# The preset's lines for one end: server or client of a reverse tunnel, or
# either end of a direct or a layer-3 one. With none, a KCP transport still
# gets its drive - Turbo's, less the error correction, which has to match at
# both ends and comes with a preset only, written at both at once.
tunnel_tuning() {
    local side="$1" p="${TUNNEL_PRESET:-}" t="$TUNNEL_TRANSPORT" v iv ack
    if [ -z "$p" ]; then
        case "$side:$t" in server:kcp|server:xdi|server:pck|client:kcp|client:xdi|client:pck)
            printf 'kcp_mtu = 1250\nkcp_interval = 10\nkcp_resend = 2\nkcp_nodelay = 1\n'
            printf 'kcp_nocongestion = 1\nkcp_sndwnd = 1024\nkcp_rcvwnd = 1024\nkcp_acknodelay = true\n' ;;
        esac
        return 0
    fi
    case "$side" in
    l3)
        case "$p" in balance) v="1024 4194304" ;; turbo) v="4096 8388608" ;; *) v="16384 33554432" ;; esac
        # shellcheck disable=SC2086
        set -- $v
        printf 'preset = "%s"\ntxqueuelen = %s\nsockbuf = %s\nqdisc = "fq_codel"\n' "$p" "$1" "$2"
        return 0 ;;
    direct)
        case "$p" in
            balance) v="32768 4194304 262144 1" ;;
            turbo) v="32768 16777216 2097152 1" ;;
            *) v="65535 33554432 16777216 4" ;;
        esac
        # shellcheck disable=SC2086
        set -- $v
        printf 'sessions = %s\nnodelay = true\nkeepalive_period = 75\nmux_framesize = %s\n' "$4" "$1"
        printf 'mux_recievebuffer = %s\nmux_streambuffer = %s\n' "$2" "$3"
        return 0 ;;
    esac
    # keepalive, heartbeat, channel size, pool, aggressive pool, socket buffers,
    # mux sessions, mux frame, mux receive and stream buffers, KCP window, parity
    case "$p" in
        balance) v="75 40 2048 4 false 4194304 4 32768 4194304 262144 512 2" ;;
        turbo) v="75 40 4096 8 false 8388608 8 32768 16777216 2097152 1024 3" ;;
        aggressive) v="60 25 8192 16 true 33554432 16 65535 33554432 16777216 2048 4" ;;
        *) v="75 40 8192 16 false 33554432 8 65535 67108864 33554432 4096 1" ;;
    esac
    # shellcheck disable=SC2086
    set -- $v
    printf 'preset = "%s"\n' "$p"
    if [ "$side" = server ]; then
        printf 'channel_size = %s\nkeepalive_period = %s\nnodelay = true\nheartbeat = %s\n' "$3" "$1" "$2"
    else
        printf 'connection_pool = %s\naggressive_pool = %s\nkeepalive_period = %s\nnodelay = true\n' "$4" "$5" "$1"
    fi
    printf 'so_rcvbuf = %s\nso_sndbuf = %s\n' "$6" "$6"
    case "$t" in tcpmux|wsmux|wssmux)
        if [ "$side" = server ]; then printf 'mux_con = %s\n' "$7"; else printf 'mux_session = %s\n' "$7"; fi
        printf 'mux_version = 2\nmux_framesize = %s\nmux_recievebuffer = %s\nmux_streambuffer = %s\n' \
               "$8" "$9" "${10}" ;;
    esac
    case "$t" in kcp|xdi|pck)
        if [ "$p" = throughput ]; then iv=20; ack=false; else iv=10; ack=true; fi
        printf 'kcp_mtu = 1250\nkcp_interval = %s\nkcp_resend = 2\nkcp_nodelay = 1\nkcp_nocongestion = 1\n' "$iv"
        printf 'kcp_sndwnd = %s\nkcp_rcvwnd = %s\nkcp_acknodelay = %s\nkcp_datashards = 10\nkcp_parityshards = %s\n' \
               "${11}" "${11}" "$ack" "${12}" ;;
    esac
}

# A relay's addresses on its layer-3 tunnel: a /30 of 10.10.0.0/16 picked by
# its own address, so the relay and the exit each work out the same block
# with nothing to pass between them, and every relay of an exit has its own -
# and its own interface beside it. The smartdns-sync and the admin panel
# compute it the same way (l3_block there).
l3_block() {
    local h n o3 o4
    h="$(printf 'master-dns-l3:%s' "$1" | sha256sum | cut -c1-4)"
    n=$((16#$h % 16384)); o3=$((n / 64)); o4=$(((n % 64) * 4))
    L3_RELAY="10.10.$o3.$((o4 + 1))"; L3_EXIT="10.10.$o3.$((o4 + 2))"; L3_IFACE="ddl$h"
}

# Why a port cannot carry the tunnel, or nothing when it can. The same ports
# the admin panel may not take, and the relay's own besides.
tunnel_port_problem() {
    local p="$1" admin
    case "$p" in *[!0-9]*|"") echo "not a number"; return 0 ;; esac
    { [ "$p" -ge 1 ] && [ "$p" -le 65535 ]; } || { echo "not a port"; return 0; }
    case "$p" in
        22) echo "ssh" ;;
        53) echo "dns" ;;
        80|443) echo "the proxy" ;;
        8443) echo "the sync API and the customer panel" ;;
        8445) echo "the bot API" ;;
        8446) echo "the exit's route to Google over IPv6" ;;
        18119) echo "the exit's route to Battle.net's version check" ;;
        1119) echo "Battle.net's launcher" ;;
        4070) echo "Spotify's access point" ;;
        8402) echo "where certificates are proved" ;;
        3478) echo "STUN on the relay" ;;
        5298) echo "the relay's page that sends addresses not let in to the customer panel" ;;
        5297|8853|8454) echo "the relay's DoH and DoT for addresses not let in" ;;
        "$TUNNEL_LOCAL_HTTPS"|"$TUNNEL_LOCAL_HTTP"|"$TUNNEL_LOCAL_API"|"$TUNNEL_LOCAL_SPOTIFY"|"$TUNNEL_LOCAL_BLIZZARD") echo "the tunnel's own end on the relay" ;;
    esac
    { [ "$p" -ge 5299 ] && [ "$p" -le 5999 ]; } && echo "the templates' resolvers on the relay"
    admin="$(sed -n 's/^ADMIN_PORT=//p' /etc/smart-dns/admin.env 2>/dev/null | head -1 || true)"
    [ -n "$admin" ] && [ "$p" = "$admin" ] && echo "the admin panel"
    return 0
}

# bp-stealth-8444-r: what the exit chose, carried to the relay inside the
# pairing token so the two ends are never set up differently.
parse_tunnel_spec() {
    local s="$1" d
    # bp-xdi-8477-l-turbo: a preset, when the exit picked one.
    TUNNEL_PRESET=""
    case "$s" in *-balance|*-turbo|*-aggressive|*-throughput) TUNNEL_PRESET="${s##*-}"; s="${s%-*}" ;; esac
    case "$s" in bp-*-*-[rdl]) ;; *) return 1 ;; esac
    s="${s#bp-}"; d="${s##*-}"; s="${s%-*}"
    TUNNEL_PORT="${s##*-}"; TUNNEL_TRANSPORT="${s%-*}"
    case "$d" in r) TUNNEL_DIRECTION=reverse ;; d) TUNNEL_DIRECTION=direct ;; *) TUNNEL_DIRECTION=l3 ;; esac
    tunnel_transport_ok "$TUNNEL_DIRECTION" "$TUNNEL_TRANSPORT" || return 1
    tunnel_preset_ok "$TUNNEL_DIRECTION" "$TUNNEL_TRANSPORT" "$TUNNEL_PRESET" || return 1
    [ -z "$(tunnel_port_problem "$TUNNEL_PORT")" ] || return 1
    TUNNEL=backpack
}

# Both ends derive the tunnel's token from the secret they already share, so
# there is nothing new to copy between them.
tunnel_token() { printf 'master-dns-tunnel:%s' "$1" | sha256sum | cut -c1-48; }

# An xdi tunnel's token is its pair's own: xdi tells its pings from another
# tunnel's by a tag made from the token, and two xdi tunnels on one machine
# with one token took each other's pings. smartdns-sync and the admin panel
# make it the same way (tunnel_token there).
tunnel_pair_token() {
    if [ "$TUNNEL_TRANSPORT" = xdi ]; then tunnel_token "$1:$RELAY_IP:$EXIT_IP"
    else tunnel_token "$1"; fi
}

ask_tunnel() {
    local a list="" i=0 t note
    # What this machine has now, when there is one, is the answer enter gives:
    # asking again with --tunnel and changing only the port should take one
    # line typed, not four.
    local d1=1 d2=1 d3=1
    [ "${CUR_TUNNEL:-}" = backpack ] && d1=2
    [ "${CUR_DIRECTION:-}" = l3 ] && d2=2
    printf '\n%sBetween the relay and this exit%s\n\n' "$B" "$N"
    if [ "${CUR_TUNNEL:-}" = backpack ]; then
        case "${CUR_DIRECTION:-}" in l3) t=direct ;; direct) t="the old direct" ;; *) t="${CUR_DIRECTION:-?}" ;; esac
        printf '  now: BackPack, %s, %s, port %s\n\n' "${CUR_TRANSPORT:-?}" "$t" "${CUR_PORT:-?}"
    elif [ -n "${CUR_TUNNEL:-}" ]; then
        printf '  now: direct TCP\n\n'
    fi
    printf '  1) direct TCP        as it has always been - nothing extra installed\n'
    printf '  2) BackPack tunnel   hides the names of the sites from filtering on the way\n\n'
    read -r -p "  choice [$d1]: " a
    case "${a:-$d1}" in 1) TUNNEL=off; return 0 ;; 2) TUNNEL=backpack ;; *) die "answer 1 or 2" ;; esac
    # Direct is BackPack's direct as its own menu makes it now - the layer-3
    # one, l3 here. The older direct engine is not offered any more; a
    # machine that has it keeps it until it is asked again.
    printf '\n  Which end dials the other?\n\n'
    printf '  1) reverse   this exit dials the relay - BackPack'"'"'s usual way\n'
    printf '  2) direct    the relay dials this exit, over a network of the two machines'"'"' own -\n'
    printf '               for where connections into Iran do not get through\n\n'
    read -r -p "  choice [$d2]: " a
    case "${a:-$d2}" in 1) TUNNEL_DIRECTION=reverse ;; 2) TUNNEL_DIRECTION=l3 ;;
        *) die "answer 1 or 2" ;; esac
    # What each transport is. How one performs depends on the route, so that is
    # not said here; only the two that did not connect at all in our own test
    # say so.
    printf '\n  Transport:\n\n'
    while IFS='|' read -r t note; do
        tunnel_transport_ok "$TUNNEL_DIRECTION" "$t" || continue
        i=$((i + 1)); list="$list $t"
        [ "$t" = "${CUR_TRANSPORT:-}" ] && d3=$i
        printf '  %2d) %-8s %s\n' "$i" "$t" "$note"
    done <<'NOTES'
stealth|encrypted, looks like random bytes - recommended
wss|looks like an ordinary HTTPS website
wssmux|the same over a few pooled connections
wsmux|websocket, pooled - not encrypted: site names show
ws|websocket - not encrypted: site names show
tcp|plain - not encrypted: site names show
tcpmux|plain and pooled - not encrypted: site names show
kcp|over UDP, for a route that loses packets
pck|for a route where TCP connects, then dies
xdi|inside ping - for where only ping gets through
sni|as pck, opening with the name of a site the route lets through
quic|over UDP - did not connect in our test
udp|raw datagrams, no reliability - did not connect in our test
NOTES
    printf '\n'
    read -r -p "  choice [$d3]: " a
    a="${a:-$d3}"
    case "$a" in *[!0-9]*) die "answer with the number" ;; esac
    # shellcheck disable=SC2086
    TUNNEL_TRANSPORT="$(echo $list | cut -d' ' -f"$a")"
    [ -n "$TUNNEL_TRANSPORT" ] || die "there is no transport number $a"
    while :; do
        read -r -p "  tunnel port [${CUR_PORT:-8444}]: " a
        a="${a:-${CUR_PORT:-8444}}"
        t="$(tunnel_port_problem "$a")"
        [ -z "$t" ] && { TUNNEL_PORT="$a"; break; }
        warn "port $a cannot carry the tunnel: $t - pick another"
    done
    # The preset: BackPack's own, those this kind of tunnel has.
    list=""; i=0; d3=1
    printf '\n  Performance preset (both ends get it):\n\n'
    while IFS='|' read -r t note; do
        [ -z "$t" ] || tunnel_preset_ok "$TUNNEL_DIRECTION" "$TUNNEL_TRANSPORT" "$t" || continue
        i=$((i + 1)); list="$list ${t:--}"
        [ "$t" = "${CUR_PRESET:-}" ] && d3=$i
        printf '  %2d) %-11s %s\n' "$i" "${t:-none}" "$note"
    done <<'PRESETS'
|as it has always been
balance|light on memory - a small server
turbo|BackPack's choice for most routes
aggressive|the most memory and speed
throughput|heavy downloads
PRESETS
    printf '\n'
    read -r -p "  choice [$d3]: " a
    a="${a:-$d3}"
    case "$a" in *[!0-9]*) die "answer with the number" ;; esac
    # shellcheck disable=SC2086
    TUNNEL_PRESET="$(echo $list | cut -d' ' -f"$a")"
    [ -n "$TUNNEL_PRESET" ] || die "there is no preset number $a"
    [ "$TUNNEL_PRESET" = - ] && TUNNEL_PRESET=""
    if [ "$TUNNEL_DIRECTION" = reverse ]; then
        info "open port $TUNNEL_PORT to this exit in the relay's firewall, if it has one"
    elif [ "$TUNNEL_TRANSPORT" = xdi ]; then
        info "xdi needs no port opened - only ping, both ways"
    else
        info "open port $TUNNEL_PORT to the relay in this exit's firewall, if it has one"
    fi
}

# Fetch the pinned BackPack, or take it from BACKPACK_TARBALL. Refuses anything
# whose hash does not match. Returns non-zero, having said why, on failure.
install_backpack() {
    local arch sha tmp
    case "$(uname -m)" in
        x86_64|amd64) arch=amd64 ;;
        aarch64|arm64) arch=arm64 ;;
        *) warn "BackPack has no build for $(uname -m) in this installer"; return 1 ;;
    esac
    eval "sha=\$BACKPACK_SHA_$arch"
    if [ -x "$BACKPACK_BIN" ] && [ "$(cat "$BACKPACK_BIN.version" 2>/dev/null)" = "$BACKPACK_VERSION $sha" ]; then
        info "BackPack $BACKPACK_VERSION already here"
        return 0
    fi
    tmp="$(mktemp -d)"
    if [ -n "${BACKPACK_TARBALL:-}" ]; then
        cp "$BACKPACK_TARBALL" "$tmp/bp.tgz" || { warn "cannot read $BACKPACK_TARBALL"; rm -rf "$tmp"; return 1; }
    elif [ -n "${BACKPACK_QUIET:-}" ] && ! curl -fsSL --connect-timeout 15 -m 180 -o "$tmp/bp.tgz" \
            "https://github.com/AminMGMT/BackPack/releases/download/$BACKPACK_VERSION/backpack_linux_$arch.tar.gz"; then
        info "BackPack could not be downloaded from GitHub - a tunnel set from the admin"
        info "panel needs it here. Later, run this again, or with BACKPACK_TARBALL=/path/to/"
        info "backpack_linux_$arch.tar.gz ($BACKPACK_VERSION) fetched elsewhere."
        rm -rf "$tmp"; return 1
    elif [ -z "${BACKPACK_QUIET:-}" ] && ! curl -fsSL -m 300 -o "$tmp/bp.tgz" \
            "https://github.com/AminMGMT/BackPack/releases/download/$BACKPACK_VERSION/backpack_linux_$arch.tar.gz"; then
        warn "could not download BackPack from GitHub. Without internet, fetch"
        warn "backpack_linux_$arch.tar.gz ($BACKPACK_VERSION) elsewhere and run with"
        warn "    BACKPACK_TARBALL=/path/to/it"
        rm -rf "$tmp"; return 1
    fi
    if [ "$(sha256sum "$tmp/bp.tgz" | cut -d' ' -f1)" != "$sha" ]; then
        warn "that BackPack archive does not match the hash pinned for $BACKPACK_VERSION - not installing it"
        rm -rf "$tmp"; return 1
    fi
    tar -xzf "$tmp/bp.tgz" -C "$tmp" 2>/dev/null
    [ -f "$tmp/backpack" ] || { warn "no backpack binary in that archive"; rm -rf "$tmp"; return 1; }
    mkdir -p "$(dirname "$BACKPACK_BIN")"
    note_file "$BACKPACK_BIN"
    note_file "$BACKPACK_BIN.version"
    install -m 755 "$tmp/backpack" "$BACKPACK_BIN"
    printf '%s %s\n' "$BACKPACK_VERSION" "$sha" > "$BACKPACK_BIN.version"
    rm -rf "$tmp"
    info "BackPack $BACKPACK_VERSION installed, its hash checked"
    # A tunnel already running goes on running the old binary - the file
    # replaced under it - until it is restarted: a relay upgraded to v1.8.5
    # was still on v1.8.0 four days later, stuck the way v1.8.1 had fixed.
    local unit
    for unit in $(systemctl list-units --plain --no-legend --state=active 'smartdns-tunnel*' \
                  2>/dev/null | awk '{print $1}'); do
        systemctl restart "$unit" && info "$unit restarted on the new BackPack"
    done
    info "BackPack is the work of Amin Mohammadi - github.com/AminMGMT/BackPack (AGPL-3.0)"
}

# The relay's way to the exit, which its nginx.conf includes: through the
# tunnel's end here first, or straight to the exit. smartdns-sync writes the
# same file, to the byte, when the admin panel turns the tunnel on or off.
EXIT_CONF=/etc/nginx/smartdns-exit.conf
CUSTOMER_EXITS=/etc/nginx/smartdns-customer-exits.map
exit_upstreams() {
    local line name here there tmp
    tmp="$(mktemp)"
    {
        printf '# The way to the exit - written by master dns: the installer, and\n'
        printf "# smartdns-sync when the admin panel turns this relay's tunnel on or off.\n"
        for line in "https $TUNNEL_LOCAL_HTTPS 443" "http $TUNNEL_LOCAL_HTTP 80" \
                    "spotify $TUNNEL_LOCAL_SPOTIFY 4070" "blizzard $TUNNEL_LOCAL_BLIZZARD 1119"; do
            read -r name here there <<<"$line"
            if [ "$TUNNEL" = backpack ]; then
                printf 'upstream to_exit_%s {\n    server 127.0.0.1:%s;\n    server %s:%s backup;\n}\n' \
                       "$name" "$here" "$EXIT_IP" "$there"
            else
                printf 'upstream to_exit_%s {\n    server %s:%s;\n}\n' "$name" "$EXIT_IP" "$there"
            fi
        done
    } > "$tmp"
    # The customers the admin panel sent through another exit: none until
    # smartdns-sync says, a few seconds after this run - never names this
    # file's new version might not have.
    note_file "$CUSTOMER_EXITS"
    printf '# Customers the admin panel sent through another exit than this relay'"'"'s:\n# written by the installer (empty) and smartdns-sync.\n' \
        > "$CUSTOMER_EXITS"
    chmod 644 "$CUSTOMER_EXITS"
    note_file "$EXIT_CONF"
    if [ -f "$EXIT_CONF" ] && cmp -s "$tmp" "$EXIT_CONF"; then rm -f "$tmp"; return 0; fi
    mv "$tmp" "$EXIT_CONF"; chmod 644 "$EXIT_CONF"
    if [ "$TUNNEL" = backpack ]; then info "nginx reaches the exit through the tunnel, directly when it is down"
    else info "nginx reaches the exit directly"; fi
}

# The tunnel's config for this end, on stdout.
tunnel_toml() {
    local token c="" k=""
    token="$(tunnel_pair_token "$1")"
    # wss on the listening end wants a certificate: the machine's own if it has
    # a domain, a self-signed one if not. The other end does not verify it -
    # BackPack proves the token inside the TLS session instead.
    case "$TUNNEL_TRANSPORT" in wss|wssmux)
        if [ -n "${PANEL_DOMAIN:-}" ] && [ -f "/etc/letsencrypt/live/$PANEL_DOMAIN/fullchain.pem" ]; then
            c="/etc/letsencrypt/live/$PANEL_DOMAIN/fullchain.pem"; k="/etc/letsencrypt/live/$PANEL_DOMAIN/privkey.pem"
        else
            c="$TUNNEL_DIR/tls.crt"; k="$TUNNEL_DIR/tls.key"
            [ -f "$c" ] || openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
                -subj "/CN=${PANEL_DOMAIN:-localhost}" -keyout "$k" -out "$c" >/dev/null 2>&1 || true
        fi ;;
    esac
    printf "# written by master dns: the installer, or the admin panel's relays card\n"
    if [ "$TUNNEL_DIRECTION" = l3 ]; then
        # The relay dials and keeps the same five ports on its loopback as
        # every other tunnel, each going to the exit's own port at the far
        # end of the network the two of them share.
        l3_block "$RELAY_IP"
        if [ "$ROLE" = relay ]; then
            printf '[l3]\nmode = "dial"\naddr = "%s:%s"\n' "$EXIT_IP" "$TUNNEL_PORT"
            printf 'local_ip = "%s/30"\npeer_ip = "%s"\n' "$L3_RELAY" "$L3_EXIT"
            printf 'ports = ["127.0.0.1:%s=443", "127.0.0.1:%s=80", "127.0.0.1:%s=8443", "127.0.0.1:%s=4070", "127.0.0.1:%s=1119"]\n' \
                   "$TUNNEL_LOCAL_HTTPS" "$TUNNEL_LOCAL_HTTP" "$TUNNEL_LOCAL_API" "$TUNNEL_LOCAL_SPOTIFY" "$TUNNEL_LOCAL_BLIZZARD"
        else
            printf '[l3]\nmode = "listen"\naddr = "0.0.0.0:%s"\n' "$TUNNEL_PORT"
            printf 'local_ip = "%s/30"\npeer_ip = "%s"\n' "$L3_EXIT" "$L3_RELAY"
        fi
        printf 'carrier = "%s"\niface = "%s"\ntoken = "%s"\n' "$TUNNEL_TRANSPORT" "$L3_IFACE" "$token"
        tunnel_tuning l3
        return 0
    fi
    if [ "$TUNNEL_DIRECTION" = reverse ] && [ "$ROLE" = relay ]; then
        printf '[server]\nbind_addr = "0.0.0.0:%s"\n' "$TUNNEL_PORT"
        printf 'ports = ["127.0.0.1:%s=443", "127.0.0.1:%s=80", "127.0.0.1:%s=8443", "127.0.0.1:%s=4070", "127.0.0.1:%s=1119"]\n' \
               "$TUNNEL_LOCAL_HTTPS" "$TUNNEL_LOCAL_HTTP" "$TUNNEL_LOCAL_API" "$TUNNEL_LOCAL_SPOTIFY" "$TUNNEL_LOCAL_BLIZZARD"
        [ -n "$c" ] && printf 'tls_cert = "%s"\ntls_key = "%s"\n' "$c" "$k"
    elif [ "$TUNNEL_DIRECTION" = reverse ]; then
        printf '[client]\nremote_addr = "%s:%s"\n' "$RELAY_IP" "$TUNNEL_PORT"
    elif [ "$ROLE" = relay ]; then
        printf '[direct]\nrole = "iran"\naddr = "%s:%s"\n' "$EXIT_IP" "$TUNNEL_PORT"
        printf 'ports = ["127.0.0.1:%s=443", "127.0.0.1:%s=80", "127.0.0.1:%s=8443", "127.0.0.1:%s=4070", "127.0.0.1:%s=1119"]\n' \
               "$TUNNEL_LOCAL_HTTPS" "$TUNNEL_LOCAL_HTTP" "$TUNNEL_LOCAL_API" "$TUNNEL_LOCAL_SPOTIFY" "$TUNNEL_LOCAL_BLIZZARD"
    else
        printf '[direct]\nrole = "kharej"\naddr = "0.0.0.0:%s"\n' "$TUNNEL_PORT"
        [ -n "$c" ] && printf 'tls_cert = "%s"\ntls_key = "%s"\n' "$c" "$k"
    fi
    printf 'transport = "%s"\ntoken = "%s"\n' "$TUNNEL_TRANSPORT" "$token"
    if [ "$TUNNEL_DIRECTION" = reverse ] && [ "$ROLE" = relay ]; then tunnel_tuning server
    elif [ "$TUNNEL_DIRECTION" = reverse ]; then tunnel_tuning client
    else tunnel_tuning direct; fi
    # The reverse engine's own extras: no web panel, no kernel tuning of its
    # own, and a log at the level journald is read at.
    if [ "$TUNNEL_DIRECTION" = reverse ]; then
        printf 'web_port = 0\nskip_optz = true\nlog_level = "info"\n'
    fi
}

# Bring this end of the tunnel to what TUNNEL says, or take it down.
apply_tunnel() {
    local secret="$1" tmp changed=0 peer
    # The units, whether there is a tunnel now or not: the admin panel can set
    # one for any relay later - the relay's own, and on the exit one per relay.
    if [ "$ROLE" = relay ]; then
        install_payload TUNNEL_SERVICE /etc/systemd/system/smartdns-tunnel.service && changed=1 || true
        # And one per node, for the tunnels the admin panel sets to them.
        install_payload TUNNEL_INSTANCE /etc/systemd/system/smartdns-tunnel@.service || true
        systemctl daemon-reload
    elif [ "$ROLE" = exit ] || [ "$ROLE" = node ]; then
        install_payload TUNNEL_INSTANCE /etc/systemd/system/smartdns-tunnel@.service || true
        systemctl daemon-reload
    fi
    if [ "${TUNNEL:-off}" != backpack ]; then
        if systemctl is-enabled --quiet smartdns-tunnel.service 2>/dev/null \
           || systemctl is-active --quiet smartdns-tunnel.service 2>/dev/null; then
            systemctl disable --now smartdns-tunnel.service >/dev/null 2>&1 || true
            info "no tunnel - the relay reaches the exit directly"
        fi
        # The whole directory: BackPack keeps its metrics beside the config.
        rm -f "$TUNNEL_NFT"
        rm -rf "$TUNNEL_DIR"
        nft delete table inet smartdns_tunnel >/dev/null 2>&1 || true
        return 0
    fi
    step "Tunnel: BackPack $BACKPACK_VERSION - $TUNNEL_TRANSPORT, $TUNNEL_DIRECTION, port $TUNNEL_PORT"
    if [ -z "$secret" ]; then
        warn "no pairing, so no tunnel - the relay reaches the exit directly"
        TUNNEL=off; return 0
    fi
    mkdir -p "$TUNNEL_DIR"; chmod 700 "$TUNNEL_DIR"
    note_file "$TUNNEL_DIR/tunnel.toml"
    tmp="$(mktemp)"
    tunnel_toml "$secret" > "$tmp"
    cmp -s "$tmp" "$TUNNEL_DIR/tunnel.toml" || changed=1
    install -m 600 "$tmp" "$TUNNEL_DIR/tunnel.toml"; rm -f "$tmp"
    # The end that listens lets the other machine in and nobody else. Loaded
    # by the service itself as well, so it holds on a machine whose nftables
    # service does not read /etc/nftables.d.
    if { [ "$ROLE" = relay ] && [ "$TUNNEL_DIRECTION" = reverse ]; } \
       || { [ "$ROLE" = exit ] && [ "$TUNNEL_DIRECTION" != reverse ]; }; then
        if [ "$ROLE" = relay ]; then peer="$EXIT_IP"; else peer="$RELAY_IP"; fi
        mkdir -p /etc/nftables.d
        note_file "$TUNNEL_NFT"
        cat > "$TUNNEL_NFT" <<EOF
# written by master dns: the tunnel's port answers $peer only
table inet smartdns_tunnel
delete table inet smartdns_tunnel
table inet smartdns_tunnel {
    chain input {
        type filter hook input priority -5 ; policy accept ;
        tcp dport $TUNNEL_PORT ip saddr != $peer drop
        udp dport $TUNNEL_PORT ip saddr != $peer drop
        meta nfproto ipv6 tcp dport $TUNNEL_PORT drop
        meta nfproto ipv6 udp dport $TUNNEL_PORT drop
    }
}
EOF
        if nft -f "$TUNNEL_NFT" 2>/dev/null; then info "port $TUNNEL_PORT answers $peer only"
        else warn "could not load the tunnel's firewall rule - port $TUNNEL_PORT is open to all"; fi
    else
        rm -f "$TUNNEL_NFT"
        nft delete table inet smartdns_tunnel >/dev/null 2>&1 || true
    fi
    install_payload TUNNEL_SERVICE /etc/systemd/system/smartdns-tunnel.service && changed=1 || true
    systemctl daemon-reload
    enable_service smartdns-tunnel.service
    if [ "$changed" = 1 ] || ! systemctl is-active --quiet smartdns-tunnel.service; then
        systemctl restart smartdns-tunnel.service
    fi
    sleep 2
    if systemctl is-active --quiet smartdns-tunnel.service; then info "tunnel service running"
    else warn "the tunnel service did not start - journalctl -u smartdns-tunnel"; fi
}

# Classify a file we are about to write. "replaced" means something was already
# there and uninstall should put it back; "created" means it is ours to delete.
# A re-run must not reclassify: once a file has been recorded as replaced, the
# original still belongs to whoever had it first, even though by now the file on
# disk is ours.
note_file() {
    local f="$1"
    case " $(recall_flat files-created) $(recall_flat files-replaced) " in
        *" $f "*) return 0 ;;
    esac
    case " ${PREV_REPLACED:-} " in
        *" $f "*) remember files-replaced "$f"; return 0 ;;
    esac
    # And a file an earlier run created is still ours to delete. Without this
    # the test below sees a file that exists, concludes it belongs to the
    # machine's owner, and uninstall then tries to restore a backup that was
    # never taken - leaving every config we wrote behind for good.
    case " ${PREV_CREATED:-} " in
        *" $f "*) remember files-created "$f"; return 0 ;;
    esac
    if [ -e "$f" ]; then remember files-replaced "$f"
    else remember files-created "$f"; fi
}

# --------------------------------------------------------------- questions?
# Answered before the preflight, because neither one touches the machine and
# neither has any business demanding root. Asking a script what version it is
# and being told to use sudo is the kind of small rudeness that makes people
# stop asking.
case "${1:-}" in
    --version|-V) printf '%s\n' "$VERSION"; exit 0 ;;
    --help|-h)
        printf 'master dns %s\n\n' "$VERSION"
        printf 'usage: sudo bash %s [--check | --uninstall | --tunnel]\n\n' "$0"
        printf '  no arguments   install or update this machine\n'
        printf '  --check        look at this machine and say what an install would meet; change nothing\n'
        printf '  --uninstall    put it back as it was\n'
        printf '  --tunnel       choose the tunnel between relay and exit again, then update\n'
        printf '  --take-over    on the standby node: become the panel, from its backup\n'
        printf '  --version      print the version of this file\n'
        printf '\nenvironment (sudo does not pass these, put them after it):\n'
        printf '  ASSUME_YES=1   take the default for every question\n'
        printf '  ENFORCE=no     leave a relay open to everyone\n'
        printf '  DELETE_DB=1    on --uninstall, delete the database too, without asking\n'
        printf '  PURGE_PACKAGES=1  on --uninstall, remove nginx, dnsmasq, coturn and certbot\n'
        printf '                 too, with their config, without asking\n'
        printf '  TUNNEL=backpack|off  TUNNEL_TRANSPORT=stealth  TUNNEL_DIRECTION=reverse|l3\n'
        printf '                 (l3 is BackPack'"'"'s direct, as its menu makes it now)\n'
        printf '  TUNNEL_PORT=8444     the tunnel between relay and exit, asked on the exit\n'
        printf '  TUNNEL_PRESET=turbo  balance|turbo|aggressive|throughput - BackPack'"'"'s, or none\n'
        printf '  ROLE=node PANEL_IP=<main exit> SYNC_TOKEN=<token>   another exit, joined to\n'
        printf '                 that panel - the token is on its admin panel'"'"'s Node page\n'
        printf '  ROLE=single PANEL_IP=<main exit> SYNC_TOKEN=<token>   a single server in\n'
        printf '                 another country, its customers and quota those of that panel\n'
        printf '  BACKPACK_TARBALL=/path/backpack_linux_amd64.tar.gz   BackPack without GitHub\n'
        exit 0 ;;
esac

# ---------------------------------------------------------------- the name
# The first thing anybody running this sees: the name, large. On one line
# where the terminal is wide enough (100 columns), on two where it is not; in
# colour on a terminal, plain in a log.
banner() {
    local cols="${COLUMNS:-}" c="" n=""
    # Each row of the name in its own shade, violet down to cyan, on a terminal
    # that has colour; the same rows, plain, in a log.
    paint() {
        local ramp=(135 99 63 69 75 81) i=0 row
        for row in "$@"; do
            if [ -n "$c" ]; then printf '\033[1;38;5;%sm%s\033[0m\n' "${ramp[i % 6]}" "$row"
            else printf '%s\n' "$row"; fi
            i=$((i + 1))
        done
    }
    case "$cols" in ""|*[!0-9]*) cols="$(tput cols 2>/dev/null || echo 80)" ;; esac
    if [ -t 1 ]; then c=$'\033[1;36m'; n=$'\033[0m'; fi
    printf '\n%s' "$c"
    if [ "$cols" -ge 100 ] 2>/dev/null; then
        paint \
        '  ███╗   ███╗  █████╗  ███████╗ ████████╗ ███████╗ ██████╗    ██████╗  ███╗   ██╗ ███████╗' \
        '  ████╗ ████║ ██╔══██╗ ██╔════╝ ╚══██╔══╝ ██╔════╝ ██╔══██╗    ██╔══██╗ ████╗  ██║ ██╔════╝' \
        '  ██╔████╔██║ ███████║ ███████╗    ██║    █████╗   ██████╔╝    ██║  ██║ ██╔██╗ ██║ ███████╗' \
        '  ██║╚██╔╝██║ ██╔══██║ ╚════██║    ██║    ██╔══╝   ██╔══██╗    ██║  ██║ ██║╚██╗██║ ╚════██║' \
        '  ██║ ╚═╝ ██║ ██║  ██║ ███████║    ██║    ███████╗ ██║  ██║    ██████╔╝ ██║ ╚████║ ███████║' \
        '  ╚═╝     ╚═╝ ╚═╝  ╚═╝ ╚══════╝    ╚═╝    ╚══════╝ ╚═╝  ╚═╝    ╚═════╝  ╚═╝  ╚═══╝ ╚══════╝'
    else
        paint \
        '  ███╗   ███╗  █████╗  ███████╗ ████████╗ ███████╗ ██████╗' \
        '  ████╗ ████║ ██╔══██╗ ██╔════╝ ╚══██╔══╝ ██╔════╝ ██╔══██╗' \
        '  ██╔████╔██║ ███████║ ███████╗    ██║    █████╗   ██████╔╝' \
        '  ██║╚██╔╝██║ ██╔══██║ ╚════██║    ██║    ██╔══╝   ██╔══██╗' \
        '  ██║ ╚═╝ ██║ ██║  ██║ ███████║    ██║    ███████╗ ██║  ██║' \
        '  ╚═╝     ╚═╝ ╚═╝  ╚═╝ ╚══════╝    ╚═╝    ╚══════╝ ╚═╝  ╚═╝'
        printf '\n'
        paint \
        '      ██████╗  ███╗   ██╗ ███████╗' \
        '      ██╔══██╗ ████╗  ██║ ██╔════╝' \
        '      ██║  ██║ ██╔██╗ ██║ ███████╗' \
        '      ██║  ██║ ██║╚██╗██║ ╚════██║' \
        '      ██████╔╝ ██║ ╚████║ ███████║' \
        '      ╚═════╝  ╚═╝  ╚═══╝ ╚══════╝'
    fi
    printf '%s\n  %ssmart DNS for Iran, version %s%s\n' "$n" "${B:-}" "$VERSION" "$n"
}
# ---------------------------------------------------------------- system check
# What this machine is, before anything is changed: a line each for the things
# that decide whether an install goes through, with a tick, a note or a cross.
# `--check` runs only this, and says so in its exit status.
syscheck() {
    local bad=0 warns=0 name ram disk addr holder port
    # shellcheck disable=SC1091
    name="$( . /etc/os-release 2>/dev/null && printf '%s|%s' "${ID:-}" "${PRETTY_NAME:-unknown}" )" || name="|unknown"
    printf '\n  %sSystem check%s\n' "$B" "$N"
    chk() { printf '    %s %-12s %s\n' "$1" "$2" "$3"; }
    case "${name%%|*}" in
        debian|ubuntu) chk "${G}+${N}" "system" "${name#*|}" ;;
        *) chk "${RD}x${N}" "system" "${name#*|} - this installer expects Debian or Ubuntu"; bad=$((bad + 1)) ;;
    esac
    if [ -d /run/systemd/system ]; then chk "${G}+${N}" "systemd" "running"
    else chk "${RD}x${N}" "systemd" "not running - the services need it"; bad=$((bad + 1)); fi
    if [ "$(id -u)" = 0 ]; then chk "${G}+${N}" "root" "yes"
    else chk "${Y}!${N}" "root" "no - the install itself needs sudo"; warns=$((warns + 1)); fi
    ram="$(awk '/^MemTotal/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null)" || ram=""
    if [ "${ram:-0}" -ge 512 ]; then chk "${G}+${N}" "memory" "${ram} MB"
    else chk "${Y}!${N}" "memory" "${ram:-?} MB - a small server; it will run, with little room"; warns=$((warns + 1)); fi
    disk="$(df -Pm / 2>/dev/null | awk 'NR == 2 {print $4}')" || disk=""
    if [ "${disk:-0}" -ge 2000 ]; then chk "${G}+${N}" "disk" "${disk} MB free"
    else chk "${Y}!${N}" "disk" "${disk:-?} MB free - the database and logs grow"; warns=$((warns + 1)); fi
    addr="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')" || addr=""
    if [ -n "$addr" ]; then chk "${G}+${N}" "address" "$addr"
    else chk "${Y}!${N}" "address" "no IPv4 route found"; warns=$((warns + 1)); fi
    for port in 53 80 443; do
        holder="$(ss -Hltnup "sport = :$port" 2>/dev/null | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' | head -n 1)" || holder=""
        case "$holder" in
            ""|nginx|dnsmasq) chk "${G}+${N}" "port $port" "${holder:+held by $holder (it is ours to configure)}${holder:-free}" ;;
            *) chk "${Y}!${N}" "port $port" "held by $holder - the install will ask it to step aside"; warns=$((warns + 1)) ;;
        esac
    done
    printf '    %s%d to look at, %d blocking%s\n' "$D" "$warns" "$bad" "$N"
    [ "$bad" -eq 0 ]
}


# Not for -V and -h, answered above: a script asking wants only the answer.
case "${1:-}" in
    -V|--version|-h|--help) ;;
    --check) banner; syscheck; exit $? ;;
    *) banner; syscheck || true ;;
esac

# ---------------------------------------------------------------- preflight
[ "$(id -u)" = 0 ] || die "run as root:  sudo bash $0"
[ -r "$SELF" ] && [ -n "$(payload SYSCTL)" ] || die "cannot read my own payloads.
    Download this file and run it directly. Piping it into bash will not work,
    because the configs are stored inside the script itself."
# A download that stopped early is still a runnable script. Everything below
# `exit 0` is a comment, so bash parses half a file quite happily and would
# then set the machine up with configs silently missing - which is worse than
# not running at all. A whole one ends on one exact line the build writes after
# the last payload, and only an exact match will do: a cut that landed just
# after the terminator of a payload in the middle, or half way through one -
# "#__END_ACL_SAVE_S" - passed the looser test this used to be, and went on to
# install with twenty configs missing.
[ "$(tail -n 1 "$SELF")" = "#__MASTER_DNS_COMPLETE__" ] || die "this file is incomplete - the
    download stopped early. Fetch it again:
        curl -fsSLO https://raw.githubusercontent.com/AlsoAryan/Master-dns/main/master-dns.sh"
command -v apt-get >/dev/null 2>&1 || die "this installer expects Debian or Ubuntu"

# ---------------------------------------------------------------- uninstall
# Undoes exactly what the state file says this script did, and nothing else.
# Anything it is unsure about is left alone and reported, because a leftover
# file is a nuisance while a wrongly deleted one is an outage.
uninstall() {
    [ -f "$STATE" ] || die "no record of an install at $STATE.
    Either this machine was never set up by this script, or the state file is
    gone. Refusing to guess what to remove."

    local role packages
    role="$(recall role)"
    packages="$(recall packages-installed)"

    printf '\n%sAbout to remove the smart DNS from this machine.%s\n\n' "$B" "$N"
    printf '    installed as : %s on %s\n' "$role" "$(recall installed-at)"
    printf '    will restore : nginx config, and stop the services set up here\n'
    printf '    will delete  : the config files, helper commands and timers added\n'
    if [ -n "$packages" ]; then
        printf '    will NOT remove these packages, unless you ask for it below:\n'
        printf '                   %s\n' "$packages"
    fi
    printf '    backups kept : %s\n\n' "$BACKUP_DIR"
    if [ -z "${ASSUME_YES:-}" ]; then
        read -r -p "  proceed? [y/N]: " ok
        case "$ok" in y|Y|yes) ;; *) die "cancelled" ;; esac
    fi
    # The database stays unless the admin says otherwise: the customers, their
    # balances and usage, the admins and every setting. Kept, installing here
    # again picks it all up; deleted, the next install starts from nothing.
    local forget_db=""
    if [ -f "$STATE_DIR/panel.db" ]; then
        if [ "${DELETE_DB:-}" = 1 ]; then
            forget_db=1
        elif [ -z "${ASSUME_YES:-}" ]; then
            printf '\n  The database - customers, plans, admins and every setting - is kept, so\n'
            printf '  installing here again picks it all up. Delete it too, to start from nothing?\n'
            read -r -p "  delete the database? [y/N]: " ok
            case "$ok" in y|Y|yes) forget_db=1 ;; esac
        fi
    fi
    # The packages too, if the admin wants: what this install added, and the
    # servers it sets up even if an earlier install left them behind (the
    # record of what was new is only true the first time). Never the
    # machine's own python3, curl, openssl or nftables.
    local purge="" purge_list="" pkg
    for pkg in nginx nginx-common nginx-core libnginx-mod-stream dnsmasq coturn certbot \
               python3-certbot-dns-cloudflare wireguard-tools qrencode $packages; do
        case " python3 curl openssl nftables $purge_list " in *" $pkg "*) continue ;; esac
        dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" \
            && purge_list="$purge_list $pkg"
    done
    purge_list="$(echo $purge_list)"
    if [ -z "$purge_list" ]; then
        :
    elif [ "${PURGE_PACKAGES:-}" = 1 ]; then
        purge=1
    elif [ -z "${ASSUME_YES:-}" ]; then
        printf '\n  Remove these packages too, with their config?\n'
        printf '      %s\n' "$purge_list"
        printf '  Anything else on this machine using them goes with them.\n'
        read -r -p "  remove the packages? [y/N]: " ok
        case "$ok" in y|Y|yes) purge=1 ;; esac
    fi

    step "Stopping services"
    local svc
    for svc in $(recall services-enabled); do
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" >/dev/null 2>&1 || true
        info "stopped and disabled $svc"
    done
    # The Telegram bot is switched on from the admin panel, not by this script,
    # so it is not on the list above - and it holds the bot's token.
    if systemctl is-enabled --quiet master-dns-bot.service 2>/dev/null             || [ -f /etc/master-dns-bot.env ]; then
        systemctl disable --now master-dns-bot.service >/dev/null 2>&1 || true
        rm -f /etc/master-dns-bot.env
        info "stopped the Telegram bot and removed its settings"
    fi
    # The resellers' bots, one unit of the template each.
    local sb
    for sb in /etc/master-dns-bot-*.env; do
        [ -f "$sb" ] || continue
        sb=${sb#/etc/master-dns-bot-}; sb=${sb%.env}
        systemctl disable --now "master-dns-bot@$sb.service" >/dev/null 2>&1 || true
        rm -f "/etc/master-dns-bot-$sb.env"
        info "stopped reseller $sb's Telegram bot and removed its settings"
    done
    rm -f /etc/systemd/system/master-dns-bot@.service

    step "Removing files this install created"
    local f
    for f in $(recall files-created); do
        if [ -e "$f" ]; then rm -f "$f"; info "removed $f"; fi
    done

    step "Restoring files this install replaced"
    for f in $(recall files-replaced); do
        # The oldest backup is the state the machine was in before we touched
        # it; later ones are just our own edits over time.
        local original
        # `|| true` is load-bearing. Under `set -e` with pipefail, a glob that
        # matches nothing makes ls exit non-zero and takes the whole uninstall
        # down without a word, halfway through - which is precisely how the
        # missing carry-over below first showed itself.
        original="$(ls -1 "$BACKUP_DIR/$(basename "$f")".* 2>/dev/null | head -1 || true)"
        if [ -n "$original" ] && [ -f "$original" ]; then
            cp -a "$original" "$f"; info "restored $f from $(basename "$original")"
        else
            warn "no backup found for $f - left as it is"
        fi
    done

    step "Swap"
    # Only a swap file this script created, and only if it is still the one
    # recorded - never a swap file that was already on the machine.
    if [ -n "$(recall swapfile)" ] && [ -f /swapfile ]; then
        swapoff /swapfile 2>/dev/null || true
        sed -i '\#^/swapfile #d' /etc/fstab 2>/dev/null || true
        rm -f /swapfile
        info "removed the swap file this installer created"
    fi

    step "Removing the firewall table"
    export PATH="$PATH:/usr/sbin"
    if nft list table inet smartdns >/dev/null 2>&1; then
        nft delete table inet smartdns; info "removed the nftables table"
    fi
    if nft list table inet smartdns_tunnel >/dev/null 2>&1; then
        nft delete table inet smartdns_tunnel; info "removed the tunnel's firewall table"
    fi
    # WireGuard, which smartdns-sync brought up when the admin panel said so.
    if ip link show wg0 >/dev/null 2>&1; then
        ip link del wg0 && info "removed WireGuard (wg0)"
    fi
    nft delete table inet smartdns_wg >/dev/null 2>&1 || true
    rm -f /etc/nftables.d/42-smartdns-wg.conf
    rm -f /etc/modules-load.d/smartdns-conntrack.conf
    rm -rf /etc/smart-dns/wg
    # The tunnels the admin panel set: one per relay on an exit, or a relay's
    # own when this installer did not make it. Not on the list of services.
    local t
    for t in $(systemctl list-units --all --plain --no-legend 'smartdns-tunnel@*' 2>/dev/null | awk '{print $1}'); do
        systemctl disable --now "$t" >/dev/null 2>&1 || true
        info "stopped $t"
    done
    systemctl disable --now smartdns-tunnel.service >/dev/null 2>&1 || true
    systemctl disable --now smartdns-dns-gate.service >/dev/null 2>&1 || true
    nft delete chain inet smartdns gatedns >/dev/null 2>&1 || true
    for t in $(nft list tables 2>/dev/null | awk '$3 ~ /^smartdns_tunnel_/ {print $3}'); do
        nft delete table inet "$t" && info "removed the firewall table $t"
    done
    rm -f /etc/nftables.d/41-smartdns-tunnel-*.conf
    if nft list table inet smartdns_api >/dev/null 2>&1; then
        nft delete table inet smartdns_api; info "removed the sync API's firewall table"
    fi
    # 10- is recorded in the state file and goes with the other created files.
    # 20- and 30- are not: smartdns-acl writes them at runtime, long after the
    # install, so nothing recorded them. The allowlist in 20- is worth keeping,
    # so it moves to the backups rather than being deleted - reinstalling and
    # discovering every customer's registered address is gone would be a poor
    # way to learn that uninstall is destructive.
    if [ -f /etc/nftables.d/20-smartdns-state.conf ]; then
        backup_file /etc/nftables.d/20-smartdns-state.conf
        rm -f /etc/nftables.d/20-smartdns-state.conf
        info "allowlist kept in $BACKUP_DIR"
    fi
    rm -f /etc/nftables.d/30-smartdns-enforce.conf
    rm -f /etc/nftables.d/smartdns.conf

    step "Panel"
    # /etc/smart-dns holds the bot token, the shared secret and the sync
    # certificate. Deleting them outright would mean re-pairing every relay
    # after an uninstall that was only meant to move things around, so they go
    # to the backups instead.
    if [ -d /etc/smart-dns ]; then
        mkdir -p "$BACKUP_DIR"
        cp -a /etc/smart-dns "$BACKUP_DIR/smart-dns-config.$STAMP"
        rm -rf /etc/smart-dns
        info "credentials moved to $BACKUP_DIR/smart-dns-config.$STAMP"
    fi
    # The database is the customers, their balances and their usage. Unless
    # the admin asked for it gone, it is not deleted or moved, so that
    # reinstalling on the same machine simply picks it up again. Asked for
    # gone, a last copy goes to the backups first - where no install looks.
    if [ -f "$STATE_DIR/panel.db" ] && [ -n "$forget_db" ]; then
        mkdir -p "$BACKUP_DIR"
        if python3 - "$STATE_DIR/panel.db" "$BACKUP_DIR/panel.db.$STAMP" <<'PY' 2>/dev/null
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("VACUUM INTO ?", (sys.argv[2],))
db.close()
PY
        then
            info "a last copy of the database: $BACKUP_DIR/panel.db.$STAMP"
        elif cp -a "$STATE_DIR/panel.db" "$BACKUP_DIR/panel.db.$STAMP" 2>/dev/null; then
            info "a last copy of the database: $BACKUP_DIR/panel.db.$STAMP"
        else
            warn "could not copy the database first - left where it is: $STATE_DIR/panel.db"
            forget_db=""
        fi
    elif [ -f "$STATE_DIR/panel.db" ]; then
        info "database left where it is: $STATE_DIR/panel.db"
    fi

    if [ -n "$purge" ]; then
        step "Removing the packages"
        if [ -n "$purge_list" ]; then
            systemctl stop nginx dnsmasq coturn >/dev/null 2>&1 || true
            # shellcheck disable=SC2086
            if DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq $purge_list >/dev/null 2>&1; then
                info "purged, with their config: $purge_list"
                DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y -qq >/dev/null 2>&1 || true
            else
                warn "apt could not purge them - try: apt-get purge $purge_list"
            fi
        fi
        rm -rf /usr/local/lib/smart-dns
        # Kept on purpose: a reinstall with the same domain uses them again,
        # and Let's Encrypt gives only five of the same name a week.
        [ -d /etc/letsencrypt/live ] && info "HTTPS certificates kept in /etc/letsencrypt"
    fi

    step "Restarting what is left"
    systemctl daemon-reload
    # nginx is only left running if it was already enabled before we arrived,
    # i.e. it is not on the list we just disabled. In that case it now has its
    # original config back and should be put back into service.
    case "$purge: $(recall_flat services-enabled) " in
        1:*) ;;
        *" nginx "*) info "nginx was installed here by this script - left stopped" ;;
        *)
            if nginx -t >/dev/null 2>&1; then
                systemctl restart nginx; info "nginx restarted with its original config"
            else
                warn "the restored nginx config does not parse - nginx left alone"
            fi ;;
    esac

    rm -f "$STATE"
    # The version note goes with the state it describes. Left behind, it would
    # tell a later install that this machine already runs a version whose
    # files are no longer here, and that install would skip its own upgrade
    # question on the strength of it.
    rm -f "$STATE_DIR/version"
    # Everything the panel kept beside the database goes with it: its media,
    # its standby copy, the installer it saved for upgrades.
    if [ -n "$forget_db" ]; then
        rm -rf "$STATE_DIR"
        info "database deleted - the next install here starts from nothing"
    fi
    # Only if nothing else put anything there; never blow away a
    # directory a later stage of this project may be using.
    rmdir "$STATE_DIR" 2>/dev/null || true
    printf '\n%sRemoved.%s Backups are still in %s if you want anything back.\n\n' "$G" "$N" "$BACKUP_DIR"
    if [ -n "$packages" ] && [ -z "$purge" ]; then
        printf '    To also remove the packages it installed:\n\n'
        printf '        apt-get purge %s\n\n' "$packages"
    fi
    exit 0
}

# --version and --help were answered above, before the preflight.
case "${1:-}" in
    --uninstall|-u|uninstall) uninstall ;;
    # Asked on the exit, carried to the relay by the pairing token - see the
    # tunnel section below.
    --tunnel|tunnel) ASK_TUNNEL=1 ;;
    # On the standby the admin panel named: become the exit with the panel,
    # from the backup kept here, when the machine it was on is gone.
    --take-over|take-over) TAKE_OVER=1 ;;
    "") ;;
    *) die "unknown argument: $1  (try --help)" ;;
esac

# ---------------------------------------------------------------- version
# Nothing below has touched the machine yet, and the answer here decides
# whether anything will. Two cases are worth stopping for: an upgrade, which
# the operator should know is happening rather than discover afterwards, and
# the reverse - an older file run over a newer install, which is nearly always
# somebody re-running a download they still had lying around.
VERSION_FILE="$STATE_DIR/version"
INSTALLED_VERSION=""
[ -f "$VERSION_FILE" ] && INSTALLED_VERSION="$(head -1 "$VERSION_FILE" | tr -d "[:space:]")" || true

# The customer database, the settings, the sync secret and any certificate all
# live outside the files this script writes, and every payload it does write is
# backed up before it is replaced. So an upgrade keeps them - but "keeps them"
# is a promise worth a copy behind it, taken before anything starts.
snapshot_db() {
    local db="$STATE_DIR/panel.db" out
    [ -f "$db" ] || return 0
    mkdir -p "$BACKUP_DIR"
    out="$BACKUP_DIR/panel.db.$STAMP"
    # VACUUM INTO, not cp: the panel keeps a write-ahead log beside the file,
    # so a plain copy of the file alone can be a database missing its newest
    # rows. Falls back to cp where sqlite is too old to know the statement.
    if python3 - "$db" "$out" <<'PY' 2>/dev/null
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("VACUUM INTO ?", (sys.argv[2],))
db.close()
PY
    then
        info "database copied to $out"
    elif cp -a "$db" "$out" 2>/dev/null; then
        warn "database copied to $out (plain copy - sqlite here is old)"
    else
        die "could not copy the database at $db. Fix that before upgrading."
    fi
}

if [ -n "$INSTALLED_VERSION" ] && [ "$INSTALLED_VERSION" != "$VERSION" ]; then
    older="$(printf '%s\n%s\n' "$INSTALLED_VERSION" "$VERSION" | sort -V | head -1)"
    printf '\n%sVersion%s\n\n' "$B" "$N"
    info "installed on this machine:  $INSTALLED_VERSION"
    info "this file:                  $VERSION"
    printf '\n'
    if [ "$older" = "$VERSION" ]; then
        warn "this file is OLDER than what is installed."
        warn "installing it will put old configs over new ones, and this"
        warn "script has no way to undo what a later version did."
        warn "the newest is at github.com/AlsoAryan/Master-dns/releases"
        answer=n
    else
        warn "this will upgrade this machine from $INSTALLED_VERSION to $VERSION."
        answer=y
    fi
    warn "your customers, settings, certificates and allowlist are kept."
    printf '\n'
    if [ -z "${ASSUME_YES:-}" ]; then
        read -r -p "  go ahead? [$answer]: " reply
        # An answer piped in from a file written on Windows arrives with
        # a carriage return attached, and a "y" with one glued on
        # matches nothing below.
        reply="$(printf '%s' "$reply" | tr -d '\r')"
        reply="${reply:-$answer}"
    else
        reply="$answer"
        info "ASSUME_YES - taking '$answer'"
    fi
    case "$reply" in
        y|Y|yes|YES) ;;
        *) printf '\n    Nothing was changed.\n\n'; exit 0 ;;
    esac
    snapshot_db
elif [ -n "$INSTALLED_VERSION" ]; then
    info "already at $VERSION - re-running to check and repair"
fi

# An upgrade asks nothing the machine already knows. Every answer the first
# install was given is still here: the state file records the role and both
# addresses on every run, and the domain sits in the config the panel serves
# from. So an upgrade reads them back, keeps the one-time choices - swap, BBR -
# exactly as they are, and the only question it has is the one above: whether
# to install this version at all. It used to walk the whole questionnaire
# again, addresses and all, as if the machine had never been set up.
# ---------------------------------------------------------------- take over
# The standby node the admin panel named becomes the exit with the panel,
# from the encrypted backup the panel kept on it: the database, the pictures'
# key, and the sync secret, key and certificate the relays pin - so they
# take this machine for the panel they had, find it on their own and stay.
STANDBY_DIR="$STATE_DIR/standby"
if [ -n "${TAKE_OVER:-}" ]; then
    RESTORE="${RESTORE:-$STANDBY_DIR/backup.enc}"
    [ -f "$RESTORE" ] || die "no panel backup at $RESTORE - is this the standby the Node page named?"
    if [ -z "${RESTORE_PASS:-}" ]; then
        read -r -s -p "  the backup password (the admin panel's settings): " RESTORE_PASS
        printf '\n'
    fi
    tmpr="$(mktemp -d)"; chmod 700 "$tmpr"
    DDNS_BACKUP_PASS="$RESTORE_PASS" openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
        -in "$RESTORE" -pass env:DDNS_BACKUP_PASS -out "$tmpr/b.tgz" 2>/dev/null \
        || { rm -rf "$tmpr"; die "that password does not open the backup"; }
    tar -xzf "$tmpr/b.tgz" -C "$tmpr" 2>/dev/null || { rm -rf "$tmpr"; die "the backup is damaged"; }
    for f in panel.db panel.env sync.key sync.crt; do
        [ -f "$tmpr/$f" ] || { rm -rf "$tmpr"; die "the backup has no $f - not a whole panel"; }
    done
    me="$(sed -n 's/^SELF_IP=//p' /etc/smart-dns/node.env 2>/dev/null | head -1 || true)"
    me="${SELF_IP:-$me}"
    # The admin panel's name has to point here already: its certificate is
    # got again below, the way it was the first time.
    dom="$(sed -n 's#^ADMIN_CERT=/etc/letsencrypt/live/\([^/]*\)/.*#\1#p' "$tmpr/admin.env" 2>/dev/null | head -1 || true)"
    if [ -n "$dom" ] && [ -n "$me" ]; then
        seen="$(getent ahostsv4 "$dom" 2>/dev/null | awk '{print $1; exit}' || true)"
        if [ "$seen" != "$me" ]; then
            rm -rf "$tmpr"
            die "point $dom at this server ($me) first - it points at ${seen:-nothing} now.
    Then run this again."
        fi
    fi
    step "Taking over the panel from its backup"
    systemctl disable --now smartdns-node.service >/dev/null 2>&1 || true
    mkdir -p /etc/smart-dns "$STATE_DIR"; chmod 700 /etc/smart-dns
    for f in panel.env sync.key sync.crt admin.env db.key; do
        [ -f "$tmpr/$f" ] && install -m 600 "$tmpr/$f" "/etc/smart-dns/$f"
    done
    [ -f "$tmpr/master-dns-bot.env" ] && install -m 600 "$tmpr/master-dns-bot.env" /etc/master-dns-bot.env
    for f in "$tmpr"/master-dns-bot-*.env; do
        [ -f "$f" ] && install -m 600 "$f" "/etc/$(basename "$f")"
    done
    install -m 600 "$tmpr/panel.db" "$STATE_DIR/panel.db"
    rm -f "$STATE_DIR/panel.db-wal" "$STATE_DIR/panel.db-shm" /etc/smart-dns/node.env
    rm -rf "$tmpr"
    # This machine is the panel's exit now, no longer one of its nodes.
    if [ -n "$me" ]; then
        set_env_key /etc/smart-dns/panel.env NODE_IP "$(sed -n 's/^NODE_IP=//p' /etc/smart-dns/panel.env \
            | head -1 | tr ',' '\n' | grep -vx "$me" | paste -sd, - || true)"
    fi
    ROLE=exit; SELF_IP="$me"
    info "the panel's database, secret and certificate are this machine's now"
fi

UPGRADE=""
if [ -n "$INSTALLED_VERSION" ]; then
    UPGRADE=1
    was() { recall "$1" 2>/dev/null | tail -1 || true; }
    ROLE="${ROLE:-$(was role)}"
    if [ -z "$ROLE" ]; then
        # Both files is a single machine; one of them, that end.
        if [ -f /etc/smart-dns/sync.env ] && [ -f /etc/smart-dns/panel.env ]; then ROLE=single
        elif grep -qx 'SINGLE=1' /etc/smart-dns/sync.env 2>/dev/null; then ROLE=single
        elif [ -f /etc/smart-dns/node.env ]; then ROLE=node
        elif [ -f /etc/smart-dns/sync.env ]; then ROLE=relay
        elif [ -f /etc/smart-dns/panel.env ]; then ROLE=exit
        fi
    fi
    if [ "$ROLE" = single ]; then
        SELF_IP="${SELF_IP:-$(was relay-ip)}"
        [ -n "$SELF_IP" ] || SELF_IP="$(sed -n 's/^SELF_IP=//p' /etc/smart-dns/sync.env 2>/dev/null | head -1 || true)"
        # One joined to another machine's panel keeps syncing there.
        if [ ! -f /etc/smart-dns/panel.env ]; then
            PANEL_IP="${PANEL_IP:-$(sed -n 's/^PANEL_HOST=//p' /etc/smart-dns/sync.env 2>/dev/null | head -1 || true)}"
        fi
    elif [ "$ROLE" = relay ]; then
        PEER_IP="${PEER_IP:-$(was exit-ip)}"
        SELF_IP="${SELF_IP:-$(was relay-ip)}"
        # Older state files, or none: the relay's own config has both.
        [ -n "$PEER_IP" ] || PEER_IP="$(sed -n 's/^PANEL_HOST=//p' /etc/smart-dns/sync.env 2>/dev/null | head -1 || true)"
        [ -n "$SELF_IP" ] || SELF_IP="$(sed -n 's/^SELF_IP=//p' /etc/smart-dns/sync.env 2>/dev/null | head -1 || true)"
    elif [ "$ROLE" = node ]; then
        SELF_IP="${SELF_IP:-$(was exit-ip)}"
        [ -n "$SELF_IP" ] || SELF_IP="$(sed -n 's/^SELF_IP=//p' /etc/smart-dns/node.env 2>/dev/null | head -1 || true)"
        PEER_IP="${PEER_IP:-$(sed -n 's/^PANEL_HOST=//p' /etc/smart-dns/node.env 2>/dev/null | head -1 || true)}"
        SYNC_TOKEN="${SYNC_TOKEN:-$(sed -n 's/^SYNC_SECRET=//p' /etc/smart-dns/node.env 2>/dev/null | head -1 || true).$(sed -n 's/^SYNC_FINGERPRINT=//p' /etc/smart-dns/node.env 2>/dev/null | head -1 || true)}"
    elif [ "$ROLE" = exit ]; then
        PEER_IP="${PEER_IP:-$(was relay-ip)}"
        SELF_IP="${SELF_IP:-$(was exit-ip)}"
        # panel.env holds every relay this exit serves, comma separated. Any of
        # them will do here: it is already on the list, so nothing is added.
        [ -n "$PEER_IP" ] || PEER_IP="$(sed -n 's/^RELAY_IP=//p' /etc/smart-dns/panel.env 2>/dev/null | head -1 | cut -d, -f1 || true)"
    fi
    PANEL_DOMAIN="${PANEL_DOMAIN:-$(was panel-domain)}"
    info "upgrading this ${ROLE:-machine} in place - nothing to answer"
fi

# ---------------------------------------------------------------- questions
valid_ip() {
    local ip="$1" part
    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    IFS='.' read -r -a part <<< "$ip"
    for n in "${part[@]}"; do [ "$n" -le 255 ] || return 1; done
}

ROLE="${ROLE:-}"; PEER_IP="${PEER_IP:-}"; SELF_IP="${SELF_IP:-}"

if [ -z "$ROLE" ]; then
    printf '\n%sWhich side is this machine?%s\n\n' "$B" "$N"
    printf '  1) relay  - the server inside Iran, the one clients point their DNS at\n'
    printf '  2) exit   - the server abroad, which reaches the blocked sites\n'
    printf '  3) single - both on one server abroad, with no relay in Iran\n'
    printf '  4) node   - another exit abroad, joined to the panel on an exit\n\n'
    while :; do
        read -r -p "  choice [1/2/3/4]: " answer
        case "$answer" in
            1|relay)  ROLE=relay;  break ;;
            2|exit)   ROLE=exit;   break ;;
            3|single) ROLE=single; break ;;
            4|node)   ROLE=node;   break ;;
            *) warn "answer 1, 2, 3 or 4" ;;
        esac
    done
fi
[ "$ROLE" = relay ] || [ "$ROLE" = exit ] || [ "$ROLE" = single ] || [ "$ROLE" = node ] \
    || die "ROLE must be relay, exit, single or node"
# A node's other end is the exit its panel is on.
[ "$ROLE" = node ] && [ -z "$PEER_IP" ] && PEER_IP="${PANEL_IP:-}"
SINGLE=""
if [ "$ROLE" = single ]; then
    SINGLE=1
    # Said once, plainly, because it is the whole trade: customers in Iran
    # reach a server abroad directly, which is what gets filtered and slowed
    # on the way, and what a relay in Iran exists to avoid.
    warn "one server abroad, no relay: customers reach it directly from Iran."
    warn "that works, but the direct path is the one that gets filtered and slowed."
    warn "for real customers a relay in Iran is better - this is for trying it out,"
    warn "or where the direct path to this server happens to be good."
fi

if [ -z "$PEER_IP" ] && [ "$ROLE" != single ]; then
    printf '\n'
    if [ "$ROLE" = relay ]; then
        read -r -p "  public address of the EXIT server abroad: " PEER_IP
    elif [ "$ROLE" = node ]; then
        read -r -p "  public address of the exit with the PANEL: " PEER_IP
    else
        read -r -p "  public address of the RELAY server in Iran: " PEER_IP
    fi
fi
[ "$ROLE" = single ] || valid_ip "$PEER_IP" || die "'$PEER_IP' is not an IPv4 address"

if [ -z "$SELF_IP" ]; then
    guess="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
    printf '\n'
    read -r -p "  public address of THIS server [${guess}]: " SELF_IP
    SELF_IP="${SELF_IP:-$guess}"
fi
valid_ip "$SELF_IP" || die "'$SELF_IP' is not an IPv4 address"
[ "$ROLE" = single ] || [ "$SELF_IP" != "$PEER_IP" ] || die "both addresses are the same"
if is_exit; then exit_owner_check "$SELF_IP"; fi

# A single machine joined to the panel on another one (PANEL_IP): its
# customers, their quota and its settings are that panel's, and it has no
# panel of its own.
JOINED=""
if [ "$ROLE" = single ]; then
    RELAY_IP="$SELF_IP"; EXIT_IP="$SELF_IP"; PEER_IP="$SELF_IP"
    if [ -n "${PANEL_IP:-}" ] && [ "$PANEL_IP" != 127.0.0.1 ] && [ "$PANEL_IP" != "$SELF_IP" ]; then
        JOINED=1
        valid_ip "$PANEL_IP" || die "PANEL_IP '$PANEL_IP' is not an IPv4 address"
        if [ -z "${SYNC_TOKEN:-}" ] && [ -z "${ASSUME_YES:-}" ]; then
            printf '\n  The pairing token is on the admin panel'"'"'s Node page, on %s.\n' "$PANEL_IP"
            read -r -p "  pairing token: " SYNC_TOKEN
        fi
        [ -n "${SYNC_TOKEN:-}" ] || [ -f /etc/smart-dns/sync.env ] \
            || die "a single server joined to a panel needs the pairing token from its Node page"
    fi
elif [ "$ROLE" = relay ]; then
    RELAY_IP="$SELF_IP"; EXIT_IP="$PEER_IP"
elif [ "$ROLE" = node ]; then
    # The relays come from the panel; the panel is the other address.
    RELAY_IP=""; EXIT_IP="$SELF_IP"; PANEL_IP="$PEER_IP"
    if [ -z "${SYNC_TOKEN:-}" ] && [ -z "${ASSUME_YES:-}" ]; then
        printf '\n  The pairing token is on the admin panel'"'"'s Node page, on %s.\n' "$PEER_IP"
        read -r -p "  pairing token: " SYNC_TOKEN
    fi
    [ -n "${SYNC_TOKEN:-}" ] || die "a node needs the pairing token from the admin panel's Node page"
else
    RELAY_IP="$PEER_IP"; EXIT_IP="$SELF_IP"
fi
# Where dnsmasq and coturn listen. The public address, as it always was -
# unless it is not on any interface here, as on a cloud machine behind NAT
# (AWS and many others, where a single machine is likely to be): then they
# listen on the private address the public one is mapped to, and coturn is
# told the public one to hand out. DNS answers name the public address either
# way; that is what customers connect to.
LISTEN_IP="$RELAY_IP"; TURN_EXTERNAL="$RELAY_IP"
if is_relay && ! ip -4 -o addr show 2>/dev/null | grep -qw "inet $RELAY_IP"; then
    local_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
    if [ -n "$local_ip" ] && [ "$local_ip" != "$RELAY_IP" ]; then
        LISTEN_IP="$local_ip"; TURN_EXTERNAL="$RELAY_IP/$local_ip"
        info "$RELAY_IP is not on this machine - listening on $local_ip, which it is mapped to"
    fi
fi

# ------------------------------------------------------------------ panel
# The panel is optional. Someone who only wants the bypass can leave these
# blank and still get a working pair; the questions are asked here rather than
# halfway through the install so that the whole thing runs unattended after
# this point.
#
# The panel lives on the exit node: it holds the database every relay syncs to,
# and one database is what makes a customer's allowance mean the same thing on
# all of them. The exit builds it unasked; the relay asks for the pairing token
# the exit prints at the end of its own install.
if [ "$ROLE" = relay ] && [ -z "${SYNC_TOKEN:-}" ] && [ -z "${ASSUME_YES:-}" ] && [ -z "$UPGRADE" ]; then
    printf '\n%sPanel%s (optional - press enter to skip)\n\n' "$B" "$N"
    printf '  The exit server prints a pairing token at the end of its install.\n'
    read -r -p "  pairing token: " SYNC_TOKEN
fi
# ------------------------------------------------------------------- TLS
# Optional, like the panel. Without it the claim link is plain http, which
# works but sends the registration token in the clear - anyone on the path can
# take it and register their own address against the user's account.
if [ -z "${PANEL_DOMAIN:-}" ] && [ -z "${ASSUME_YES:-}" ] && [ -z "$UPGRADE" ] && [ "$ROLE" != node ]; then
    printf '\n%sHTTPS%s (optional - press enter to skip)\n\n' "$B" "$N"
    if [ "$ROLE" = single ]; then
        printf '  A name pointing at this machine, for both panels and for DNS\n'
        printf '  over HTTPS and TLS.\n'
    elif [ "$ROLE" = relay ]; then
        printf '  A name pointing at this machine, for the page users open to\n'
        printf '  register their address.\n'
    else
        printf '  A name pointing at this machine, for the admin panel.\n'
    fi
    read -r -p "  domain: " PANEL_DOMAIN
    # Nothing else is asked. The certificate is obtained automatically and the
    # only thing that proves anything is the domain itself - no DNS token, no
    # account, nothing to hand over.
    if [ -n "$PANEL_DOMAIN" ]; then
        printf '\n  A certificate will be obtained for that name automatically.\n'
        printf '  Point the record at this machine first and leave port 80\n'
        printf '  reachable from the internet - that is how it is checked.\n'
    fi
fi

# ------------------------------------------------------------------ tunnel
# How the relay reaches the exit: straight, as it always has, or through a
# BackPack tunnel that hides the names of the sites from filtering on the way.
# The exit is asked, because it is installed first; the relay learns the
# answer from the pairing token, so the two ends cannot disagree. A re-run
# keeps whatever this machine was set up with.
TUNNEL="${TUNNEL:-}"
TUNNEL_SPEC=""
TUNNEL_OUT=""
env_get() { sed -n "s/^$2=//p" "$1" 2>/dev/null | head -1 || true; }
# --tunnel: ask again on a machine that is already set up. The exit shows the
# menu with what it has now as the defaults; the relay asks for the exit's new
# pairing token, which carries the answer.
if [ -n "${ASK_TUNNEL:-}" ] && [ -z "$TUNNEL" ]; then
    if [ "$ROLE" = exit ]; then
        CUR_TUNNEL="$(env_get /etc/smart-dns/panel.env TUNNEL)"
        CUR_TRANSPORT="$(env_get /etc/smart-dns/panel.env TUNNEL_TRANSPORT)"
        CUR_DIRECTION="$(env_get /etc/smart-dns/panel.env TUNNEL_DIRECTION)"
        CUR_PORT="$(env_get /etc/smart-dns/panel.env TUNNEL_PORT)"
        CUR_PRESET="$(env_get /etc/smart-dns/panel.env TUNNEL_PRESET)"
        ask_tunnel
    elif [ -z "${SYNC_TOKEN:-}" ]; then
        printf '\n%sTunnel%s\n\n' "$B" "$N"
        printf '  Run the installer with --tunnel on the exit first. It prints a new\n'
        printf '  pairing token that carries its answer: paste it here, or press enter\n'
        printf '  to keep the tunnel this relay has now.\n\n'
        read -r -p "  pairing token: " SYNC_TOKEN
    fi
fi
# One machine has nothing to tunnel between.
{ [ "$ROLE" = single ] || [ "$ROLE" = node ]; } && TUNNEL=off
if [ -z "$TUNNEL" ]; then
    if [ "$ROLE" = exit ] && [ -n "$(env_get /etc/smart-dns/panel.env TUNNEL)" ]; then
        TUNNEL="$(env_get /etc/smart-dns/panel.env TUNNEL)"
        TUNNEL_TRANSPORT="${TUNNEL_TRANSPORT:-$(env_get /etc/smart-dns/panel.env TUNNEL_TRANSPORT)}"
        TUNNEL_DIRECTION="${TUNNEL_DIRECTION:-$(env_get /etc/smart-dns/panel.env TUNNEL_DIRECTION)}"
        TUNNEL_PORT="${TUNNEL_PORT:-$(env_get /etc/smart-dns/panel.env TUNNEL_PORT)}"
        TUNNEL_PRESET="${TUNNEL_PRESET-$(env_get /etc/smart-dns/panel.env TUNNEL_PRESET)}"
    elif [ "$ROLE" = relay ]; then
        spec="$(printf '%s' "${SYNC_TOKEN:-}" | cut -s -d. -f3)"
        if [ -n "$spec" ]; then
            parse_tunnel_spec "$spec" || die "the tunnel part of the pairing token, '$spec', is not one this installer knows.
    Install the exit and the relay from the same version of this file."
        elif [ -n "${SYNC_TOKEN:-}" ]; then
            TUNNEL=off          # a two-part token: the exit has no tunnel
        elif [ -n "$(env_get /etc/smart-dns/sync.env TUNNEL)" ]; then
            TUNNEL="$(env_get /etc/smart-dns/sync.env TUNNEL)"
            TUNNEL_TRANSPORT="${TUNNEL_TRANSPORT:-$(env_get /etc/smart-dns/sync.env TUNNEL_TRANSPORT)}"
            TUNNEL_DIRECTION="${TUNNEL_DIRECTION:-$(env_get /etc/smart-dns/sync.env TUNNEL_DIRECTION)}"
            TUNNEL_PORT="${TUNNEL_PORT:-$(env_get /etc/smart-dns/sync.env TUNNEL_PORT)}"
            TUNNEL_PRESET="${TUNNEL_PRESET-$(env_get /etc/smart-dns/sync.env TUNNEL_PRESET)}"
        fi
    fi
fi
if [ "$ROLE" = exit ] && [ -z "$TUNNEL" ] && [ -z "${ASSUME_YES:-}" ] && [ -z "$UPGRADE" ]; then
    ask_tunnel
fi
case "${TUNNEL:-off}" in
    off|no|direct|"") TUNNEL=off ;;
    backpack|on|yes) TUNNEL=backpack ;;
    *) die "TUNNEL must be backpack or off" ;;
esac
if [ "$TUNNEL" = backpack ]; then
    TUNNEL_DIRECTION="${TUNNEL_DIRECTION:-reverse}"
    TUNNEL_TRANSPORT="${TUNNEL_TRANSPORT:-stealth}"
    TUNNEL_PORT="${TUNNEL_PORT:-8444}"
    case "$TUNNEL_DIRECTION" in reverse|direct|l3) ;; *) die "TUNNEL_DIRECTION must be reverse, direct or l3" ;; esac
    tunnel_transport_ok "$TUNNEL_DIRECTION" "$TUNNEL_TRANSPORT" \
        || die "BackPack's $TUNNEL_DIRECTION tunnel has no transport called '$TUNNEL_TRANSPORT'"
    tunnel_preset_ok "$TUNNEL_DIRECTION" "$TUNNEL_TRANSPORT" "${TUNNEL_PRESET:-}" \
        || die "BackPack's $TUNNEL_DIRECTION tunnel on $TUNNEL_TRANSPORT has no preset called '$TUNNEL_PRESET'"
    why="$(tunnel_port_problem "$TUNNEL_PORT")"
    [ -z "$why" ] || die "port $TUNNEL_PORT cannot carry the tunnel: $why"
    # The tunnel runs between this relay and its own exit, on a secret only
    # that exit knows - a relay whose panel is on another machine has none.
    if [ "$ROLE" = relay ] && [ -n "${PANEL_IP:-}" ] && [ "$PANEL_IP" != "$EXIT_IP" ]; then
        warn "the panel is on $PANEL_IP, not on this relay's exit - no tunnel"
        TUNNEL=off
    fi
fi
[ "$TUNNEL" = backpack ] && TUNNEL_SPEC="bp-$TUNNEL_TRANSPORT-$TUNNEL_PORT-$(printf '%.1s' "$TUNNEL_DIRECTION")${TUNNEL_PRESET:+-$TUNNEL_PRESET}"
[ "$TUNNEL" = backpack ] || TUNNEL_PRESET=""
if [ "$TUNNEL" = backpack ]; then
    case "$TUNNEL_DIRECTION" in l3) d=direct ;; direct) d="the old direct" ;; *) d="$TUNNEL_DIRECTION" ;; esac
    TUNNEL_OUT="BackPack, $TUNNEL_TRANSPORT, $d, port $TUNNEL_PORT${TUNNEL_PRESET:+, $TUNNEL_PRESET}"
else
    TUNNEL_OUT="none - the relay reaches the exit directly"
fi

printf '\n%sAbout to configure:%s\n' "$B" "$N"
if [ "$ROLE" = node ]; then
    printf '    role   : node\n    this   : %s\n    panel  : %s\n\n' "$EXIT_IP" "$PANEL_IP"
else
    printf '    role   : %s\n    relay  : %s\n    exit   : %s\n    tunnel : %s\n\n' "$ROLE" "$RELAY_IP" "$EXIT_IP" "$TUNNEL_OUT"
fi
if [ -z "${ASSUME_YES:-}" ] && [ -z "$UPGRADE" ]; then
    read -r -p "  proceed? [y/N]: " ok
    case "$ok" in y|Y|yes) ;; *) die "cancelled" ;; esac
fi

export DEBIAN_FRONTEND=noninteractive
NGINX_CHANGED=0
DNSMASQ_CHANGED=0

# What the run has to tell the operator at the end. Empty here so that the
# summary can read them plainly under `set -u`, whichever paths ran. One of
# these was left unset when the customer panel stopped being served without a
# certificate, and the install died on its very last line - after doing all of
# its work, and before recording that it had.
ADMIN_URL_OUT=""
ADMIN_PASS_OUT=""
ADMIN_USER_OUT=""
SYNC_TOKEN_OUT=""
USER_PANEL_OUT=""
ENFORCE_OUT=""

# Start the record over, but keep what an earlier install already knew: which
# packages were new and which files existed before we ever touched them. Those
# facts are only true the first time, and losing them would make a later
# uninstall unable to tell "we added this" from "this was already here".
mkdir -p "$STATE_DIR"
# Everything an earlier run recorded, read before the state file is rewritten.
# The record has to survive re-installation: by the second run our own files
# exist and our own services are enabled, so a fresh look at the machine can no
# longer tell our work from the owner's.
PREV_PACKAGES="$(recall_flat packages-installed || true)"
PREV_REPLACED="$(recall_flat files-replaced || true)"
PREV_CREATED="$(recall_flat files-created || true)"
PREV_SERVICES="$(recall_flat services-enabled || true)"
: > "$STATE"
remember role "$ROLE"
remember relay-ip "$RELAY_IP"
remember exit-ip "$EXIT_IP"
remember installed-at "$(date -Is)"

# ---------------------------------------------------------------- packages
step "Installing packages"
if [ "$ROLE" = single ]; then
    WANT="nginx libnginx-mod-stream dnsmasq coturn nftables dnsutils python3 curl openssl qrencode"
elif [ "$ROLE" = relay ]; then
    # wireguard-tools for WireGuard beside the DNS, when the admin panel turns
    # it on: the kernel has WireGuard, this is the wg command that drives it.
    WANT="nginx libnginx-mod-stream dnsmasq coturn nftables dnsutils python3 curl wireguard-tools"
else
    # nftables for the rule that keeps strangers off the sync API.
    # qrencode for the WireGuard config's QR code the panel hands customers.
    WANT="nginx libnginx-mod-stream dnsutils curl python3 openssl nftables qrencode"
fi
# Note what was missing beforehand, so uninstall can name exactly what this
# script added rather than offering to purge nginx from a web server.
if [ -n "$PREV_PACKAGES" ]; then
    NEW_PACKAGES="$(echo "$PREV_PACKAGES" | xargs || true)"
else
    NEW_PACKAGES=""
    for pkg in $WANT; do
        dpkg -s "$pkg" >/dev/null 2>&1 || NEW_PACKAGES="$NEW_PACKAGES $pkg"
    done
    NEW_PACKAGES="$(echo "$NEW_PACKAGES" | xargs || true)"
fi
[ -n "$NEW_PACKAGES" ] && remember packages-installed "$NEW_PACKAGES"
# Only what is missing, and apt is not touched at all when nothing is. It used
# to run apt-get update and reinstall the whole list on every run, which made
# an upgrade slow and quietly upgraded the operator's nginx along the way -
# neither of which an upgrade of this service was asked to do. dpkg-query, not
# dpkg -s: a package removed but not purged still answers dpkg -s happily.
missing=""
for pkg in $WANT; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" || missing="$missing $pkg"
done
if [ -n "$missing" ]; then
    apt-get update -qq
    # shellcheck disable=SC2086
    apt-get install -y -qq $missing >/dev/null
    info "installed:$missing"
else
    info "all present"
fi

# ---------------------------------------------------------------- kernel
# ------------------------------------------------------------------- swap
# Off unless asked for: SWAP_GB=2 on the command line, or answer the prompt.
# Worth having on a small box - nginx under a console download opens a lot of
# connections at once, and being killed for it is worse than being slow - but
# it is the operator's disk, so it is never created behind their back.
HAVE_SWAP="$(free -m | awk '/Swap/{print $2}')"
if [ -z "${SWAP_GB:-}" ] && [ -z "${ASSUME_YES:-}" ] && [ -z "$UPGRADE" ]; then
    if [ "${HAVE_SWAP:-0}" = 0 ]; then
        printf '\n%sThis machine has no swap.%s\n\n' "$B" "$N"
        read -r -p "  create a swap file? size in GB, or enter to skip: " SWAP_GB
    else
        # Say so rather than skipping in silence. An operator who expected a
        # question and got nothing cannot tell "already handled" from "the
        # installer forgot", and will go looking - which is exactly what
        # happened the first time somebody ran this on a machine that had swap.
        step "Swap"
        info "already has ${HAVE_SWAP} MB - leaving it alone"
    fi
fi
if [ -n "${SWAP_GB:-}" ] && [ "${SWAP_GB}" != 0 ]; then
    step "Swap file"
    case "$SWAP_GB" in
        *[!0-9]*|"") die "SWAP_GB must be a whole number of gigabytes" ;;
    esac
    if [ -f /swapfile ]; then
        info "/swapfile already exists - leaving it alone"
    else
        avail="$(df --output=avail -BG / | tail -1 | tr -dc '0-9')"
        [ "${avail:-0}" -gt "$((SWAP_GB + 2))" ] \
            || die "only ${avail}G free on / - not creating a ${SWAP_GB}G swap file"
        # fallocate can produce a sparse file, which the kernel refuses to swap
        # to. dd is slower and correct.
        dd if=/dev/zero of=/swapfile bs=1M count=$((SWAP_GB * 1024)) status=none
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
        swapon /swapfile
        note_file /swapfile
        grep -q '^/swapfile ' /etc/fstab 2>/dev/null \
            || echo '/swapfile none swap sw 0 0' >> /etc/fstab
        remember swapfile "/swapfile"
        info "created and enabled ${SWAP_GB}G of swap"
    fi
fi

step "Kernel tuning for the long-RTT link"
# The conntrack limits only exist once the module is loaded, and at boot
# systemd-sysctl runs before anything has loaded it - so the module is named
# for early loading, and loaded now, before the settings that need it.
printf 'nf_conntrack\n' > /etc/modules-load.d/smartdns-conntrack.conf 2>/dev/null || true
modprobe nf_conntrack >/dev/null 2>&1 || true
install_payload SYSCTL /etc/sysctl.d/99-smartdns-tuning.conf || true
sysctl -p /etc/sysctl.d/99-smartdns-tuning.conf >/dev/null 2>&1 || true

BBR_FILE=/etc/sysctl.d/99-smartdns-bbr.conf
# Congestion control is machine-wide: it changes every connection on the box,
# including services that have nothing to do with this one. So it is asked for
# rather than assumed. On a non-interactive run the existing choice stands,
# which means an upgrade never silently changes how a working server behaves.
if [ -z "${ENABLE_BBR:-}" ]; then
    if [ -n "${ASSUME_YES:-}" ] || [ -n "$UPGRADE" ]; then
        # Keep whatever the machine is already doing. The running value matters
        # as much as the file: earlier versions set bbr from the main tuning
        # file, so on those machines there is no bbr file to find, and deciding
        # by the file alone would leave the kernel on bbr now and drop it at
        # the next reboot - a change nobody asked for, appearing days later.
        if [ -f "$BBR_FILE" ] || \
           [ "$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null)" = bbr ]; then
            ENABLE_BBR=yes
        else
            ENABLE_BBR=no
        fi
    else
        printf '\n    %sBBR congestion control%s\n' "$B" "$N"
        printf '    Paces by measured bandwidth instead of backing off on loss.\n'
        printf '    On a %s ms link it is worth a great deal, but it affects every\n' "90"
        printf '    connection on this machine, not only this service.\n\n'
        read -r -p "    enable BBR? [Y/n]: " answer
        case "$answer" in n|N|no) ENABLE_BBR=no ;; *) ENABLE_BBR=yes ;; esac
    fi
fi
case "$ENABLE_BBR" in
    yes|y|1|true)
        install_payload SYSCTL_BBR "$BBR_FILE" || true
        sysctl -p "$BBR_FILE" >/dev/null 2>&1 || true
        ;;
    no|n|0|false)
        rm -f "$BBR_FILE"
        if [ "$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null)" = bbr ]; then
            # Only fall back to the kernel default if nothing else on the
            # machine asks for bbr. Someone who set it themselves, in their own
            # file, keeps it - they did not ask this installer to decide.
            if grep -rqs 'tcp_congestion_control' /etc/sysctl.conf /etc/sysctl.d 2>/dev/null; then
                info "BBR left on - another config on this machine sets it"
            else
                sysctl -w net.ipv4.tcp_congestion_control=cubic >/dev/null 2>&1 || true
                sysctl -w net.core.default_qdisc=fq_codel >/dev/null 2>&1 || true
                info "BBR turned off"
            fi
        fi
        ;;
esac
info "congestion=$(sysctl -n net.ipv4.tcp_congestion_control) qdisc=$(sysctl -n net.core.default_qdisc)"

# ---------------------------------------------------------------- nginx
step "nginx"
MOD="$(find /usr/lib/nginx/modules -name ngx_stream_module.so 2>/dev/null | head -1)"
[ -n "$MOD" ] || die "the nginx stream module is missing - libnginx-mod-stream did not install"
info "stream module: $MOD"
# Google refuses Gemini and its other AI services to some exits' IPv4 addresses
# and serves the same pages to the same machine over IPv6, so where the exit
# has working IPv6, Google's own names leave over it. That needs a resolver
# that can be told to ask for AAAA records only, which nginx has from 1.23.1 -
# older, or without IPv6, the block is left out and nothing changes.
NO_GOOGLE_V6=1
if is_exit; then
    ngv="$(nginx -v 2>&1 | sed -n 's#.*nginx/\([0-9.]*\).*#\1#p')"
    if [ "$(printf '%s\n%s\n' 1.23.1 "${ngv:-0}" | sort -V | head -1)" = 1.23.1 ] \
       && curl -6 -s -o /dev/null -m 10 https://www.google.com/ 2>/dev/null; then
        NO_GOOGLE_V6=""
        info "Google's own names leave over IPv6 (Gemini is refused to some exits' IPv4)"
    else
        info "no working IPv6 here, or nginx older than 1.23.1 - Google leaves over IPv4"
    fi
fi
# The tunnel's binary comes before nginx, so that a download that fails
# leaves this run on the direct path rather than with nginx pointed at a
# tunnel that will never be there.
if [ "$TUNNEL" = backpack ] && ! install_backpack; then
    # A relay that cannot reach GitHub to update BackPack keeps the one it has:
    # older and newer ends speak the same protocol, and a tunnel dropped for
    # want of a download would leave it on a direct path that may be filtered.
    if [ -x "$BACKPACK_BIN" ]; then
        warn "keeping BackPack $(cut -d' ' -f1 "$BACKPACK_BIN.version" 2>/dev/null) that is already here"
    else
        warn "no tunnel this run - the relay reaches the exit directly"
        TUNNEL=off; TUNNEL_SPEC=""; TUNNEL_OUT="none - BackPack could not be installed"
    fi
elif [ "$TUNNEL" != backpack ] && [ "$ROLE" != single ]; then
    # Here all the same, on a relay or an exit with no tunnel now: the admin
    # panel can give any relay one later, without this installer. Not being
    # able to fetch it is no failure of this run.
    BACKPACK_QUIET=1 install_backpack || true
fi
if [ "$ROLE" = relay ]; then exit_upstreams; fi
# DNS over HTTPS and TLS, on a relay that has a name and a certificate for
# it. On a first install the certificate comes later in this run, so this is
# decided again once it is there - see relay_doh.
doh_paths
if [ -n "$DOH_ON" ]; then doh_includes; fi
# The exit's public resolvers, as the admin panel last set them - so an
# upgrade does not put the defaults back over the operator's choice.
RESOLVERS=""
if is_exit && [ -f /etc/smart-dns/upstream ]; then
    RESOLVERS="$(head -n 1 /etc/smart-dns/upstream \
        | grep -Ex '([0-9]{1,3}\.){3}[0-9]{1,3}( ([0-9]{1,3}\.){3}[0-9]{1,3})?' || true)"
fi
if [ "$ROLE" = exit ] || [ "$ROLE" = node ]; then relay_allows; fi
if [ "$ROLE" = relay ]; then
    install_payload RELAY_NGINX /etc/nginx/nginx.conf && NGINX_CHANGED=1 || true
else
    install_payload EXIT_NGINX /etc/nginx/nginx.conf && NGINX_CHANGED=1 || true
fi
nginx -t || die "nginx rejected the config; the previous one is in $BACKUP_DIR"
enable_service nginx nginx

# ---------------------------------------------------------------- relay only
if is_relay; then

    step "dnsmasq: the routed domain list"
    note_file /etc/dnsmasq.d/smart-dns.conf
    tmp="$(mktemp)"
    {
        # No timestamp in here. It would make the file differ on every run, so
        # every run would rewrite it and restart dnsmasq for no reason.
        printf '# generated by the smart-dns installer - do not edit by hand\n'
        # The upstream resolvers are in upstream.conf, below: the admin panel
        # changes them, and this file is rewritten on every upgrade.
        printf 'cache-size=10000\ndomain-needed\nbogus-priv\nno-hosts\n'
        printf 'bind-interfaces\nlisten-address=127.0.0.1,%s\n\n' "$LISTEN_IP"
        printf '# domains answered with this relay, so the traffic leaves via the exit\n'
        payload DOMAINS | while read -r d; do
            [ -n "$d" ] && printf 'address=/%s/%s\n' "$d" "$RELAY_IP"
        done
    } > "$tmp"
    if [ -f /etc/dnsmasq.d/smart-dns.conf ] && cmp -s "$tmp" /etc/dnsmasq.d/smart-dns.conf; then
        rm -f "$tmp"; info "unchanged ($(grep -c '^address=' /etc/dnsmasq.d/smart-dns.conf) domains)"
    else
        backup_file /etc/dnsmasq.d/smart-dns.conf
        mv "$tmp" /etc/dnsmasq.d/smart-dns.conf; chmod 644 /etc/dnsmasq.d/smart-dns.conf
        info "wrote $(grep -c '^address=' /etc/dnsmasq.d/smart-dns.conf) domains"
        DNSMASQ_CHANGED=1
    fi

    step "dnsmasq: the public resolvers"
    # A file of its own, because the admin panel changes it (smartdns-sync
    # writes the pick once it has checked it answers from here). So it is
    # written only when missing, and an upgrade leaves the choice alone. No
    # Google by default: 8.8.8.8 passes the asker's subnet on (ECS), and
    # services that refuse Iran in their DNS - Tencent's games answer 0.0.0.1 -
    # saw this relay's Iranian subnet and refused it.
    if [ -f /etc/dnsmasq.d/upstream.conf ]; then
        info "unchanged ($(grep '^server=' /etc/dnsmasq.d/upstream.conf | cut -d= -f2 | tr '\n' ' '))"
    else
        note_file /etc/dnsmasq.d/upstream.conf
        printf '# the public resolvers - the admin panel changes these\nno-resolv\nserver=1.1.1.1\nserver=9.9.9.9\n' \
            > /etc/dnsmasq.d/upstream.conf
        chmod 644 /etc/dnsmasq.d/upstream.conf
        info "wrote 1.1.1.1 and 9.9.9.9"
        DNSMASQ_CHANGED=1
    fi

    step "dnsmasq: names that must NOT be routed"
    install_payload BYPASS /etc/dnsmasq.d/bypass.conf && DNSMASQ_CHANGED=1 || true
    rm -f /etc/dnsmasq.d/ea-bypass.conf   # superseded filename from an earlier build

    step "dnsmasq: stop AAAA answers routing clients around us"
    install_payload NO_AAAA /etc/dnsmasq.d/no-aaaa.conf && DNSMASQ_CHANGED=1 || true

    dnsmasq --test -C /etc/dnsmasq.conf || die "dnsmasq rejected the config"
    enable_service dnsmasq dnsmasq

    step "STUN server, so consoles can still detect their NAT"
    install_payload TURNSERVER /etc/turnserver.conf || true
    grep -q '^TURNSERVER_ENABLED=1' /etc/default/coturn 2>/dev/null \
        || echo 'TURNSERVER_ENABLED=1' >> /etc/default/coturn
    enable_service coturn coturn
    systemctl restart coturn || warn "coturn did not start; STUN will be unavailable"

    step "Firewall: rate limit, access control and traffic accounting"
    export PATH="$PATH:/usr/sbin"
    mkdir -p /etc/nftables.d
    # An earlier version of this installer built the table with a series of
    # `nft add` commands and dumped the result here. That file is a complete
    # table definition, so leaving it in place would load a second copy of
    # every chain alongside the one below.
    if [ -f /etc/nftables.d/smartdns.conf ]; then
        backup_file /etc/nftables.d/smartdns.conf
        rm -f /etc/nftables.d/smartdns.conf
        info "removed the ruleset from the previous layout"
    fi
    grep -q 'nftables.d' /etc/nftables.conf 2>/dev/null \
        || echo 'include "/etc/nftables.d/*.conf"' >> /etc/nftables.conf

    install_payload NFTABLES /etc/nftables.d/10-smartdns.conf && NFT_CHANGED=1 || NFT_CHANGED=0
    # Reload only when the structure actually changed, or when the table is
    # missing entirely. Loading it on every run would append a duplicate of
    # every rule; rebuilding the table on every run would throw away the
    # allowlist and everybody's usage along with it.
    if [ "$NFT_CHANGED" = 1 ] || ! nft list table inet smartdns >/dev/null 2>&1; then
        [ -x /usr/local/bin/smartdns-acl ] && /usr/local/bin/smartdns-acl save 2>/dev/null
        nft delete table inet smartdns 2>/dev/null || true
        nft -f /etc/nftables.d/10-smartdns.conf || die "nft rejected the ruleset"
        # Structure first, then whoever was registered before it, then the
        # access rules if this machine had them switched on.
        [ -f /etc/nftables.d/20-smartdns-state.conf ] \
            && { nft -f /etc/nftables.d/20-smartdns-state.conf || warn "could not restore the allowlist"; }
        [ -f /etc/nftables.d/30-smartdns-enforce.conf ] \
            && { nft -f /etc/nftables.d/30-smartdns-enforce.conf || warn "could not restore the access rules"; }
        info "ruleset loaded"
    else
        info "ruleset already current"
    fi
    enable_service nftables nftables

    step "smartdns-acl command, for access control and usage"
    note_file /usr/local/bin/smartdns-acl
    payload SMARTDNS_ACL > /usr/local/bin/smartdns-acl
    chmod +x /usr/local/bin/smartdns-acl
    install_payload ACL_SAVE_SERVICE /etc/systemd/system/smartdns-acl-save.service || true
    install_payload ACL_SAVE_TIMER   /etc/systemd/system/smartdns-acl-save.timer   || true
    systemctl daemon-reload
    enable_service smartdns-acl-save.timer
    systemctl start smartdns-acl-save.timer 2>/dev/null || true
    # Counting starts now; blocking does not. Nobody has registered an address
    # yet, so switching enforcement on at this point would cut off every user
    # of the relay, including whoever is running this.
    info "counting usage - nothing is blocked yet"

    step "smartdns-shape command, for per-customer speed limits"
    note_file /usr/local/bin/smartdns-shape
    payload SMARTDNS_SHAPE > /usr/local/bin/smartdns-shape
    chmod +x /usr/local/bin/smartdns-shape
    # Nothing is shaped until a customer is actually given a limit; the sync
    # agent calls this when the panel says somebody has one.
    if ! modprobe sch_htb 2>/dev/null; then
        warn "this kernel has no htb - speed limits will not work here"
    fi
    info "no limits set - customers run at line rate until you set one"

    step "smartdns command"
    note_file /usr/local/bin/smartdns
    payload SMARTDNS | sed "s#__RELAY_IP__#${RELAY_IP}#g" > /usr/local/bin/smartdns
    chmod +x /usr/local/bin/smartdns
    info "try: smartdns status"

    step "smartdns-rules command, for what each template does with a domain"
    note_file /usr/local/bin/smartdns-rules
    payload SMARTDNS_RULES > /usr/local/bin/smartdns-rules
    chmod +x /usr/local/bin/smartdns-rules
    info "try: smartdns-rules check gemini.google.com"

    step "smartdns-watch command, for the names a customer asks for"
    note_file /usr/local/bin/smartdns-watch
    payload SMARTDNS_WATCH > /usr/local/bin/smartdns-watch
    chmod +x /usr/local/bin/smartdns-watch
    info "try: smartdns-watch <username or address>"

    step "epic-pin, keeping Epic's backend on addresses that answer from here"
    # epic-pins.conf is written later by epic-pin itself, but it is ours either
    # way and uninstall needs to know to take it with us.
    for f in /usr/local/bin/epic-pin \
             /etc/systemd/system/epic-pin.service \
             /etc/systemd/system/epic-pin.timer \
             /etc/dnsmasq.d/epic-pins.conf
    do
        note_file "$f"
    done
    payload EPIC_PIN > /usr/local/bin/epic-pin
    chmod +x /usr/local/bin/epic-pin
    payload EPIC_PIN_SERVICE > /etc/systemd/system/epic-pin.service
    payload EPIC_PIN_TIMER   > /etc/systemd/system/epic-pin.timer
    systemctl daemon-reload
    enable_service epic-pin.timer
    systemctl start epic-pin.timer  >/dev/null 2>&1 || true
fi

# ------------------------------------------------------------------- TLS
# A machine that already has a domain keeps it, even when this run was not
# told one. Everything below is gated on PANEL_DOMAIN - the certificate, its
# renewal timer, the admin panel and its restart - so an upgrade that did not
# repeat the domain skipped all of it and still ended by announcing a
# successful upgrade. The operator is then left on the previous version of the
# one page they actually use, with nothing said. That is not hypothetical: it
# happened here, and the symptom was an admin panel showing a stale service
# catalogue and a stale warning under every group in it.
#
# The state file is no help - it is truncated at the start of every run - so
# the answer has to come from something the machine keeps for its own sake.
PANEL_DOMAIN="${PANEL_DOMAIN:-}"
if [ -z "$PANEL_DOMAIN" ]; then
    if [ -f /etc/smart-dns/sync.env ]; then
        PANEL_DOMAIN="$(sed -n 's/^PANEL_DOMAIN=//p' /etc/smart-dns/sync.env \
                        | head -1 || true)"
    fi
    # The exit keeps no sync.env. Its admin.env records where the certificate
    # is, and that path is /etc/letsencrypt/live/<domain>/fullchain.pem.
    if [ -z "$PANEL_DOMAIN" ] && [ -f /etc/smart-dns/admin.env ]; then
        PANEL_DOMAIN="$(sed -n \
            's#^ADMIN_CERT=/etc/letsencrypt/live/\([^/]*\)/.*#\1#p' \
            /etc/smart-dns/admin.env | head -1 || true)"
    fi
    if [ -n "$PANEL_DOMAIN" ]; then
        info "keeping the domain this machine already has: $PANEL_DOMAIN"
    fi
fi

# The helper goes on every machine, domain or no domain. Without one this run
# installs no certificate - but the summary at the end tells the operator to
# come back and run this command once they have a name, and a command that is
# only installed when it is not needed is not much of an instruction.
payload CERT > /usr/local/bin/smartdns-cert
chmod +x /usr/local/bin/smartdns-cert
note_file /usr/local/bin/smartdns-cert
payload SMARTDNS_LOGS > /usr/local/bin/smartdns-logs
chmod +x /usr/local/bin/smartdns-logs
note_file /usr/local/bin/smartdns-logs
payload SMARTDNS_RESTART > /usr/local/bin/smartdns-restart
chmod +x /usr/local/bin/smartdns-restart
note_file /usr/local/bin/smartdns-restart
# On either side, tunnel or none: status says there is none, which is itself
# the answer somebody asking wants.
payload SMARTDNS_TUNNEL > /usr/local/bin/smartdns-tunnel
chmod +x /usr/local/bin/smartdns-tunnel
note_file /usr/local/bin/smartdns-tunnel
payload SMARTDNS_MENU > /usr/local/bin/smartdns-menu
chmod +x /usr/local/bin/smartdns-menu
note_file /usr/local/bin/smartdns-menu
install_payload CERT_SERVICE /etc/systemd/system/smartdns-cert.service || true
install_payload CERT_TIMER   /etc/systemd/system/smartdns-cert.timer   || true
systemctl daemon-reload

if [ -n "${PANEL_DOMAIN:-}" ]; then
    step "HTTPS certificate for $PANEL_DOMAIN"
    CERT_PKGS="certbot"
    [ -f /etc/smart-dns/cloudflare.ini ] && CERT_PKGS="$CERT_PKGS python3-certbot-dns-cloudflare"
    for pkg in $([ -z "${PANEL_CERT:-}" ] && echo $CERT_PKGS); do
        dpkg -s "$pkg" >/dev/null 2>&1 || {
            apt-get install -y -qq "$pkg" >/dev/null 2>&1 || die "could not install $pkg"
            NEW_PACKAGES="$NEW_PACKAGES $pkg"
            remember packages-installed "$pkg"
        }
    done

    mkdir -p /etc/smart-dns; chmod 700 /etc/smart-dns

    # A certificate the operator obtained themselves. Recorded and used as-is;
    # keeping it renewed is then their business, which is the trade they made
    # by not handing over a DNS token.
    if [ -n "${PANEL_CERT:-}" ]; then
        [ -f "$PANEL_CERT" ] || die "no certificate at $PANEL_CERT"
        [ -f "${PANEL_KEY:-}" ] || die "no private key at ${PANEL_KEY:-<not given>}"
        CERT_PATH="$PANEL_CERT"; KEY_PATH="$PANEL_KEY"
        info "using the certificate you supplied"
        # Nothing here renews it, so say how long it has. A panel that stops
        # answering in two months with no warning is a bad way to find out.
        if openssl x509 -checkend $((30 * 86400)) -noout -in "$CERT_PATH" >/dev/null 2>&1; then
            info "valid until $(openssl x509 -enddate -noout -in "$CERT_PATH" | cut -d= -f2)"
        else
            warn "this certificate expires within 30 days - nothing here renews it"
        fi
    else
        # certbot's own. It proves the domain over port 80 by default, which
        # needs nothing from the operator but a record pointing here. A token
        # left in cloudflare.ini switches it to DNS instead, but nothing asks
        # for one and nothing needs one.
        if [ -n "${CF_API_TOKEN:-}" ]; then
            umask 077
            printf 'dns_cloudflare_api_token = %s\n' "$CF_API_TOKEN" \
                > /etc/smart-dns/cloudflare.ini
            umask 022
            chmod 600 /etc/smart-dns/cloudflare.ini
        fi
        CERT_PATH="/etc/letsencrypt/live/$PANEL_DOMAIN/fullchain.pem"
        KEY_PATH="/etc/letsencrypt/live/$PANEL_DOMAIN/privkey.pem"
    fi

    if [ -z "${PANEL_CERT:-}" ]; then
        /usr/local/bin/smartdns-cert "$PANEL_DOMAIN" || die "could not get a certificate"
        # Only once there is something to renew. A timer running against no
        # certificate is a unit that wakes twice a day to do nothing.
        enable_service smartdns-cert.timer
        systemctl start smartdns-cert.timer 2>/dev/null || true
    fi
    [ -f "$CERT_PATH" ] || die "still no certificate at $CERT_PATH"
    remember panel-domain "$PANEL_DOMAIN"
fi
relay_doh

# ------------------------------------------------------------------ node
if [ "$ROLE" = node ]; then
    step "Node: joined to the panel on $PANEL_IP"
    SECRET="$(printf '%s' "$SYNC_TOKEN" | cut -d. -f1)"
    FINGER="$(printf '%s' "$SYNC_TOKEN" | cut -s -d. -f2)"
    [ -n "$SECRET" ] && [ -n "$FINGER" ] && [ "$SECRET" != "$FINGER" ] \
        || die "that does not look like a pairing token - the whole line from the Node page"
    case "$FINGER" in
        *[!0-9a-f]*|"") die "the fingerprint half of the token is not hexadecimal" ;;
    esac
    valid_ip "$PANEL_IP" || die "PANEL_IP '$PANEL_IP' is not an IPv4 address"
    mkdir -p /etc/smart-dns; chmod 700 /etc/smart-dns
    umask 077
    note_file /etc/smart-dns/node.env
    cat > /etc/smart-dns/node.env <<EOF
PANEL_HOST=$PANEL_IP
SYNC_SECRET=$SECRET
SYNC_FINGERPRINT=$FINGER
SELF_IP=$SELF_IP
EOF
    umask 022
    chmod 600 /etc/smart-dns/node.env
    payload SYNC > /usr/local/bin/smartdns-sync
    chmod +x /usr/local/bin/smartdns-sync
    note_file /usr/local/bin/smartdns-sync
    note_file /usr/local/bin/smartdns-api-guard
    payload SMARTDNS_API_GUARD > /usr/local/bin/smartdns-api-guard
    chmod +x /usr/local/bin/smartdns-api-guard
    install_payload NODE_SERVICE /etc/systemd/system/smartdns-node.service || true
    systemctl daemon-reload
    enable_service smartdns-node.service
    systemctl restart smartdns-node.service
fi

# ----------------------------------------------------------------- panel
if is_exit && [ "$ROLE" != node ] && [ -z "$JOINED" ]; then
    step "Panel: database and sync API"
    mkdir -p /etc/smart-dns; chmod 700 /etc/smart-dns

    # The relay authenticates this machine by the fingerprint of this
    # certificate, so it must survive re-runs: generating a new one would
    # silently break the pairing and the relay would refuse to talk.
    if [ ! -f /etc/smart-dns/sync.key ]; then
        openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
            -subj "/CN=smartdns-sync" \
            -keyout /etc/smart-dns/sync.key -out /etc/smart-dns/sync.crt \
            >/dev/null 2>&1 || die "could not generate the sync certificate"
        chmod 600 /etc/smart-dns/sync.key
        info "generated the sync certificate"
    fi
    # Same for the shared secret. Re-running the installer must not unpair a
    # relay that is working.
    # `|| true` again: on the first install panel.env does not exist, sed exits
    # non-zero, and under `set -e` with pipefail that ends the installer right
    # here without printing anything.
    SYNC_SECRET="$(sed -n 's/^SYNC_SECRET=//p' /etc/smart-dns/panel.env 2>/dev/null | head -1 || true)"
    [ -n "$SYNC_SECRET" ] || SYNC_SECRET="$(openssl rand -hex 24)"

    umask 077
    if [ ! -f /etc/smart-dns/panel.env ]; then
        cat > /etc/smart-dns/panel.env <<EOF
# Secrets and panel settings. Not in git and not in the installer: this file is
# written at install time and is readable only by root.
SYNC_SECRET=$SYNC_SECRET
RELAY_IP=$RELAY_IP
EOF
    else
        # Merge rather than rewrite. An earlier version of this rewrote the
        # whole file on every run, which silently undid the operator's own
        # settings - a second relay added to RELAY_IP, a CLAIM_HOST - and the
        # only symptom was the other relay suddenly getting 401s.
        # RELAY_IP is a list, and this relay may already be on it or may be a
        # new one joining. Adding is right; replacing would unpair the others.
        current_relays="$(sed -n 's/^RELAY_IP=//p' /etc/smart-dns/panel.env | head -1 || true)"
        case ",${current_relays}," in
            *",$RELAY_IP,"*) ;;
            *) set_env_key /etc/smart-dns/panel.env RELAY_IP \
                   "${current_relays:+$current_relays,}$RELAY_IP"
               info "added $RELAY_IP to the relays this panel serves" ;;
        esac
    fi
    # A single machine's sync API is for itself alone, on loopback: its 8443
    # is the customer panel.
    if [ "$ROLE" = single ]; then
        set_env_key /etc/smart-dns/panel.env API_PORT "$SINGLE_API_PORT"
        set_env_key /etc/smart-dns/panel.env API_BIND 127.0.0.1
    fi
    # What a re-run or an upgrade keeps, unasked.
    set_env_key /etc/smart-dns/panel.env TUNNEL "$TUNNEL"
    set_env_key /etc/smart-dns/panel.env TUNNEL_TRANSPORT "${TUNNEL_TRANSPORT:-}"
    set_env_key /etc/smart-dns/panel.env TUNNEL_DIRECTION "${TUNNEL_DIRECTION:-}"
    set_env_key /etc/smart-dns/panel.env TUNNEL_PORT "${TUNNEL_PORT:-}"
    set_env_key /etc/smart-dns/panel.env TUNNEL_PRESET "${TUNNEL_PRESET:-}"
    umask 022
    chmod 600 /etc/smart-dns/panel.env

    payload PANEL > /usr/local/bin/smartdns-panel
    chmod +x /usr/local/bin/smartdns-panel
    note_file /usr/local/bin/smartdns-panel
    # The service catalogue: which brands exist, and which domains are in each
    # group. Shipped as a file so it is versioned with the code rather than
    # migrated into the database.
    mkdir -p /usr/local/share/smart-dns
    note_file /usr/local/share/smart-dns/services.json
    payload SERVICES > /usr/local/share/smart-dns/services.json
    # The game index: which groups each game needs. Only names and
    # references - nothing here routes anything on its own.
    note_file /usr/local/share/smart-dns/games.json
    payload GAMES > /usr/local/share/smart-dns/games.json
    # What a template can close: ad networks and adult sites, by name and
    # domain, for the admin to tick at the foot of a template's page.
    note_file /usr/local/share/smart-dns/blocks.json
    payload BLOCKS > /usr/local/share/smart-dns/blocks.json
    # The logos drawn beside the services on a template's page.
    note_file /usr/local/share/smart-dns/icons.json
    payload ICONS > /usr/local/share/smart-dns/icons.json
    # Only the relays reach the sync API. The panel's service runs this before
    # every start, so a relay added to RELAY_IP by hand is let in the next time
    # the panel restarts - exactly when the panel itself would let it in.
    note_file /usr/local/bin/smartdns-api-guard
    payload SMARTDNS_API_GUARD > /usr/local/bin/smartdns-api-guard
    chmod +x /usr/local/bin/smartdns-api-guard
    install_payload PANEL_SERVICE /etc/systemd/system/smartdns-panel.service || true
    # The key that seals customers' receipts and ticket pictures. Made here,
    # as root: the panel's sandbox keeps /etc read-only. Never over a missing
    # key while sealed pictures are in the database - that only warns. Not
    # noted for uninstall: a database left behind is no use without it.
    if /usr/local/bin/smartdns-panel --make-key; then :; else
        warn "the pictures' sealing key is missing - see the admin panel's settings"
    fi
    systemctl daemon-reload
    enable_service smartdns-panel.service
    systemctl restart smartdns-panel.service
    sleep 2
    if systemctl is-active --quiet smartdns-panel.service; then
        if [ "$ROLE" = single ]; then info "sync API is up on 127.0.0.1:$SINGLE_API_PORT"
        else info "sync API is up on :8443"; fi
    else
        warn "the panel did not start - journalctl -u smartdns-panel"
    fi
    if [ "$ROLE" = single ]; then
        :       # on loopback, nothing to close
    elif nft list table inet smartdns_api >/dev/null 2>&1; then
        info "port 8443 answers the relays only: $(sed -n 's/^RELAY_IP=//p' /etc/smart-dns/panel.env | head -1)"
    else
        warn "port 8443 could not be closed to strangers - the panel still refuses them itself"
    fi

    # ---- admin web panel -------------------------------------------------
    if [ -n "${PANEL_DOMAIN:-}" ]; then
        step "Admin web panel"
        payload ADMIN > /usr/local/bin/smartdns-admin
        chmod +x /usr/local/bin/smartdns-admin
        note_file /usr/local/bin/smartdns-admin
        install_payload ADMIN_SERVICE /etc/systemd/system/smartdns-admin.service || true

        payload SMARTDNS_ACCESS > /usr/local/bin/smartdns-access
        chmod +x /usr/local/bin/smartdns-access
        note_file /usr/local/bin/smartdns-access

        # Generated once and kept. Regenerating on every run would move the URL
        # and change the password under the operator each time they upgraded.
        if [ ! -f /etc/smart-dns/admin.env ]; then
            # Asked for, not assumed. The port is the operator's firewall to
            # think about, and a password they chose is one they will still
            # have tomorrow - a generated one gets pasted somewhere careless
            # or lost. Both have answers, so pressing enter is fine.
            if [ -z "${ASSUME_YES:-}" ]; then
                printf '\n%sAdmin panel%s\n\n' "$B" "$N"
                if [ -z "${ADMIN_PORT:-}" ]; then
                    # Said before the question rather than after a rejected
                    # answer: an operator who has already typed 443 has
                    # usually also written it into a firewall rule.
                    warn "these ports are taken - do not pick one of them:"
                    warn "    22    ssh"
                    warn "    53    dns"
                    warn "    80    the proxy, and how certificates are proved"
                    warn "   443    the proxy"
                    warn "  8443    the sync API the relays connect to"
                    warn "  8445    the bot API"
                    warn "  8446    the exit's own route to Google over IPv6"
                    warn " 18119    the exit's own route to Battle.net"
                    warn "  1119    Battle.net's launcher"
                    warn "  4070    Spotify's access point"
                    warn "on a relay, 3478 is taken as well."
                    warn "pick anything else, and open it in your firewall."
                    printf '\n'
                    read -r -p "  port to serve it on [9443]: " ADMIN_PORT
                fi
                if [ -z "${ADMIN_USER:-}" ]; then
                    read -r -p "  username [admin]: " ADMIN_USER
                fi
                if [ -z "${ADMIN_PASS:-}" ]; then
                    printf '  password [enter for a generated one]: '
                    read -rs ADMIN_PASS; printf '\n'
                    if [ -n "$ADMIN_PASS" ]; then
                        printf '  again: '
                        read -rs ADMIN_PASS2; printf '\n'
                        [ "$ADMIN_PASS" = "$ADMIN_PASS2" ] \
                            || die "the two passwords did not match"
                        [ "${#ADMIN_PASS}" -ge 8 ] \
                            || die "use a password of 8 characters or more"
                    fi
                fi
            fi
            ADMIN_PORT="${ADMIN_PORT:-9443}"
            ADMIN_USER="$(printf '%s' "${ADMIN_USER:-admin}" | tr 'A-Z' 'a-z')"
            printf '%s' "$ADMIN_USER" | grep -Eq '^[a-z0-9._-]{3,32}$' \
                || die "a username is 3 to 32 lower-case letters, digits, . - _"
            case "$ADMIN_PORT" in
                *[!0-9]*|"") die "the admin port must be a number" ;;
                22) die "port 22 is ssh" ;;
                8443) die "port 8443 is the sync API the relays connect to" ;;
                8445) die "port 8445 is the bot API" ;;
                8446) die "port 8446 is the exit's own route to Google over IPv6" ;;
                18119) die "port 18119 is the exit's own route to Battle.net" ;;
                "$SINGLE_API_PORT") if [ "$ROLE" = single ]; then die "port $SINGLE_API_PORT is this machine's own sync API"; fi ;;
                853|3478) if [ "$ROLE" = single ]; then die "port $ADMIN_PORT is taken on a single machine - DoT and STUN"; fi ;;
                1119) die "port 1119 carries Battle.net's launcher" ;;
                4070) die "port 4070 carries Spotify's access point" ;;
                53|80|443) die "port $ADMIN_PORT is the service's own - pick
    another. 22, 53, 80, 443, 1119, 4070, 8443, 8445, 8446 and 18119 are all taken." ;;
                "${TUNNEL_PORT:-none}") die "port $ADMIN_PORT carries the tunnel - pick another" ;;
            esac
            # The path stays generated. Nobody types it from memory, and an
            # operator asked to invent one invents a guessable one.
            [ -n "${ADMIN_PASS:-}" ] \
                || ADMIN_PASS="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-16)"
            ADMIN_SALT="$(openssl rand -hex 16)"
            ADMIN_HASH="$(ADMIN_PASS="$ADMIN_PASS" ADMIN_SALT="$ADMIN_SALT" python3 -c '
import hashlib, os
print(hashlib.pbkdf2_hmac("sha256", os.environ["ADMIN_PASS"].encode(),
                          bytes.fromhex(os.environ["ADMIN_SALT"]), 200000).hex())')"
            ADMIN_PATH_GEN="$(openssl rand -hex 12)"
            umask 077
            cat > /etc/smart-dns/admin.env <<EOF
# Written once at install. The password itself is not stored - only a salted
# hash - so a forgotten password is replaced, never recovered.
ADMIN_PORT=$ADMIN_PORT
ADMIN_USER=$ADMIN_USER
ADMIN_PATH=$ADMIN_PATH_GEN
ADMIN_SALT=$ADMIN_SALT
ADMIN_HASH=$ADMIN_HASH
ADMIN_CERT=$CERT_PATH
ADMIN_KEY=$KEY_PATH
EOF
            umask 022
            chmod 600 /etc/smart-dns/admin.env
            # The panel was started above, before this file existed, so its
            # bot API picked the relays' self-signed pair and would keep it
            # until the next restart. Once more, now that it can find the
            # real certificate.
            systemctl try-restart smartdns-panel.service >/dev/null 2>&1 || true
            ADMIN_URL_OUT="https://$PANEL_DOMAIN:$ADMIN_PORT/$ADMIN_PATH_GEN/"
            ADMIN_PASS_OUT="$ADMIN_PASS"
            ADMIN_USER_OUT="$ADMIN_USER"
        else
            info "keeping the admin URL and password already set up here"
            info "change them with: smartdns-access"
            # From 0.9.0 the panel is signed into with a username too. A panel
            # from before gets one now - asked for, or "admin" to change later.
            if ! grep -q '^ADMIN_USER=.' /etc/smart-dns/admin.env; then
                # One chosen in the panel by a build before this one is kept.
                [ -n "${ADMIN_USER:-}" ] || ADMIN_USER="$(python3 -c '
import sqlite3, sys
try:
    r = sqlite3.connect(sys.argv[1]).execute(
        "SELECT value FROM settings WHERE key = ?", ("owner_username",)).fetchone()
    print(r[0] if r and r[0] else "")
except Exception:
    pass' "$STATE_DIR/panel.db" 2>/dev/null || true)"
                if [ -z "${ADMIN_USER:-}" ] && [ -z "${ASSUME_YES:-}" ]; then
                    printf '\n  The admin panel now asks for a username as well as the password.\n'
                    read -r -p "  username [admin]: " ADMIN_USER
                fi
                ADMIN_USER="$(printf '%s' "${ADMIN_USER:-admin}" | tr 'A-Z' 'a-z')"
                printf '%s' "$ADMIN_USER" | grep -Eq '^[a-z0-9._-]{3,32}$' || ADMIN_USER=admin
                printf 'ADMIN_USER=%s\n' "$ADMIN_USER" >> /etc/smart-dns/admin.env
                ADMIN_USER_OUT="$ADMIN_USER"
            fi
        fi
        install_font
        # Which operator each customer's address is on, for the users page: a
        # daily list of the operators' own address blocks from RIPE, matched
        # here, so no customer's address is sent anywhere.
        for f in /usr/local/bin/smartdns-operators \
                 /etc/systemd/system/smartdns-operators.service \
                 /etc/systemd/system/smartdns-operators.timer; do
            note_file "$f"
        done
        payload OPERATORS > /usr/local/bin/smartdns-operators
        chmod +x /usr/local/bin/smartdns-operators
        payload OPERATORS_SERVICE > /etc/systemd/system/smartdns-operators.service
        payload OPERATORS_TIMER   > /etc/systemd/system/smartdns-operators.timer
        # The sample Telegram bot. Put here, switched off: the admin panel's
        # bot page sets it up and turns it on. One that is already set up is
        # restarted below, so an upgrade reaches it too.
        note_file /usr/local/bin/master-dns-bot
        note_file /etc/systemd/system/master-dns-bot.service
        payload BOT > /usr/local/bin/master-dns-bot
        chmod +x /usr/local/bin/master-dns-bot
        payload BOT_SERVICE > /etc/systemd/system/master-dns-bot.service
        # A reseller's own bot: the same program, one unit of this each.
        note_file /etc/systemd/system/master-dns-bot@.service
        payload BOT_SELLER_SERVICE > /etc/systemd/system/master-dns-bot@.service
        note_file /usr/local/bin/smartdns-bot-logs
        payload SMARTDNS_BOT_LOGS > /usr/local/bin/smartdns-bot-logs
        chmod +x /usr/local/bin/smartdns-bot-logs
        systemctl daemon-reload
        enable_service smartdns-admin.service
        enable_service smartdns-operators.timer
        systemctl start smartdns-operators.timer >/dev/null 2>&1 || true
        # The first list now, in the background, rather than at the timer's
        # first tick - so the users page has operators on it from the start.
        [ -s /var/lib/smart-dns/operators.json ] \
            || systemctl start --no-block smartdns-operators.service >/dev/null 2>&1 || true
        systemctl restart smartdns-admin.service
        systemctl try-restart master-dns-bot.service >/dev/null 2>&1 || true
        # Taken over with a bot that was running: running here too.
        if [ -n "${TAKE_OVER:-}" ] && grep -q '^BOT_TOKEN=.' /etc/master-dns-bot.env 2>/dev/null; then
            systemctl enable --now master-dns-bot.service >/dev/null 2>&1 || true
        fi
        for sb in /etc/master-dns-bot-*.env; do
            [ -f "$sb" ] || continue
            sb=${sb#/etc/master-dns-bot-}; sb=${sb%.env}
            if [ -n "${TAKE_OVER:-}" ]; then
                systemctl enable --now "master-dns-bot@$sb.service" >/dev/null 2>&1 || true
            else
                systemctl try-restart "master-dns-bot@$sb.service" >/dev/null 2>&1 || true
            fi
        done
        sleep 2
        if systemctl is-active --quiet smartdns-admin.service; then
            info "admin panel running"
        else
            warn "the admin panel did not start - journalctl -u smartdns-admin"
        fi
    fi

    FP="$(openssl x509 -in /etc/smart-dns/sync.crt -noout -fingerprint -sha256 \
          | cut -d= -f2 | tr -d ':' | tr 'A-Z' 'a-z')"
    # A third part when there is a tunnel, so the relay sets up the same one.
    SYNC_TOKEN_OUT="$SYNC_SECRET.$FP${TUNNEL_SPEC:+.$TUNNEL_SPEC}"
    # A single machine pairs with itself, here and now; there is nobody to
    # hand the token to.
    if [ "$ROLE" = single ]; then
        SYNC_TOKEN="$SYNC_TOKEN_OUT"; SYNC_TOKEN_OUT=""; PANEL_IP=127.0.0.1
    fi
fi

# A relay that is already paired keeps its pairing. Requiring the token again
# on every run meant an upgrade run without it skipped this whole section and
# silently left the old agent in place - the machine kept syncing, so nothing
# looked wrong, while the new code never arrived.
if is_relay && [ -z "${SYNC_TOKEN:-}" ] && [ -f /etc/smart-dns/sync.env ]; then
    SYNC_TOKEN="$(sed -n 's/^SYNC_SECRET=//p' /etc/smart-dns/sync.env | head -1 || true).$(sed -n 's/^SYNC_FINGERPRINT=//p' /etc/smart-dns/sync.env | head -1 || true)"
    PANEL_IP="${PANEL_IP:-$(sed -n 's/^PANEL_HOST=//p' /etc/smart-dns/sync.env | head -1 || true)}"
    KEEP_PAIRING=1
fi

if is_relay && [ -n "${SYNC_TOKEN:-}" ]; then
    step "Panel: sync agent and claim page"
    # secret.fingerprint - one string for the user to copy, carrying both the
    # shared secret and the certificate to pin. Splitting them into two
    # questions only creates a chance to paste one and forget the other.
    # The third part, when there is one, is the tunnel, read further up.
    SECRET="$(printf '%s' "$SYNC_TOKEN" | cut -d. -f1)"
    FINGER="$(printf '%s' "$SYNC_TOKEN" | cut -s -d. -f2)"
    [ -n "$SECRET" ] && [ -n "$FINGER" ] && [ "$SECRET" != "$FINGER" ] \
        || die "that does not look like a pairing token.
    It is the whole 'secret.fingerprint' line the exit server printed."
    case "$FINGER" in
        *[!0-9a-f]*|"") die "the fingerprint half of the token is not hexadecimal" ;;
    esac
    [ -n "${KEEP_PAIRING:-}" ] && info "keeping the pairing already on this machine"

    # Usually the panel lives on this relay's own exit, but it need not: one
    # database can serve several relay/exit pairs, and one database is what
    # makes a customer's allowance mean the same thing on all of them.
    # PANEL_IP names the machine running the panel when it is a different one.
    PANEL_HOST="${PANEL_IP:-$EXIT_IP}"
    valid_ip "$PANEL_HOST" || die "PANEL_IP '$PANEL_HOST' is not an IPv4 address"

    mkdir -p /etc/smart-dns; chmod 700 /etc/smart-dns
    # Recover the domain this relay already serves its panel on, if this run
    # was not told one. Without this, re-running the installer and pressing
    # enter at the domain prompt blanked PANEL_DOMAIN, and the customer panel
    # silently dropped from https to plain http - which also switches sign-up
    # off. The same trap that once rewrote panel.env on the exit.
    if [ -z "${PANEL_DOMAIN:-}" ] && [ -f /etc/smart-dns/sync.env ]; then
        PANEL_DOMAIN="$(sed -n 's/^PANEL_DOMAIN=//p' /etc/smart-dns/sync.env | head -1 || true)"
        [ -n "$PANEL_DOMAIN" ] && info "keeping the panel domain already set: $PANEL_DOMAIN"
    fi
    umask 077
    if [ ! -f /etc/smart-dns/sync.env ]; then
        cat > /etc/smart-dns/sync.env <<EOF
PANEL_HOST=$PANEL_HOST
SYNC_SECRET=$SECRET
SYNC_FINGERPRINT=$FINGER
SELF_IP=$RELAY_IP
PANEL_DOMAIN=${PANEL_DOMAIN:-}
EOF
    else
        # Merge, so anything the operator added by hand survives an upgrade.
        set_env_key /etc/smart-dns/sync.env PANEL_HOST "$PANEL_HOST"
        set_env_key /etc/smart-dns/sync.env SYNC_SECRET "$SECRET"
        set_env_key /etc/smart-dns/sync.env SYNC_FINGERPRINT "$FINGER"
        set_env_key /etc/smart-dns/sync.env SELF_IP "$RELAY_IP"
        set_env_key /etc/smart-dns/sync.env PANEL_DOMAIN "${PANEL_DOMAIN:-}"
    fi
    if [ "$ROLE" = single ]; then
        # Its own panel's API on loopback - or, joined, the other panel's 8443.
        if [ -n "$JOINED" ]; then set_env_key /etc/smart-dns/sync.env PANEL_PORT ""
        else set_env_key /etc/smart-dns/sync.env PANEL_PORT "$SINGLE_API_PORT"; fi
        set_env_key /etc/smart-dns/sync.env SINGLE 1
    fi
    set_env_key /etc/smart-dns/sync.env TUNNEL "$TUNNEL"
    set_env_key /etc/smart-dns/sync.env TUNNEL_TRANSPORT "${TUNNEL_TRANSPORT:-}"
    set_env_key /etc/smart-dns/sync.env TUNNEL_DIRECTION "${TUNNEL_DIRECTION:-}"
    set_env_key /etc/smart-dns/sync.env TUNNEL_PORT "${TUNNEL_PORT:-}"
    set_env_key /etc/smart-dns/sync.env TUNNEL_PRESET "${TUNNEL_PRESET:-}"
    umask 022
    chmod 600 /etc/smart-dns/sync.env

    payload SYNC > /usr/local/bin/smartdns-sync
    chmod +x /usr/local/bin/smartdns-sync
    note_file /usr/local/bin/smartdns-sync
    install_font
    # A systemd template, one instance per service profile. The instances
    # themselves are started and stopped by the sync agent as the panel adds
    # and retires templates, so nothing here is enabled.
    install_payload DNS_PROFILE_UNIT /etc/systemd/system/smartdns-dns@.service || true
    # The customer panel's name for addresses not registered yet, so a
    # customer whose line changed can still reach the page. The sync agent
    # starts it while enforcement is on.
    install_payload DNS_GATE_UNIT /etc/systemd/system/smartdns-dns-gate.service || true
    mkdir -p /etc/smartdns-profiles
    install_payload SYNC_SERVICE /etc/systemd/system/smartdns-sync.service || true
    # DNS over HTTPS and TLS. Harmless where the relay has no certificate:
    # nginx only sends it anything once relay_doh has turned DoH on.
    payload DOH > /usr/local/bin/smartdns-doh
    chmod +x /usr/local/bin/smartdns-doh
    note_file /usr/local/bin/smartdns-doh
    install_payload DOH_SERVICE /etc/systemd/system/smartdns-doh.service || true
    systemctl daemon-reload
    enable_service smartdns-sync.service
    systemctl restart smartdns-sync.service
    enable_service smartdns-doh.service
    systemctl restart smartdns-doh.service
    sleep 3
    # Where the customer's panel ended up, for the summary at the end. It is
    # served over TLS or not at all - it asks for a password, and there is no
    # safe way to do that in the clear - so a relay with no certificate has no
    # panel and nothing to print. 8443 sits outside the gated ports on purpose,
    # so somebody whose address changed can still reach the page that fixes it.
    if [ -n "${PANEL_DOMAIN:-}" ]; then
        USER_PANEL_OUT="https://$PANEL_DOMAIN:8443/"
    fi
    # Closed from the moment it is installed. This used to wait for the first
    # customer to register before shutting the door, on the reasoning that
    # enforcing against an empty allowlist cuts everyone off - but on a fresh
    # relay there is nobody to cut off, and what "waiting" really means is a
    # relay that anybody who learns its address can use for free, for as long
    # as it takes somebody to notice.
    #
    # Nothing here is at risk from it. SSH is never gated, the customer panel
    # is on a port the gate does not touch, and the certificate challenge is
    # redirected in prerouting so it reaches certbot before the gate ever sees
    # a packet on 80.
    rm -f /etc/smart-dns/auto-enforce
    if [ "${ENFORCE:-yes}" = no ]; then
        info "ENFORCE=no - this relay is open to everyone until you close it:"
        info "    smartdns-acl enforce on"
    elif smartdns-acl enforce on --yes --allow-empty >/dev/null 2>&1; then
        ENFORCE_OUT=1
        info "access control is on - only registered addresses get through"
    else
        warn "could not switch access control on - this relay is open."
        warn "close it by hand once you have looked:  smartdns-acl enforce on"
    fi
    if systemctl is-active --quiet smartdns-sync.service; then
        info "syncing with the panel at $PANEL_HOST every 30s"
        if [ -n "$USER_PANEL_OUT" ]; then
            info "customer panel on $USER_PANEL_OUT"
        else
            warn "no certificate, so no customer panel - see the end of this run"
        fi
    else
        warn "the sync agent did not start - journalctl -u smartdns-sync"
    fi
fi

# ---------------------------------------------------------------- tunnel
if [ "$ROLE" = exit ]; then apply_tunnel "${SYNC_SECRET:-}"; else apply_tunnel "${SECRET:-}"; fi

# ---------------------------------------------------------------- start
step "Starting services"
if [ "$NGINX_CHANGED" = 1 ]; then systemctl restart nginx
else systemctl reload nginx 2>/dev/null || systemctl start nginx; fi
if is_relay; then
    if [ "$DNSMASQ_CHANGED" = 1 ]; then systemctl restart dnsmasq
    else systemctl start dnsmasq 2>/dev/null || true; fi
    /usr/local/bin/epic-pin || warn "epic-pin failed this run; the timer will retry"
fi

# ---------------------------------------------------------------- verify
step "Checking"
fail=0
check() {
    if [ "$2" = "$3" ]; then printf '    %s.%s %s\n' "$G" "$N" "$1"
    else printf '    %sx%s %s  (got: %s)\n' "$RD" "$N" "$1" "$2"; fail=1; fi
}
check "nginx running" "$(systemctl is-active nginx)" active
if is_relay; then
    check "dnsmasq running" "$(systemctl is-active dnsmasq)" active
    check "coturn running"  "$(systemctl is-active coturn)"  active
    check "a routed domain resolves to this relay" \
          "$(dig +short +time=3 @127.0.0.1 github.com A 2>/dev/null | tail -1)" "$RELAY_IP"
    check "no IPv6 answers leak around the relay" \
          "$(dig +short +time=3 @127.0.0.1 github.com AAAA 2>/dev/null | grep -c ':' || true)" "0"
    # Two things, not one. A domain we do not route has to answer, and has to
    # answer with somebody else's address. Counting its records was wrong:
    # example.com has more than one, and how many is not ours to assert.
    unrouted="$(dig +short +time=3 @127.0.0.1 example.com A 2>/dev/null)"
    check "an unrouted domain still resolves" \
          "$([ -n "$unrouted" ] && echo yes || echo no)" "yes"
    # example.com is the sentinel because it is stable and nobody needs it
    # bypassed - but an operator can add anything to their own routed list, so
    # a failure here is as likely to mean "you added this on purpose" as it is
    # to mean something is wrong. Say which name it used, so the answer is in
    # the message rather than in a debugging session.
    if [ "$(printf '%s\n' "$unrouted" | grep -c "^${RELAY_IP}$" || true)" != 0 ]; then
        warn "example.com resolves to this relay, so it is being routed."
        warn "That is only a problem if you did not mean it - check with:"
        warn "    grep -rn example.com /etc/dnsmasq.d/"
    fi
    check "an unrouted domain is not pointed at this relay" \
          "$(printf '%s\n' "$unrouted" | grep -c "^${RELAY_IP}$" || true)" "0"
    check "a site loads through the full chain" \
          "$(curl -sS -o /dev/null -m 25 --resolve "github.com:443:${LISTEN_IP}" -w '%{http_code}' https://github.com/ 2>/dev/null || true)" "200"
    # The API the relay syncs with, reached the way smartdns-sync reaches it -
    # by address, with a name in the handshake - but with a GET, which the API
    # refuses as 501 without looking at any secret, so this proves the path
    # and leaves no "wrong secret" warning in the exit's log. A relay whose
    # sync could not get through used to pass every check here and then fail
    # in the customer's panel instead.
    if [ -n "$JOINED" ]; then api_at="$PANEL_IP"; api_port=8443
    elif [ "$ROLE" = single ]; then api_at="127.0.0.1"; api_port="$SINGLE_API_PORT"
    else api_at="$EXIT_IP"; api_port=8443; fi
    api_code() {
        curl -sk -o /dev/null -m 20 --resolve "${PANEL_DOMAIN:-sync.example.com}:$2:$1" \
             -w '%{http_code}' "https://${PANEL_DOMAIN:-sync.example.com}:$2/" 2>/dev/null || true
    }
    api_got="$(api_code "$api_at" "$api_port")"
    # The sync tries the tunnel first and the direct path after. Where only
    # the tunnel gets through - which is when xdi is chosen - the direct path
    # failing is no failure of the sync, and saying so here sent people
    # looking for a fault that was not there.
    if [ "$api_got" != 501 ] && [ "$ROLE" = relay ] && [ -z "$JOINED" ] \
            && [ "${TUNNEL:-off}" = backpack ]; then
        if [ "$(api_code 127.0.0.1 "$TUNNEL_LOCAL_API")" = 501 ]; then
            api_got=501
            warn "The exit's sync API answers through the tunnel only: the direct path to"
            warn "$EXIT_IP is closed. The sync uses the tunnel, so this relay works."
        fi
    fi
    check "the exit's sync API answers this relay" "$api_got" "501"
fi
if [ "$ROLE" = node ]; then
    check "the node's sync is running" "$(systemctl is-active smartdns-node.service)" active
    # The panel's API, the way the node reaches it; a GET is refused as 501
    # without any secret being looked at. Anything else - a timeout, most
    # likely - is this node's address not yet on the panel's Node page.
    check "the panel's API answers this node" \
          "$(curl -sk -o /dev/null -m 20 --resolve "sync.example.com:8443:${PANEL_IP}" -w '%{http_code}' "https://sync.example.com:8443/" 2>/dev/null || true)" "501"
fi
if [ "$TUNNEL" = backpack ]; then
    check "the tunnel service is running" "$(systemctl is-active smartdns-tunnel.service)" active
    if [ "$ROLE" = relay ]; then
        # Straight at the tunnel's own end, so that the fallback in nginx
        # cannot pass this for it. The far end may still be dialling in.
        tun=000
        for i in $(seq 1 20); do
            tun="$(curl -s -o /dev/null -m 8 --connect-to "github.com:443:127.0.0.1:$TUNNEL_LOCAL_HTTPS" \
                   -w '%{http_code}' https://github.com/ 2>/dev/null || true)"
            [ "$tun" = 200 ] && break
            sleep 3
        done
        check "a site loads through the tunnel" "$tun" 200
        [ "$tun" = 200 ] || warn "customers still get through - nginx falls back to the direct path -
    but the tunnel is not carrying them. Is port $TUNNEL_PORT open between the two
    machines? Or try another transport: re-run the installer on the exit."
    fi
fi

printf '\n'
if [ "$fail" = 0 ]; then
    # Written here and nowhere earlier: a run that died half way through has
    # not installed this version, and recording it would tell the next run
    # there was nothing left to do.
    mkdir -p "$STATE_DIR"
    printf '%s\n' "$VERSION" > "$VERSION_FILE"
    # On the exit with the panel, a copy of this very file, for the admin
    # panel to upgrade the other servers with - never from the internet,
    # which a relay in Iran may not reach. Only when run from a file.
    if [ "$ROLE" = exit ] && [ -f "$0" ] && grep -qx "VERSION=\"$VERSION\"" "$0"; then
        mkdir -p "$STATE_DIR/installer"
        cp -f "$0" "$STATE_DIR/installer/master-dns.sh.tmp" \
            && chmod 600 "$STATE_DIR/installer/master-dns.sh.tmp" \
            && mv -f "$STATE_DIR/installer/master-dns.sh.tmp" "$STATE_DIR/installer/master-dns.sh" \
            && info "kept this installer for the admin panel to upgrade the other servers with"
    fi
    timeline
    printf '  %s+----------------------------------------------+%s\n' "$C" "$N"
    printf '  %s|%s  %sMaster DNS%s is ready  -  %-9s v%-10s %s|%s\n' "$C" "$N" "$B" "$N" \
           "$ROLE" "$VERSION" "$C" "$N"
    printf '  %s|%s  %s%d steps in %ds%s%-30s%s|%s\n' "$C" "$N" "$D" "$STEP_N" \
           "$(( $(date +%s) - T_START ))" "$N" "" "$C" "$N"
    printf '  %s+----------------------------------------------+%s\n' "$C" "$N"
    # The admin panel reads this line from a remote upgrade's log to know it worked.
    printf '%s%s is installed and working, version %s.%s\n' "$G" "$ROLE" "$VERSION" "$N"
else
    printf '%sSomething is off - see the failures above.%s\n' "$Y" "$N"
fi

if is_relay; then
    printf '
    Point your devices at this address for DNS:

        %s

    Set it as both primary and secondary. A different secondary is worse than
    none: the device will sometimes use it and quietly skip the bypass.

    Manage the list with:  smartdns status | list | add | del | bypass

' "$RELAY_IP"
elif [ "$ROLE" = node ]; then
    # A node's relays are the panel's, written by its sync: none are known here.
    printf '
    This node only accepts connections from the panel'"'"'s relays, so it is not an
    open proxy.

'
else
    printf '
    This exit only accepts connections from %s, so it is not an open proxy.
    Run the installer on the relay next, if you have not already.

' "$RELAY_IP"
fi

if [ -n "$ENFORCE_OUT" ]; then
    printf '    %sAccess control is on%s - only addresses registered in the panel get
    DNS, HTTP and HTTPS through this relay. Nobody is registered yet, so right
    now that is nobody: sign a customer up, give them a plan, and let them
    register their address from the customer panel.

    SSH is never gated, and the customer panel is on a port the gate does not
    touch - so a wrong allowlist cannot lock you out of either.

        smartdns-acl list               who is allowed, and what they have used
        smartdns-acl enforce status     which way the door is
        smartdns-acl enforce off        open it to everyone

' "$B" "$N"
fi

if [ -n "$USER_PANEL_OUT" ]; then
    printf '    %sCustomer panel%s - where people sign up, register the address the
    service works on, see what is left of their allowance, and send a payment
    receipt. It also shows them the DNS address to enter.

        %s

' "$B" "$N" "$USER_PANEL_OUT"
fi

if is_relay && [ -z "${PANEL_DOMAIN:-}" ]; then
    printf '    %sThere is no customer panel on this relay%s, because it has no
    certificate. That page asks for a password, and nothing asks for a
    password over plain http here - so it is not served at all rather than
    served unsafely. Nobody can sign up or register an address until you
    give this machine a domain:

        smartdns-cert panel.example.com

    then put PANEL_DOMAIN in /etc/smart-dns/sync.env and restart
    smartdns-sync.

' "$Y" "$N"
fi

if [ -n "$ADMIN_URL_OUT" ]; then
    printf '    %sAdmin panel%s - shown once. Only a hash of the password is stored,
    so it can be replaced but never read back. Write it down now.

        %s
        username: %s
        password: %s

' "$B" "$N" "$ADMIN_URL_OUT" "$ADMIN_USER_OUT" "$ADMIN_PASS_OUT"
elif [ -n "$ADMIN_USER_OUT" ]; then
    printf '    %sAdmin panel%s - it now asks for a username with the password:

        username: %s

    Change it in the panel'"'"'s settings, or with: smartdns-access username

' "$B" "$N" "$ADMIN_USER_OUT"
fi

if [ "$TUNNEL" = backpack ]; then
    printf '    %sTunnel%s - %s. The relay'"'"'s nginx goes through it, and
    straight to the exit only while it is down. Its log is in smartdns-logs.

' "$B" "$N" "$TUNNEL_OUT"
fi

if [ -n "$SYNC_TOKEN_OUT" ]; then
    printf '    %sPairing token%s - run the installer on the relay and paste this when
    it asks. It carries both the shared secret and the fingerprint of this
    machine'"'"'s certificate, so the relay will talk to this server and no other.

        %s

' "$B" "$N" "$SYNC_TOKEN_OUT"
fi

# The tunnel was asked again here: the relay has not heard yet, and it will not
# until it is given the token above.
if [ -n "${ASK_TUNNEL:-}" ] && [ "$ROLE" = exit ]; then
    printf '    %sNow the relay%s: run the installer there with --tunnel and paste the\n' "$Y" "$N"
    printf '    pairing token above. Until then it goes straight to this exit.\n\n'
fi

if [ -n "$JOINED" ]; then
    printf '    %sJoined%s - this single server'"'"'s customers, their quota and its settings
    are those of the panel on %s, and it shows on that panel'"'"'s Node page.

' "$B" "$N" "$PANEL_IP"
fi
if [ "$ROLE" = node ]; then
    printf '    %sNode%s - joined to the panel on %s. Its relays come from there, and a
    relay goes through this server once the admin panel'"'"'s Node page says so.

' "$B" "$N" "$PANEL_IP"
fi

printf '    Every command there is, in one menu:  %ssudo smartdns-menu%s\n\n' "$B" "$N"

exit 0
