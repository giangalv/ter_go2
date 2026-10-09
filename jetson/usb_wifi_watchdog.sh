#!/usr/bin/env bash
# Recovers the USB Wi-Fi dongle when the Tegra xHCI controller wedges,
# without taking the rest of the USB bus down when it does not have to.
#
# Symptom this was written for, seen on this Jetson roughly 80 s after a cold
# boot:
#
#   tegra-xusb 3610000.xhci: WARN Set TR Deq Ptr cmd failed due to incorrect
#                            slot or ep state
#   tegra-xusb 3610000.xhci: controller error detected
#   tegra-xusb 3610000.xhci: Abort failed to stop command ring: -110
#
# After that the controller is dead: `lsusb` lists only the root hubs, every USB
# device disappears and `wlan0` with it. The dongle is fine — it is the host
# controller that stops responding. Unbinding and rebinding the platform driver
# reinitialises the controller and the dongle comes back, without a reboot.
# NetworkManager then reconnects on its own because the profile has autoconnect
# enabled.
#
# Why it no longer resets the controller every time wlan0 is missing
# (2026-10-08): the RealSense on the USB-C port hangs off the same, only
# controller, and every reset took it down as well. That morning the dongle
# itself kept dropping off the bus and re-enumerating every 3-15 s from 27 s
# after boot, with no kernel error at all, while the controller was healthy;
# the old watchdog answered with full resets (at 51 s, 126 s, 350 s) and the
# third left neither the dongle nor the camera on the bus. So, when wlan0 is
# missing:
#
#   1. wait GRACE seconds: a dongle that dropped off re-enumerates by itself;
#   2. controller dead (no USB device left on it, or the kernel reported it):
#      reset the controller, as before — the camera goes down too, but it was
#      gone already;
#   3. dongle on the bus but no wlan0: reload its driver only;
#   4. dongle off the bus, controller alive: leave the controller alone and say
#      so. That is a dongle or cable problem, and a reset would only add the
#      camera to the casualties.
#
# All with a cooldown so it cannot spin.

IFACE="${GO2_WLAN_IFACE:-wlan0}"
XHCI="${GO2_XHCI:-3610000.xhci}"
DRV=/sys/bus/platform/drivers/tegra-xusb
DONGLE_ID="${GO2_WLAN_USB_ID:-2357:012d}"      # TP-Link 802.11ac NIC (RTL88x2BU)
WLAN_DRIVER="${GO2_WLAN_DRIVER:-88x2bu}"
INTERVAL="${GO2_WATCHDOG_INTERVAL:-30}"
GRACE="${GO2_WATCHDOG_GRACE:-20}"
COOLDOWN="${GO2_WATCHDOG_COOLDOWN:-180}"

log() { logger -t usb-wifi-watchdog "$*"; echo "$*"; }

iface_up() { ip link show "$IFACE" >/dev/null 2>&1; }

dongle_on_bus() {
    local d
    for d in /sys/bus/usb/devices/*; do
        [ -f "$d/idVendor" ] || continue
        [ "$(cat "$d/idVendor"):$(cat "$d/idProduct")" = "$DONGLE_ID" ] && return 0
    done
    return 1
}

# Dead = no USB device left on the controller besides the root hubs, or the
# kernel said so since the last look.
controller_dead() {
    local since="$1" d
    if journalctl -k --since "@$since" --no-pager -o cat 2>/dev/null |
            grep -qE "HC died|controller error detected|Abort failed to stop command ring"; then
        return 0
    fi
    for d in /sys/bus/usb/devices/[0-9]*-[0-9]*; do
        [ -e "$d" ] && return 1
    done
    return 0
}

if [ ! -d "$DRV" ]; then
    log "driver $DRV not found, nothing to watch"
    exit 1
fi

last_action=0
last_look=$(date +%s)
quiet_until=0
log "watching $IFACE (dongle $DONGLE_ID); controller $XHCI is reset only when it is dead"

while true; do
    if ! iface_up; then
        for i in $(seq 1 "$GRACE"); do
            sleep 1
            iface_up && break
        done
    fi

    now=$(date +%s)
    if ! iface_up && [ $((now - last_action)) -ge "$COOLDOWN" ]; then
        if controller_dead "$last_look"; then
            log "$IFACE missing and the controller is dead: resetting $XHCI (anything else on USB goes down with it)"
            echo "$XHCI" > "$DRV/unbind" 2>/dev/null
            sleep 3
            echo "$XHCI" > "$DRV/bind" 2>/dev/null
            last_action=$now
            sleep 10
            iface_up && log "$IFACE is back" || log "$IFACE still missing after the reset"
        elif dongle_on_bus; then
            log "dongle on the bus but no $IFACE: reloading the $WLAN_DRIVER driver only"
            modprobe -r "$WLAN_DRIVER" && sleep 2 && modprobe "$WLAN_DRIVER"
            last_action=$now
            sleep 10
            iface_up && log "$IFACE is back" || log "$IFACE still missing after the driver reload"
        elif [ "$now" -ge "$quiet_until" ]; then
            log "dongle off the bus, controller alive: not resetting it (the RealSense would go down too); check the dongle and its connector"
            quiet_until=$((now + 300))
        fi
    fi
    last_look=$now
    sleep "$INTERVAL"
done
