"""Test helper: writes a minimal vbaProject.bin (MS-CFB + MS-OVBA) so the reader can be tested
without Excel. The compressor follows MS-OVBA 2.4.1.3.6; the test checks it against the
specification's own example."""
import struct
import zipfile

END, FREE = 0xFFFFFFFE, 0xFFFFFFFF


def compress(data):
    out = bytearray([1])
    for base in range(0, max(len(data), 1), 4096):
        chunk, body, pos = data[base:base + 4096], bytearray(), 0
        while pos < len(chunk):
            flag_at, flags = len(body), 0
            body.append(0)
            for bit in range(8):
                if pos >= len(chunk):
                    break
                bits = 4
                while (1 << bits) < pos:
                    bits += 1
                bits = min(bits, 12)
                max_len, best, off = (0xFFFF >> bits) + 3, 0, 0
                for cand in range(pos - 1, max(pos - (1 << bits), 0) - 1, -1):
                    n = 0
                    while n < max_len and pos + n < len(chunk) and chunk[cand + n] == chunk[pos + n]:
                        n += 1
                    if n > best:
                        best, off = n, pos - cand
                if best >= 3:
                    body += struct.pack("<H", ((off - 1) << (16 - bits)) | (best - 3))
                    flags |= 1 << bit
                    pos += best
                else:
                    body.append(chunk[pos])
                    pos += 1
            body[flag_at] = flags
        out += struct.pack("<H", 0xB000 | (len(body) - 1)) + body
    return bytes(out)


def _rec(rid, body=b""):
    return struct.pack("<HI", rid, len(body)) + body


def dir_stream(modules, offsets):
    d = _rec(1, struct.pack("<I", 3)) + _rec(2, struct.pack("<I", 0x409)) + _rec(0x14, struct.pack("<I", 0x409))
    d += _rec(3, struct.pack("<H", 1252)) + _rec(4, b"VBAProject") + _rec(5) + _rec(0x40) + _rec(6) + _rec(0x3D)
    d += _rec(7, struct.pack("<I", 0)) + _rec(8, struct.pack("<I", 0))
    d += struct.pack("<HI", 9, 4) + struct.pack("<IH", 1, 1)            # PROJECTVERSION: 6 bytes follow a size of 4
    d += _rec(0x0C) + _rec(0x3C) + _rec(0x0F, struct.pack("<H", len(modules))) + _rec(0x13, struct.pack("<H", 0xFFFF))
    for (name, _), off in zip(modules, offsets):
        d += _rec(0x19, name.encode("cp1252")) + _rec(0x47, name.encode("utf-16-le"))
        d += _rec(0x1A, name.encode("cp1252")) + _rec(0x32, name.encode("utf-16-le")) + _rec(0x1C) + _rec(0x48)
        d += _rec(0x31, struct.pack("<I", off)) + _rec(0x1E, struct.pack("<I", 0)) + _rec(0x2C, struct.pack("<H", 0xFFFF))
        d += _rec(0x21) + _rec(0x2B)
    return compress(d + _rec(0x10))


def compound_file(streams):
    """streams: {"PROJECT": bytes, "VBA/dir": bytes, ...} (one level of storages) -> file bytes"""
    entries = [["Root Entry", 5, FREE, FREE, FREE, END, 0]]         # name, type, left, right, child, start, size
    storages, mini, big = {}, bytearray(), []
    minifat = []

    def add(name, kind, parent):
        entries.append([name, kind, FREE, FREE, FREE, END, 0])
        i = len(entries) - 1
        if entries[parent][4] == FREE:
            entries[parent][4] = i
        else:
            j = entries[parent][4]
            while entries[j][3] != FREE:
                j = entries[j][3]
            entries[j][3] = i                                       # siblings chained to the right
        return i

    for path, data in streams.items():
        parent = 0
        if "/" in path:
            folder, path = path.split("/")
            if folder not in storages:
                storages[folder] = add(folder, 1, 0)
            parent = storages[folder]
        i = add(path, 2, parent)
        entries[i][6] = len(data)
        if len(data) < 4096:
            entries[i][5] = len(mini) // 64
            n = max(-(-len(data) // 64), 1)
            minifat += [len(minifat) + k + 1 for k in range(n - 1)] + [END]
            mini += data.ljust(n * 64, b"\0")
        else:
            big.append((i, data))
    n_dir = -(-len(entries) // 4)
    n_minifat = max(-(-len(minifat) // 128), 1)
    n_mini = max(-(-len(mini) // 512), 1)
    n_big = sum(-(-len(d) // 512) for _, d in big)
    n_fat = 1
    while n_fat * 128 < n_fat + n_dir + n_minifat + n_mini + n_big:
        n_fat += 1
    fat, sectors = [0xFFFFFFFD] * n_fat, []

    def chain(n):
        first = len(fat)
        fat.extend([first + k + 1 for k in range(n - 1)] + [END])
        return first

    dir_start, minifat_start, mini_start = chain(n_dir), chain(n_minifat), chain(n_mini)
    entries[0][5], entries[0][6] = mini_start, len(mini)
    blobs = []
    for i, data in big:
        entries[i][5] = chain(-(-len(data) // 512))
        blobs.append(data.ljust(-(-len(data) // 512) * 512, b"\0"))
    fat += [FREE] * (n_fat * 128 - len(fat))
    out = bytearray(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6)
    out += struct.pack("<IIIIIIIII", 0, n_fat, dir_start, 0, 4096, minifat_start, n_minifat, END, 0)
    out += struct.pack("<109I", *(list(range(n_fat)) + [FREE] * (109 - n_fat)))
    out += struct.pack("<%dI" % len(fat), *fat)
    for name, kind, left, right, child, start, size in entries:
        raw = name.encode("utf-16-le")
        out += raw.ljust(64, b"\0") + struct.pack("<HBBIII", len(raw) + 2, kind, 1, left, right, child) + b"\0" * 36
        out += struct.pack("<IQ", start, size)
    out += b"\0" * ((n_dir * 4 - len(entries)) * 128)
    out += struct.pack("<%dI" % (n_minifat * 128), *(minifat + [FREE] * (n_minifat * 128 - len(minifat))))
    out += bytes(mini).ljust(n_mini * 512, b"\0")
    for b in blobs:
        out += b
    return bytes(out)


def vba_project_bin(modules, junk=(37, 5000, 0)):
    """modules: [(name, source text)] -> vbaProject.bin bytes. `junk` bytes precede each module's source,
    as Excel's compiled cache does."""
    offsets = [junk[i % len(junk)] for i in range(len(modules))]
    streams = {"PROJECT": b'ID="{00000000-0000-0000-0000-000000000000}"\r\n', "VBA/dir": dir_stream(modules, offsets)}
    for (name, text), off in zip(modules, offsets):
        streams["VBA/" + name] = bytes(range(256)) * (off // 256) + b"\x55" * (off % 256) + compress(text.encode("cp1252"))
    return compound_file(streams)


def write_xlsm(path, modules):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/vbaProject.bin", vba_project_bin(modules))
