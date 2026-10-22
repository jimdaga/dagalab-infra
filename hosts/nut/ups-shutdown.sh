#!/bin/bash
# dagalab-ups-shutdown.service (root). Started by upssched after 10 min on battery.
# dagakube01: graceful cluster stop (lab-power.py --local), recording what it stopped in /var/lib/dagalab/ups-shutdown.json
#             (the auto-start marker); dagakube02/03: wait until every Longhorn volume is detached.
# Whatever happens (API down, timeout, error) the node powers off at the end. DRY_RUN=1: print instead of acting.
set -u
LP=/usr/local/lib/dagalab/lab-power.py
STATE=/var/lib/dagalab/ups-shutdown.json
dry() { [ "${DRY_RUN:-0}" = 1 ]; }
say() { logger -t dagalab-ups "$(dry && echo "[dry-run] ")$*"; }
say "cluster shutdown: starting on $(hostname)"
if [ "$(hostname)" = dagakube01 ]; then
  mkdir -p "$(dirname "$STATE")"
  if dry; then python3 "$LP" stop-workloads --local --dry-run --state "$STATE"
  else timeout 480 python3 "$LP" stop-workloads --local --state "$STATE" \
         || logger -t dagalab-ups "graceful stop failed or timed out (rc $?): powering off anyway"; fi
else
  if dry; then echo "[dry-run] python3 $LP wait-stopped --local --timeout 480"
  else python3 "$LP" wait-stopped --local --timeout 480 \
         || logger -t dagalab-ups "cluster not confirmed stopped: powering off anyway"; fi
fi
say "cluster shutdown: powering off $(hostname)"
if dry; then echo "[dry-run] systemctl poweroff"; else systemctl poweroff; fi
