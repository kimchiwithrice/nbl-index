"""Reads a data frame saved by R's saveRDS(), which is how nblR publishes its play-by-play.

Only handles what a plain data frame needs (numbers, strings, lists and their attributes), so no
R install or extra package is required.
"""
import gzip
import struct

import numpy as np
import pandas as pd

SYM, LIST, CHAR, LGL, INT, REAL, STR, VEC = 1, 2, 9, 10, 13, 14, 16, 19
ALTREP, NIL, REF = 238, 254, 255
NA_INT = -2147483648


class _Reader:
    def __init__(self, buf):
        self.b, self.i, self.refs = buf, 0, []

    def int(self):
        self.i += 4
        return struct.unpack_from(">i", self.b, self.i - 4)[0]

    def length(self):
        n = self.int()
        return n if n != -1 else (self.int() << 32) + self.int()

    def item(self, want=True):
        """The next object. With want=False it's only skipped over, which is much faster."""
        flags = self.int()
        kind, has_attr, has_tag = flags & 0xFF, flags & 0x200, flags & 0x400
        if kind == NIL or kind >= 240 and kind not in (ALTREP, REF):
            return None
        if kind == REF:
            return self.refs[(flags >> 8 or self.int()) - 1]
        if kind == SYM:
            self.refs.append(self.item())
            return self.refs[-1]
        if kind == CHAR:
            n = self.int()
            if n == -1:
                return None
            self.i += n
            return self.b[self.i - n:self.i].decode("utf-8", "replace")
        if kind == LIST:
            out = []
            while kind == LIST:
                if has_attr:
                    self.item()
                tag = self.item() if has_tag else None
                out.append((tag, self.item(want)))
                flags = self.int()
                kind, has_attr, has_tag = flags & 0xFF, flags & 0x200, flags & 0x400
            return out
        if kind == ALTREP:
            info, state, _ = self.item(), self.item(), self.item()
            cls = info[0][1]
            if cls == "compact_intseq" or cls == "compact_realseq":
                n, start, step = state
                return np.arange(start, start + n * step, step, dtype=float)
            if cls.startswith("wrap_"):
                return state[0][1]
            raise ValueError(f"can't read R ALTREP class {cls}")

        if kind in (LGL, INT, REAL):
            n = self.length()
            size, fmt = (8, ">f8") if kind == REAL else (4, ">i4")
            out = np.frombuffer(self.b, fmt, n, self.i).astype(float) if want else None
            if want and kind != REAL:
                out[out == NA_INT] = np.nan
            self.i += size * n
        elif kind == STR:
            n = self.length()
            out = [None] * n if want else None
            b, i, unpack = self.b, self.i, struct.unpack_from
            for k in range(n):
                ln = unpack(">i", b, i + 4)[0]
                i += 8
                if ln >= 0:
                    if want:
                        out[k] = b[i:i + ln].decode("utf-8", "replace")
                    i += ln
            self.i = i
        elif kind == VEC:
            out = [self.item(want) for _ in range(self.length())]
        else:
            raise ValueError(f"can't read R type {kind}")
        if has_attr:
            self.item()
        return out


def read_frame(path, columns=None):
    """An R data frame as a pandas DataFrame, optionally with only some of its columns."""
    with gzip.open(path) as f:
        r = _Reader(f.read())
    if r.b[:2] != b"X\n":
        raise ValueError("not an R data file")
    r.i = 2
    version = r.int()
    r.i += 8
    if version == 3:
        n = r.int()  # then the name of the native encoding
        r.i += n
    flags = r.int()
    if flags & 0xFF != VEC or not flags & 0x200:
        raise ValueError("not an R data frame")
    # The column names come after the columns, so skip over the columns first and come back.
    starts = []
    for _ in range(r.length()):
        starts.append(r.i)
        r.item(want=False)
    names = dict(r.item())["names"]
    frame = {}
    for name, start in zip(names, starts):
        if columns is None or name in columns:
            r.i = start
            frame[name] = r.item()
    return pd.DataFrame(frame)
