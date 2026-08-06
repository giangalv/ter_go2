#!/usr/bin/env bash
# Recovers the USB Wi-Fi dongle when the Tegra xHCI controller wedges.
#
# Symptom, seen on this Jetson roughly 80 s after a cold boot:
#
#   tegra-xusb 3610000.xhci: WARN Set TR Deq Ptr cmd failed due to incorrect
#                            slot or ep state
#   tegra-xusb 3610000.xhci: controller error detected
#   tegra-xusb 3610000.xhci: Abort failed to stop command ring: -110
#
# After that the controller is dead: `lsusb` lists only the root hubs, every USB
# device disappears and `wlan0` with it. The dongle is fine — it is the host
# controller that stops responding.
#
# Unbinding and rebinding the platform driver reinitialises the controller and
# the dongle comes back, without a reboot. NetworkManager then reconnects on its
# own because the profile has autoconnect enabled.
#
# This watchdog checks periodically and resets only when the interface is
# actually missing, with a cooldown so it cannot spin.

IFACE="${GO2_WLAN_IFACE:-wlan0}"
XHCI="${GO2_XHCI:-3610000.xhci}"
DRV=/sys/bus/platform/drivers/tegra-xusb
INTERVAL="${GO2_WATCHDOG_INTERVAL:-30}"
COOLDOWN="${GO2_WATCHDOG_COOLDOWN:-180}"

log() { logger -t usb-wifi-watchdog "$*"; echo "$*"; }

if [ ! -d "$DRV" ]; then
    log "driver $DRV not found, nothing to watch"
    exit 1
fi

last_reset=0
log "watching $IFACE, resetting $XHCI when it disappears"

while true; do
    if ! ip link show "$IFACE" >/dev/null 2>&1; then
        now=$(date +%s)
        if [ $((now - last_reset)) -ge "$COOLDOWN" ]; then
            log "$IFACE is missing: resetting the USB controller $XHCI"
            echo "$XHCI" > "$DRV/unbind" 2>/dev/null
            sleep 3
            echo "$XHCI" > "$DRV/bind" 2>/dev/null
            last_reset=$now
            sleep 10
            if ip link show "$IFACE" >/dev/null 2>&1; then
                log "$IFACE is back"
            else
                log "$IFACE still missing after the reset"
            fi
        fi
    fi
    sleep "$INTERVAL"
done
