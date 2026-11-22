"""Read the VBA source out of an Office file without Excel. Standard library only, read-only.

An .xlsm is a zip; its xl/vbaProject.bin is an OLE compound file (MS-CFB). Inside, storage "VBA"
holds a stream "dir" (the module list) and one stream per module. Both are compressed with the
run-length scheme of MS-OVBA section 2.4.1; each module's source starts at the offset "dir" gives.

    modules = read_vba("Configuration.xlsm")        -> [(module name, source text)]

A VBA project "locked for viewing" is still readable this way (the lock is a flag for the editor).
A workbook encrypted with a password to open is not: the whole file is then encrypted.
"""
import io
import struct
import zipfile

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
END, FREE = 0xFFFFFFFE, 0xFFFFFFFF


class OVBAError(Exception):
    pass


def decompress(data):
    """MS-OVBA 2.4.1.3.1 Decompression algorithm."""
    if not data or data[0] != 1:
        raise OVBAError("not a compressed VBA container (first byte is not 0x01)")
    out, pos = bytearray(), 1
    while pos + 2 <= len(data):
        header, = struct.unpack_from("<H", data, pos)
        pos += 2
        if (header >> 12) & 7 != 3:
            raise OVBAError("bad chunk signature at byte %d" % (pos - 2))
        end, start = min(len(data), pos + (header & 0x0FFF) + 1), len(out)
        if not header & 0x8000:                               # raw chunk: 4096 bytes as they are
            out += data[pos:pos + 4096]
            pos += 4096
            continue
        while pos < end:
            flags = data[pos]
            pos += 1
            for bit in range(8):
                if pos >= end:
                    break
                if not (flags >> bit) & 1:
                    out.append(data[pos])
                    pos += 1
                    continue
                token, = struct.unpack_from("<H", data, pos)
                pos += 2
                bits = 4
                while (1 << bits) < len(out) - start:
                    bits += 1
                bits = min(bits, 12)
                length = (token & (0xFFFF >> bits)) + 3
                offset = (token >> (16 - bits)) + 1
                if offset > len(out):
                    raise OVBAError("copy token points before the start of the data")
                for _ in range(length):
                    out.append(out[-offset])
    return bytes(out)


class CompoundFile:
    """Minimal MS-CFB reader: lists and reads streams by path ('VBA/dir')."""

    def __init__(self, data):
        if data[:8] != MAGIC:
            raise OVBAError("not an OLE compound file")
        self.data = data
        self.sector = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini = 1 << struct.unpack_from("<H", data, 0x20)[0]
        (n_fat, dir_start, _, self.cutoff, minifat_start, n_minifat, difat_start, n_difat) = struct.unpack_from("<IIIIIIII", data, 0x2C)
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        per = self.sector // 4
        seen = 0
        while difat_start not in (END, FREE) and seen < n_difat + 1:
            block = struct.unpack_from("<%dI" % per, data, self._off(difat_start))
            difat += block[:-1]
            difat_start = block[-1]
            seen += 1
        self.fat = []
        for s in difat:
            if s not in (END, FREE):
                self.fat += struct.unpack_from("<%dI" % per, data, self._off(s))
        self.entries = []
        raw = self._chain(dir_start)
        for i in range(0, len(raw) - 127, 128):
            name_len, kind = struct.unpack_from("<HB", raw, i + 0x40)
            left, right, child = struct.unpack_from("<III", raw, i + 0x44)
            start, size = struct.unpack_from("<IQ", raw, i + 0x74)
            name = raw[i:i + max(name_len - 2, 0)].decode("utf-16-le", "replace")
            self.entries.append((name, kind, left, right, child, start, size if self.sector > 512 else size & 0xFFFFFFFF))
        if not self.entries:
            raise OVBAError("the compound file has no directory")
        root = self.entries[0]
        self.ministream = self._chain(root[5])[:root[6]] if root[5] not in (END, FREE) else b""
        mf = self._chain(minifat_start) if minifat_start not in (END, FREE) else b""
        self.minifat = list(struct.unpack_from("<%dI" % (len(mf) // 4), mf)) if mf else []
        self.streams = {}
        self._tree(root[4], "")

    def _off(self, sector):
        return (sector + 1) * self.sector

    def _chain(self, start):
        out, s, hops = bytearray(), start, 0
        while s not in (END, FREE):
            if s >= len(self.fat) or hops > len(self.fat):
                raise OVBAError("broken sector chain")
            out += self.data[self._off(s):self._off(s) + self.sector]
            s = self.fat[s]
            hops += 1
        return bytes(out)

    def _tree(self, index, path):
        todo = [(index, path)]
        while todo:
            i, base = todo.pop()
            if i in (END, FREE) or i >= len(self.entries):
                continue
            name, kind, left, right, child, start, size = self.entries[i]
            todo += [(left, base), (right, base)]
            if kind == 1:
                todo.append((child, base + name + "/"))
            elif kind == 2:
                self.streams[base + name] = (start, size)

    def read(self, path):
        key = next((k for k in self.streams if k.lower() == path.lower()), None)
        if key is None:
            raise OVBAError("stream %s not found" % path)
        start, size = self.streams[key]
        if size >= self.cutoff:
            return self._chain(start)[:size]
        out, s, hops = bytearray(), start, 0
        while s not in (END, FREE) and hops <= len(self.minifat):
            out += self.ministream[s * self.mini:(s + 1) * self.mini]
            s = self.minifat[s]
            hops += 1
        return bytes(out[:size])


def modules_from_bin(data):
    """vbaProject.bin bytes -> [(module name, source text)] in project order."""
    cf = CompoundFile(data)
    dirs = [k for k in cf.streams if k.lower().endswith("vba/dir")]
    if not dirs:
        raise OVBAError("no VBA/dir stream: this file holds no VBA project")
    base = dirs[0][:-3]
    info = decompress(cf.read(dirs[0]))
    pos, codec, mods, cur = 0, "cp1252", [], None
    while pos + 6 <= len(info):
        rid, size = struct.unpack_from("<HI", info, pos)
        pos += 6
        if rid == 0x0009:                                     # PROJECTVERSION: its size field is not its length
            pos += 6
            continue
        body = info[pos:pos + size]
        pos += size
        if rid == 0x0003 and size >= 2:
            page = struct.unpack_from("<H", body)[0]
            codec = {65001: "utf-8", 10000: "mac-roman"}.get(page, "cp%d" % page)
        elif rid == 0x0019:
            cur = {"name": body.decode(codec, "replace"), "stream": None, "offset": 0}
            mods.append(cur)
        elif rid == 0x0047 and cur is not None and body:
            cur["name"] = body.decode("utf-16-le", "replace")
        elif rid == 0x001A and cur is not None:
            cur["stream"] = body.decode(codec, "replace")
        elif rid == 0x0032 and cur is not None and body:
            cur["stream"] = body.decode("utf-16-le", "replace")
        elif rid == 0x0031 and cur is not None:
            cur["offset"] = struct.unpack_from("<I", body)[0]
    out = []
    for m in mods:
        raw = cf.read(base + (m["stream"] or m["name"]))
        try:
            text = decompress(raw[m["offset"]:]).decode(codec, "replace")
        except LookupError:
            text = decompress(raw[m["offset"]:]).decode("cp1252", "replace")
        out.append((m["name"], text))
    return out


def read_vba(path):
    """Any of: .xlsm/.xlam/.docm/.pptm (zip), .xls/.doc (OLE), vbaProject.bin -> [(module, source)]."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] == MAGIC:
        return modules_from_bin(data)
    if data[:2] != b"PK":
        raise OVBAError("%s is neither an Office zip package nor an OLE file" % path)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.lower().endswith("vbaproject.bin")]
        if not names:
            raise OVBAError("%s contains no vbaProject.bin (no macros)" % path)
        return modules_from_bin(z.read(names[0]))
