#!/usr/bin/env bash
# SEAL-1 live check. Read-only: it runs only `docker ps`, `docker inspect` and
# `docker network inspect`, and changes nothing. Run it on the host after every
# deploy of Base, Tiger, Radix or Rig:
#   bash ops/seal-check.sh
#
# For every running container labelled coolify.applicationUuid, it lists the
# container's networks and their Internal flag. A container that joins an
# internal network must not also join a non-internal one, except the declared
# corridor services (matched by compose service name):
#   agent-base, base-brain and base-browser-gateway on tervory-gw-base;
#   tiger-brain and tiger-browser-gateway on tervory-gw-tiger;
#   zenas-brain on this box's tervory-gw-zenas;
#   agent-rig, rig-brain and rig-browser-gateway on tervory-gw-rig;
#   egress-proxy on egress (compose names it <project>_egress).
# Trigger and Radix passive-egress use the exact compose attachments below;
# their private networks must remain internal and extra attachments always fail.
#
# Rooms are found by labels, never by container name. Coolify's normal mode names
# containers <service>-<uuid>-<timestamp>; Raw Compose mode names them
# <uuid>-<service>-1 and sets no coolify.resourceName. So containers are grouped
# by the coolify.applicationUuid label, and a Coolify app is a room when its
# coolify.resourceName starts with "agent-" or, failing that, when one of its
# compose services (com.docker.compose.service) is named "agent-<room>". That
# name is the room's name. Other apps, such as the main Tank stack, are the hub
# every corridor connects to by design; they print "not a room, skipped".
#
# Prints one line per room: "<room> (<uuid>): sealed" or
# "<room> (<uuid>): LEAK: <container> on <network>" (several leaks are joined by "; ").
# A room with no container on an internal network prints "no sealed containers".
# The four existing sealed rooms plus Autumbra Watch and Enforce can never be skipped:
# if one is missing it prints "<room>: NOT FOUND", and if one has no container on
# an internal network it prints "<room> (<uuid>): NOT SEALED: no container on an
# internal network". Both fail.
# Exit 0 when every room is sealed, 1 on any leak, NOT FOUND or NOT SEALED,
# 2 when the check cannot run.
set -uo pipefail

DOCKER="${DOCKER:-docker}"

# Rooms whose compose files declare sealed services; each must be found and sealed.
EXPECTED_SEALED=(agent-base agent-radix agent-tiger agent-rig agent-ghost agent-tisan)
# Watch is confined to its declared corridor; each Enforce has exactly one internal network.

# service name -> the one non-internal network it may also join
declare -A CORRIDOR=(
  [agent-base]="tervory-gw-base"
  [base-brain]="tervory-gw-base"
  [base-browser-gateway]="tervory-gw-base"
  [tiger-browser-gateway]="tervory-gw-tiger"
  [tiger-brain]="tervory-gw-tiger"
  [market-lab-brain]="tervory-gw-market_lab"
  [market-lab-browser-gateway]="tervory-gw-market_lab"
  [radix-brain]="tervory-gw-radix"
  [radix-browser-gateway]="tervory-gw-radix"
  [tisan-brain]="tervory-gw-tisan"
  [tisan-browser-gateway]="tervory-gw-tisan"
  [trigger-brain]="tervory-gw-trigger"
  [trigger-browser-gateway]="tervory-gw-trigger"
  [ghost-brain]="tervory-gw-ghost"
  [zenas-brain]="tervory-gw-zenas"
  [agent-rig]="tervory-gw-rig"
  [rig-brain]="tervory-gw-rig"
  [rig-browser-gateway]="tervory-gw-rig"
  [egress-proxy]="egress"
)

if ! ids="$("$DOCKER" ps -q --filter label=coolify.applicationUuid 2>&1)"; then
  echo "seal-check: docker ps failed: $ids" >&2
  exit 2
fi
if [[ -z "$ids" ]]; then
  echo "seal-check: no running Coolify containers found; nothing was checked." >&2
  exit 2
fi

declare -A INTERNAL=()     # network -> true|false
declare -A APP_RESOURCE=() # uuid -> coolify.resourceName (empty in raw mode)
declare -A APP_ROOM=()     # uuid -> agent-* compose service name, if any
declare -A APP_LEAKS=()    # uuid -> leak text
declare -A APP_SEALED=()   # uuid -> number of containers on an internal network
declare -A APP_SERVICES=() # uuid/service -> 1
APPS=()
FORMAT='{{index .Config.Labels "coolify.applicationUuid"}}|{{index .Config.Labels "coolify.resourceName"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.project"}}|{{.Name}}|{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'

# Identify raw per-client Enforce projects before validating their proxy services.
declare -A ENFORCE_PROJECTS=() CONTAINER_LINES=()
enforce_present=0
for id in $ids; do
  if ! line="$("$DOCKER" inspect -f "$FORMAT" "$id" 2>&1)"; then
    echo "seal-check: docker inspect $id failed: $line" >&2
    exit 2
  fi
  IFS='|' read -r uuid resource service project name networks <<<"$line"
  CONTAINER_LINES[$id]="$line"
  if [[ "$service" == enforce ]]; then
    ENFORCE_PROJECTS[$project]=1
    enforce_present=1
  fi
done
if (( enforce_present )); then
  EXPECTED_SEALED+=(autumbra-enforce)
fi

FLAG=""
internal_flag() {  # sets FLAG; no subshell, so the cache and exit 2 both hold
  local net="$1" out
  if [[ -z "${INTERNAL[$net]+set}" ]]; then
    if ! out="$("$DOCKER" network inspect -f '{{.Internal}}' "$net" 2>&1)"; then
      echo "seal-check: docker network inspect $net failed: $out" >&2
      exit 2
    fi
    INTERNAL[$net]="$out"
  fi
  FLAG="${INTERNAL[$net]}"
}

for id in $ids; do
  line="${CONTAINER_LINES[$id]}"
  IFS='|' read -r uuid resource service project name networks <<<"$line"
  name="${name#/}"
  if [[ -z "$uuid" ]]; then
    echo "seal-check: container $id has an empty coolify.applicationUuid label" >&2
    exit 2
  fi
  if [[ -z "${APP_SEALED[$uuid]+set}" ]]; then
    APPS+=("$uuid")
    APP_RESOURCE[$uuid]=""
    APP_ROOM[$uuid]=""
    APP_LEAKS[$uuid]=""
    APP_SEALED[$uuid]=0
  fi
  [[ -n "$resource" ]] && APP_RESOURCE[$uuid]="$resource"
  APP_SERVICES["$uuid/$service"]=1
  if [[ "$service" == enforce && -z "${APP_ROOM[$uuid]}" ]]; then
    APP_ROOM[$uuid]="autumbra-enforce"
  fi
  if [[ "$service" == agent-* && -z "${APP_ROOM[$uuid]}" ]]; then
    APP_ROOM[$uuid]="$service"
  fi
  if [[ "$service" == "enforce" ]]; then
    APP_ROOM[$uuid]="autumbra-enforce"
  fi
  on_internal=0
  outward=()
  for net in $networks; do
    internal_flag "$net"
    if [[ "$FLAG" == "true" ]]; then
      on_internal=1
    else
      outward+=("$net")
    fi
  done
  if [[ "$service" == "enforce" ]]; then
    read -ra attached <<<"$networks"
    if [[ "${#attached[@]}" -ne 1 ]] || (( ! on_internal )); then
      APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }LEAK: $name must have exactly one internal network"
    fi
  fi
  if [[ -n "${ENFORCE_PROJECTS[$project]+set}" ]]; then
    logical_expected=""
    case "$service" in
      enforce) logical_expected="enforce_internal" ;;
      egress-proxy) logical_expected="enforce_internal egress" ;;
      health-observer) logical_expected="health_internal" ;;
      health-egress-proxy) logical_expected="health_internal health_egress" ;;
    esac
    if [[ -n "$logical_expected" ]]; then
      seen=""
      for net in $networks; do
        if ! meta="$("$DOCKER" network inspect -f '{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.network"}}' "$net" 2>&1)"; then
          echo "seal-check: docker network inspect $net failed: $meta" >&2
          exit 2
        fi
        IFS='|' read -r net_project logical <<<"$meta"
        internal_flag "$net"
        expected_internal=true
        [[ "$logical" == egress || "$logical" == health_egress ]] && expected_internal=false
        if [[ "$net_project" != "$project" || " $logical_expected " != *" $logical "* || "$FLAG" != "$expected_internal" || " $seen " == *" $logical "* ]]; then
          APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }LEAK: $name on $net"
        fi
        seen+=" $logical"
      done
      for logical in $logical_expected; do
        if [[ " $seen " != *" $logical "* ]]; then
          APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }NOT SEALED: $name missing $logical"
        fi
      done
      (( on_internal )) && APP_SEALED[$uuid]=$(( APP_SEALED[$uuid] + 1 ))
      continue
    fi
  fi
  # Check exact compose attachments even if a required internal network was removed.
  # Rig's public API keeps its existing app proxy network; Trigger gets only its
  # corridor/checkers, and Radix passive-egress gets only gw/proxy-link/egress.
  expected_nets=""
  case "$service" in
    agent-ghost|ghost-browser) expected_nets="tervory-gw-ghost" ;;
    agent-tisan) expected_nets="tervory-gw-tisan ${project}_figma_internal" ;;
    figma-egress-proxy) expected_nets="${project}_figma_internal ${project}_egress" ;;
    agent-trigger) expected_nets="tervory-gw-trigger ${project}_checkers" ;;
    trigger-languagetool) expected_nets="${project}_checkers" ;;
    passive-egress) expected_nets="tervory-gw-radix ${project}_proxy-link ${project}_egress" ;;
    agent-rig) expected_nets="tervory-gw-rig 59f8cnpelzwsyte1atyjc4fv" ;;
    rig-brain) expected_nets="tervory-gw-rig ${project}_sandbox ${project}_forge-control" ;;
    tiger-brain) expected_nets="tervory-gw-tiger ${project}_sandbox" ;;
    agent-zenas) expected_nets="tervory-gw-zenas" ;;
    market-lab-brain) expected_nets="tervory-gw-market_lab ${project}_sandbox" ;;
    market-lab-sandbox) expected_nets="${project}_sandbox" ;;
    market-lab-browser) expected_nets="${project}_browser" ;;
    market-lab-browser-gateway) expected_nets="tervory-gw-market_lab ${project}_browser" ;;
    radix-brain) expected_nets="tervory-gw-radix ${project}_sandbox" ;;
    radix-sandbox) expected_nets="${project}_sandbox" ;;
    radix-browser) expected_nets="${project}_browser" ;;
    radix-browser-gateway) expected_nets="tervory-gw-radix ${project}_browser" ;;
    tisan-brain) expected_nets="tervory-gw-tisan ${project}_sandbox" ;;
    tisan-sandbox) expected_nets="${project}_sandbox" ;;
    tisan-browser) expected_nets="${project}_browser" ;;
    tisan-browser-gateway) expected_nets="tervory-gw-tisan ${project}_browser" ;;
    trigger-brain) expected_nets="tervory-gw-trigger ${project}_sandbox" ;;
    trigger-sandbox) expected_nets="${project}_sandbox" ;;
    trigger-browser) expected_nets="${project}_browser" ;;
    trigger-browser-gateway) expected_nets="tervory-gw-trigger ${project}_browser" ;;
    ghost-brain) expected_nets="tervory-gw-ghost ${project}_sandbox" ;;
    ghost-sandbox) expected_nets="${project}_sandbox" ;;
    zenas-brain) expected_nets="tervory-gw-zenas ${project}_sandbox" ;;
    rig-forge) expected_nets="${project}_forge-control ${project}_forge-egress" ;;
    rig-forge-egress) expected_nets="${project}_forge-egress ${project}_forge-outbound" ;;
    rig-sandbox) expected_nets="${project}_sandbox" ;;
    tiger-sandbox) expected_nets="${project}_sandbox" ;;
    zenas-sandbox) expected_nets="${project}_sandbox" ;;
    rig-browser) expected_nets="${project}_browser" ;;
    rig-browser-gateway) expected_nets="tervory-gw-rig ${project}_browser" ;;
  esac
  if [[ -n "$expected_nets" ]]; then
    for net in $networks; do
      if [[ " $expected_nets " != *" $net "* ]]; then
        APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }LEAK: $name on $net"
      fi
    done
    for net in $expected_nets; do
      if [[ " $networks " != *" $net "* ]]; then
        APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }NOT SEALED: $name missing $net"
      elif [[ "$service" == "agent-ghost" || "$service" == "ghost-browser" ]]; then
        continue
      elif [[ "$service" == "agent-trigger" && "$net" == "tervory-gw-trigger" ]] ||
           [[ "$service" == "passive-egress" && "$net" == "${project}_egress" ]] ||
           [[ "$service" == "agent-tisan" && "$net" == "tervory-gw-tisan" ]] ||
           [[ "$service" == "figma-egress-proxy" && "$net" == "${project}_egress" ]]; then
        continue
      elif [[ ( "$service" == "market-lab-brain" || "$service" == "market-lab-browser-gateway" ) && "$net" == "tervory-gw-market_lab" ]]; then
        continue
      elif [[ ( "$service" == "radix-brain" || "$service" == "radix-browser-gateway" ) && "$net" == "tervory-gw-radix" ]]; then
        continue
      elif [[ ( "$service" == "tisan-brain" || "$service" == "tisan-browser-gateway" ) && "$net" == "tervory-gw-tisan" ]]; then
        continue
      elif [[ ( "$service" == "trigger-brain" || "$service" == "trigger-browser-gateway" ) && "$net" == "tervory-gw-trigger" ]]; then
        continue
      elif [[ ( "$service" == "ghost-brain" ) && "$net" == "tervory-gw-ghost" ]]; then
        continue
      elif [[ "$service" == "tiger-brain" && "$net" == "tervory-gw-tiger" ]]; then
        continue
      elif [[ ( "$service" == "agent-zenas" || "$service" == "zenas-brain" ) && "$net" == "tervory-gw-zenas" ]]; then
        continue
      elif [[ "$service" != "agent-rig" && "$net" != "tervory-gw-rig" && !( "$service" == "rig-forge-egress" && "$net" == "${project}_forge-outbound" ) ]]; then
        internal_flag "$net"
        if [[ "$FLAG" != "true" ]]; then
          APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }NOT SEALED: $name on non-internal $net"
        fi
      fi
    done
    # The exact-network check above already accounts for each attachment.
    if (( on_internal )) || [[ "$service" == "agent-ghost" || "$service" == "ghost-browser" ]]; then
      APP_SEALED[$uuid]=$(( APP_SEALED[$uuid] + 1 ))
    fi
    continue
  fi
  (( on_internal )) || continue
  APP_SEALED[$uuid]=$(( APP_SEALED[$uuid] + 1 ))
  allowed="${CORRIDOR[$service]:-}"
  for net in "${outward[@]}"; do
    if [[ -n "$allowed" && ( "$net" == "$allowed" || "$net" == "${project}_${allowed}" ) ]]; then
      continue
    fi
    leak="LEAK: $name on $net"
    APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }$leak"
  done
done

# The room name comes from the app: its Coolify resource name when that is an
# agent-* name, otherwise its agent-* compose service.
declare -A ROOM_NAME=()  # uuid -> room name
declare -A FOUND=()      # room name -> 1
ROOMS=()
status=0
for uuid in "${APPS[@]}"; do
  resource="${APP_RESOURCE[$uuid]}"
  if [[ "$resource" == agent-* ]]; then
    room="$resource"
  else
    room="${APP_ROOM[$uuid]}"
  fi
  if [[ "$resource" == autumbra-enforce* || "$room" == autumbra-enforce* ]]; then
    room="autumbra-enforce"
  elif [[ "$resource" == autumbra-watch* || "$room" == agent-ghost* ]]; then
    room="agent-ghost"
  fi
  if [[ -z "$room" ]]; then
    echo "${resource:-$uuid} ($uuid): not a room, skipped"
    continue
  fi
  if [[ -n "${APP_SERVICES[$uuid/enforce]+set}" ]]; then
    FOUND[autumbra-enforce]=1
  fi
  ROOM_NAME[$uuid]="$room"
  FOUND[$room]=1
  ROOMS+=("$uuid")
done

is_expected() {
  local want
  for want in "${EXPECTED_SEALED[@]}"; do
    [[ "$want" == "$1" ]] && return 0
  done
  return 1
}

for uuid in "${ROOMS[@]}"; do
  room="${ROOM_NAME[$uuid]}"
  required_services=""
  if [[ "$room" == "agent-tiger" && ( -n "${APP_SERVICES[$uuid/tiger-brain]+set}" || -n "${APP_SERVICES[$uuid/tiger-sandbox]+set}" ) ]]; then
    required_services="tiger-brain tiger-sandbox"
  fi
  if [[ ( "$room" == "agent-zenas" || "$room" == "agent-zenas-za" ) && ( -n "${APP_SERVICES[$uuid/zenas-brain]+set}" || -n "${APP_SERVICES[$uuid/zenas-sandbox]+set}" ) ]]; then
    required_services="zenas-brain zenas-sandbox"
  fi
  [[ "$room" == "agent-ghost" ]] && required_services+=" agent-ghost ghost-browser"
  if [[ "$room" == "agent-market-lab" && ( -n "${APP_SERVICES[$uuid/market-lab-brain]+set}" || -n "${APP_SERVICES[$uuid/market-lab-sandbox]+set}" || -n "${APP_SERVICES[$uuid/market-lab-browser]+set}" || -n "${APP_SERVICES[$uuid/market-lab-browser-gateway]+set}" ) ]]; then
    required_services+=" market-lab-brain market-lab-sandbox market-lab-browser market-lab-browser-gateway"
  fi
  if [[ "$room" == "agent-radix" && ( -n "${APP_SERVICES[$uuid/radix-brain]+set}" || -n "${APP_SERVICES[$uuid/radix-sandbox]+set}" || -n "${APP_SERVICES[$uuid/radix-browser]+set}" || -n "${APP_SERVICES[$uuid/radix-browser-gateway]+set}" ) ]]; then
    required_services+=" radix-brain radix-sandbox radix-browser radix-browser-gateway"
  fi
  if [[ "$room" == "agent-tisan" && ( -n "${APP_SERVICES[$uuid/tisan-brain]+set}" || -n "${APP_SERVICES[$uuid/tisan-sandbox]+set}" || -n "${APP_SERVICES[$uuid/tisan-browser]+set}" || -n "${APP_SERVICES[$uuid/tisan-browser-gateway]+set}" ) ]]; then
    required_services+=" tisan-brain tisan-sandbox tisan-browser tisan-browser-gateway"
  fi
  if [[ "$room" == "agent-trigger" && ( -n "${APP_SERVICES[$uuid/trigger-brain]+set}" || -n "${APP_SERVICES[$uuid/trigger-sandbox]+set}" || -n "${APP_SERVICES[$uuid/trigger-browser]+set}" || -n "${APP_SERVICES[$uuid/trigger-browser-gateway]+set}" ) ]]; then
    required_services+=" trigger-brain trigger-sandbox trigger-browser trigger-browser-gateway"
  fi
  if [[ "$room" == "agent-ghost" && ( -n "${APP_SERVICES[$uuid/ghost-brain]+set}" || -n "${APP_SERVICES[$uuid/ghost-sandbox]+set}" ) ]]; then
    required_services+=" ghost-brain ghost-sandbox"
  fi
  if [[ "$room" == "autumbra-enforce" || -n "${APP_SERVICES[$uuid/enforce]+set}" ]]; then
    required_services+=" enforce egress-proxy health-observer health-egress-proxy"
  fi
  for required in $required_services; do
    if [[ -z "${APP_SERVICES[$uuid/$required]+set}" ]]; then
      APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }NOT FOUND: $required"
    fi
  done
  if [[ "$room" == "agent-rig" ]]; then
    for required in agent-rig rig-brain rig-sandbox rig-browser rig-browser-gateway rig-forge rig-forge-egress; do
      if [[ -z "${APP_SERVICES[$uuid/$required]+set}" ]]; then
        APP_LEAKS[$uuid]="${APP_LEAKS[$uuid]:+${APP_LEAKS[$uuid]}; }NOT FOUND: $required"
      fi
    done
  fi
  if [[ -n "${APP_LEAKS[$uuid]}" ]]; then
    echo "$room ($uuid): ${APP_LEAKS[$uuid]}"
    status=1
  elif (( APP_SEALED[$uuid] > 0 )); then
    echo "$room ($uuid): sealed"
  elif is_expected "$room"; then
    echo "$room ($uuid): NOT SEALED: no container on an internal network"
    status=1
  else
    echo "$room ($uuid): no sealed containers"
  fi
done
for room in "${EXPECTED_SEALED[@]}"; do
  if [[ -z "${FOUND[$room]+set}" ]]; then
    echo "$room: NOT FOUND"
    status=1
  fi
done

exit "$status"
