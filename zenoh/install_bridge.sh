#!/usr/bin/env bash
# Download the zenoh-bridge-ros2dds binary for this machine into zenoh/bin/.
# Run it once on the PC and once on the Jetson (needs internet):
#
#     ~/ter-home/ter_go2/zenoh/install_bridge.sh      # PC
#     ~/ter_go2/zenoh/install_bridge.sh               # Jetson
#
# The standalone binary, not the Docker image: no root, no docker group, and no
# container to give the right DDS configuration to.
#
# Why 1.9.0, and the `gnu` build on both machines. Highest GLIBC symbol each
# x86_64 / aarch64 `gnu` build needs (readelf -V), against what the machines have:
#
#   version   x86_64 (PC: Ubuntu 22.04, 2.35)   aarch64 (Jetson: Ubuntu 20.04, 2.31)
#   1.10.x    GLIBC_2.38  -> does not run       GLIBC_2.30
#   1.9.0     GLIBC_2.34                        GLIBC_2.30
#
# The `musl` builds are no way out: they are dynamically linked against the musl
# loader and against a musl libgcc_s, and with Ubuntu's `musl` package installed
# they still fail with "_Unwind_Backtrace: symbol not found".
# Both bridges must run the same version. See docs/remote-ros.md.

set -euo pipefail

VERSION="${ZENOH_BRIDGE_VERSION:-1.9.0}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/bin"

case "$(uname -m)" in
    x86_64)  TARGET=x86_64-unknown-linux-gnu ;;
    aarch64) TARGET=aarch64-unknown-linux-gnu ;;
    *) echo "install_bridge: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

ZIP="zenoh-plugin-ros2dds-${VERSION}-${TARGET}-standalone.zip"
URL="https://github.com/eclipse-zenoh/zenoh-plugin-ros2dds/releases/download/${VERSION}/${ZIP}"

mkdir -p "$BIN"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "install_bridge: downloading $ZIP"
curl -fsSL --retry 3 -o "$TMP/$ZIP" "$URL"
unzip -q -o "$TMP/$ZIP" -d "$TMP/x"
install -m 0755 "$TMP/x/zenoh-bridge-ros2dds" "$BIN/zenoh-bridge-ros2dds"

"$BIN/zenoh-bridge-ros2dds" --version
