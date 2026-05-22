#!/usr/bin/env bash
# Print a host IP reachable from other machines on the network (for UI URLs).
# Override with: export HOST_IP=10.0.0.5
if [[ -n "${HOST_IP:-}" ]]; then
  echo "${HOST_IP}"
  exit 0
fi
if command -v ip >/dev/null 2>&1; then
  ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}'
  exit 0
fi
if command -v ipconfig >/dev/null 2>&1; then
  for iface in en0 en1 eth0; do
    ipconfig getifaddr "${iface}" 2>/dev/null && exit 0
  done
fi
hostname -I 2>/dev/null | awk '{print $1}'
exit 0
