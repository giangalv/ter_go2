# First console access over HDMI

Kept for reference. It was needed once, to enable SSH on a Jetson that had none.
If SSH already works, you do not need any of this.

HDMI monitor and USB keyboard on the robot's rear connectors.

**Keep the robot lying down** while doing this: standing still heats the rear
hip motors (76 °C observed against 33-42 °C for the other joints).

## Login

| | |
|---|---|
| user | `unitree` |
| password | `123` |
| `sudo` | same password |

The prompt reads `unitree@ter-go2-jetson` (it was `unitree@ubuntu` before the
rename). If those credentials fail, try `pi` / `123` or `root` / `123`.

## 1. Confirm it is the machine seen on DDS

```bash
hostname
pgrep -a uslam_server
uname -m                          # aarch64 means it is a Jetson
cat /etc/nv_tegra_release         # L4T / JetPack version
```

## 2. Why SSH is closed

Either the daemon is not running or a firewall is blocking it.

```bash
systemctl status ssh
sudo ss -tlnp | grep :22
sudo ufw status verbose
```

Enable it:

```bash
sudo apt list --installed 2>/dev/null | grep openssh-server
sudo systemctl enable --now ssh
sudo ufw allow 22/tcp             # only if ufw is active
```

Verify from the PC:

```bash
ssh unitree@192.168.123.18
```

## 3. Capture the network configuration

```bash
ip -br addr
ip route
nmcli device status
iw dev
nmcli connection show
```

Worth answering:

* which wired interface carries `192.168.123.18`?
* what is the wireless interface called?
* does it have a reachable address on Wi-Fi?

## 4. Find the onboard CycloneDDS configuration

```bash
find / -name 'cyclonedds*.xml' 2>/dev/null
env | grep -i cyclone
```

**Do not edit it before making a copy.** A broken DDS configuration leaves the
robot unreachable over the cable as well.

## If nothing appears on the monitor

No HDMI signal means the module is unpowered or absent. At that point the
question goes to the reseller with the robot's serial number: the configuration
sold states whether an Orin Nano/NX was included.
