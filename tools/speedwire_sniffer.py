#!/usr/bin/env python3
"""Minimal SMA Speedwire multicast sniffer for SMA Energy Meter / Sunny Home Manager.

Listens on 239.12.255.254:9524, validates the "SMA\\0" magic, extracts the
device serial number and decodes the active-power OBIS channels 1.4.0 / 2.4.0.

Stdlib only. Tested against the public SMA Energy Meter protocol (0x6069) and
the Sunny Home Manager extended variant (0x6081).
"""

import argparse
import socket
import struct
import sys
import time

GROUP = "239.12.255.254"
PORT = 9524
MAGIC = b"SMA\x00"
READ_TIMEOUT = 10.0

TAG_DATA2 = 0x0010
PROTO_EMETER = 0x6069          # SMA Energy Meter / SHM 2.0
PROTO_EXT_EMETER = 0x6081      # Sunny Home Manager extended emeter telegram

POWER_DIVISOR = 10.0           # raw 0.1 W -> W
ENERGY_DIVISOR = 3_600_000.0   # raw Ws    -> kWh

# OBIS channel byte 0x90 carries the firmware version in EM telegrams.
CHANNEL_FIRMWARE = 0x90
# Standard grid-meter telegrams use channel 0x00; 0x90 is firmware/version data.
STANDARD_CHANNELS = {0x00, 0x01, CHANNEL_FIRMWARE}


def iter_tag_packets(data):
    """Yield (offset, tag_id, tag_length, tag_end) for each Speedwire tag."""
    offset = len(MAGIC)
    while offset + 4 <= len(data):
        tag_len, tag_id = struct.unpack_from(">HH", data, offset)
        if tag_id == 0 and tag_len == 0:          # end-of-data tag
            return
        end = offset + 4 + tag_len
        if end > len(data):
            return
        yield offset, tag_id, tag_len, end
        offset = end


def find_emeter_payload(data):
    """Return (protocol_id, payload_offset, payload_end) or None."""
    for offset, tag_id, tag_len, end in iter_tag_packets(data):
        if tag_id != TAG_DATA2 or tag_len < 2:
            continue
        proto = struct.unpack_from(">H", data, offset + 4)[0]
        if proto == PROTO_EMETER:
            payload = offset + 6                 # tag hdr + protocol id
        elif proto == PROTO_EXT_EMETER:
            payload = offset + 8                 # + longwords + control byte
        else:
            continue
        if payload + 10 > end:
            continue
        return proto, payload, end
    return None


def obis_name(index, value_type, tariff):
    """SMA writes the OBIS triplet as index.type.tariff (type is also length)."""
    return f"{index}.{value_type}.{tariff}"


def parse_emeter(data, payload, end):
    """Decode SUSY id, serial, timestamp and all OBIS channels."""
    susy_id, serial, timestamp = struct.unpack_from(">HII", data, payload)
    channels = []
    pos = payload + 10

    while pos + 4 <= end:
        ident = struct.unpack_from(">I", data, pos)[0]
        if ident == 0:                            # OBIS end marker
            break

        channel, index, value_type, tariff = data[pos:pos + 4]

        if channel == CHANNEL_FIRMWARE:
            length = 8                            # 4-byte header + 4-byte version
        elif value_type in (4, 8):
            length = 4 + value_type
        else:
            break                                 # unknown layout, stop safely

        if pos + length > end:
            break

        raw = int.from_bytes(data[pos + 4:pos + length], "big")
        channels.append({
            "channel": channel,
            "index": index,
            "type": value_type,
            "tariff": tariff,
            "obis": obis_name(index, value_type, tariff),
            "raw": raw,
            "bytes": data[pos + 4:pos + length],
        })
        pos += length

    return susy_id, serial, timestamp, channels


def format_firmware(raw_bytes):
    if len(raw_bytes) != 4:
        return "?"
    return f"{raw_bytes[0]}.{raw_bytes[1]:02d}.{raw_bytes[2]:02d}.{chr(raw_bytes[3])}"


def decode(data):
    """Return a decoded telegram dict, or None for non-meter packets."""
    if not data.startswith(MAGIC):
        return None
    found = find_emeter_payload(data)
    if found is None:
        return None
    proto, payload, end = found
    susy_id, serial, timestamp, channels = parse_emeter(data, payload, end)
    return {
        "protocol": proto,
        "susy_id": susy_id,
        "serial": serial,
        "timestamp": timestamp,
        "channels": channels,
    }


def find(channels, obis):
    for channel in channels:
        if channel["obis"] == obis and channel["channel"] != CHANNEL_FIRMWARE:
            return channel
    return None


def print_telegram(sender, payload_len, telegram, show_all):
    proto = telegram["protocol"]
    proto_name = "SMA Energy Meter" if proto == PROTO_EMETER else "SMA Sunny Home Manager (extended)"
    stamp = time.strftime("%H:%M:%S")

    print(
        f"[{stamp}] {sender[0]}:{sender[1]}  len={payload_len}  "
        f"proto=0x{proto:04X}  susy={telegram['susy_id']}  "
        f"serial={telegram['serial']}"
    )
    print(f"           protocol: {proto_name} (0x{proto:04X})")

    p_import = find(telegram["channels"], "1.4.0")
    p_export = find(telegram["channels"], "2.4.0")

    import_w = p_import["raw"] / POWER_DIVISOR if p_import else None
    export_w = p_export["raw"] / POWER_DIVISOR if p_export else None

    if import_w is None and export_w is None:
        print("           active power: 1.4.0 / 2.4.0 not present in this telegram")
    else:
        net_w = (import_w or 0.0) - (export_w or 0.0)
        direction = "consumption" if net_w >= 0 else "feed-in"
        parts = []
        if import_w is not None:
            parts.append(f"import 1.4.0 = {import_w:.1f} W")
        if export_w is not None:
            parts.append(f"export 2.4.0 = {export_w:.1f} W")
        print("           active power: " + "  |  ".join(parts) +
              f"  |  net = {net_w:+.1f} W ({direction})")

    e_import = find(telegram["channels"], "1.8.0")
    e_export = find(telegram["channels"], "2.8.0")
    if e_import or e_export:
        parts = []
        if e_import:
            parts.append(f"1.8.0 = {e_import['raw'] / ENERGY_DIVISOR:.3f} kWh")
        if e_export:
            parts.append(f"2.8.0 = {e_export['raw'] / ENERGY_DIVISOR:.3f} kWh")
        print("           energy:        " + "  |  ".join(parts))

    firmware = [c for c in telegram["channels"] if c["channel"] == CHANNEL_FIRMWARE]
    for fw in firmware:
        print(f"           firmware:      {format_firmware(fw['bytes'])}")

    # Non-standard channels are manufacturer extensions; on SHM telegrams these
    # are the only place battery/vendor data can appear.
    extended = [c for c in telegram["channels"] if c["channel"] not in STANDARD_CHANNELS]
    if extended:
        print("           manufacturer/extended OBIS channels:")
        for c in extended:
            print(
                f"             channel=0x{c['channel']:02X}  "
                f"{c['obis']} = {c['raw']} (raw)"
            )
        print("           battery-related: possible (vendor channels present; "
              "no standardized battery OBIS in EM telegrams)")
    else:
        print("           battery-related: none (standard grid-meter telegram, "
              "no vendor/battery OBIS channels)")

    if show_all:
        print("           all OBIS channels:")
        for c in telegram["channels"]:
            print(
                f"             0x{c['channel']:02X}: {c['obis']} = {c['raw']}"
            )


def open_socket(port, interfaces):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", port))                         # required: all interfaces, port 9524

    group = socket.inet_aton(GROUP)
    for interface in interfaces:
        try:
            mreq = struct.pack("4s4s", group, socket.inet_aton(interface))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            print(f"joined {GROUP}:{port} on {interface}")
        except OSError as exc:
            print(f"warning: cannot join {GROUP} on {interface}: {exc}", file=sys.stderr)

    sock.settimeout(READ_TIMEOUT)
    return sock


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="Sniff SMA Speedwire multicast telegrams.")
    parser.add_argument(
        "--interface", action="append", metavar="LOCAL_IP",
        help="local interface IP to join on (repeatable). Default: 0.0.0.0 (INADDR_ANY).",
    )
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--count", type=int, default=0,
                        help="exit after N valid telegrams (0 = run forever).")
    parser.add_argument("--all", action="store_true", dest="show_all",
                        help="also dump every OBIS channel in the telegram.")
    args = parser.parse_args()

    interfaces = args.interface or ["0.0.0.0"]
    sock = open_socket(args.port, interfaces)

    print(f"listening for SMA Speedwire on {GROUP}:{args.port} "
          f"(read timeout {READ_TIMEOUT:.0f}s, Ctrl+C to stop)")

    valid = 0
    try:
        while True:
            try:
                data, sender = sock.recvfrom(65535)
            except socket.timeout:
                print(f"[{time.strftime('%H:%M:%S')}] no packet for "
                      f"{READ_TIMEOUT:.0f}s, still listening...")
                continue
            except OSError as exc:
                print(f"recv error: {exc}", file=sys.stderr)
                continue

            if not data.startswith(MAGIC):
                continue                          # not a Speedwire packet

            telegram = decode(data)
            if telegram is None:
                if len(data) >= 18:
                    proto = struct.unpack_from(">H", data, 16)[0]
                    print(f"[{time.strftime('%H:%M:%S')}] {sender[0]}:{sender[1]}  "
                          f"len={len(data)}  non-emeter Speedwire (proto=0x{proto:04X})")
                continue

            print_telegram(sender, len(data), telegram, args.show_all)
            valid += 1
            if args.count and valid >= args.count:
                break
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
