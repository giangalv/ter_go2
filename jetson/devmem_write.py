#!/usr/bin/env python3
"""Write a 32-bit value to a physical address, like `busybox devmem ADDR w VALUE`.

    sudo devmem_write 0x02430030 0x004

Installed on the Jetson as /usr/local/sbin/devmem_write, called from
/etc/rc.local to switch on the USB-C port.

The Unitree factory /etc/rc.local enables the USB-C port in two halves: a
pinmux write with `busybox devmem 0x02430030 w 0x004`, then GPIO PP.06 driven
high. busybox is not installed on this Jetson, so the first line fails at every
boot (`/etc/rc.local: 1: busybox: not found`) while the GPIO lines still run.
The port stays off and anything plugged into it is invisible.

Measured on 2026-10-07: the register reads 0x20 after boot and the RealSense
D435i on the USB-C port does not enumerate. After writing 0x004 it appears on
the USB 3 bus (8086:0b3a) within three seconds.

Exits non-zero if the value read back differs from the one written.
"""

import mmap
import os
import struct
import sys


def main():
    if len(sys.argv) != 3:
        print(f'usage: {sys.argv[0]} ADDRESS VALUE', file=sys.stderr)
        return 2

    addr = int(sys.argv[1], 0)
    value = int(sys.argv[2], 0)
    page = addr & ~(mmap.PAGESIZE - 1)
    off = addr - page

    fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
    try:
        mem = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED,
                        mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        before = struct.unpack('<I', mem[off:off + 4])[0]
        mem[off:off + 4] = struct.pack('<I', value)
        after = struct.unpack('<I', mem[off:off + 4])[0]
        mem.close()
    finally:
        os.close(fd)

    print(f'{addr:#010x}: {before:#x} -> {after:#x}')
    return 0 if after == value else 1


if __name__ == '__main__':
    sys.exit(main())
