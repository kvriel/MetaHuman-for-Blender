"""Rename objects inside a binary FBX file (versions 7.x), without Blender.

Blender limits names to 63 bytes, but Unreal's RigLogic finds a DNA blend shape's morph target by
the name "<mesh>__<channel>", which is often longer. The exporter writes short placeholder shape
key names and this module puts the full names into the file afterwards.

Names in binary FBX are string properties of the form b"name\\x00\\x01Class". Every other byte
(arrays, compressed or not) is copied as it is, so a rewrite with no renames gives back the same file.
"""
from __future__ import annotations

import struct
from pathlib import Path

__all__ = ["rename_objects", "object_names"]

_MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
_SEP = b"\x00\x01"
_FIXED = {ord("Y"): 2, ord("C"): 1, ord("I"): 4, ord("F"): 4, ord("D"): 8, ord("L"): 8}
_ARRAYS = frozenset(b"fdlib")
_STRINGS = frozenset(b"SR")
_FOOTER_HEAD = 20      # footer id + 4 zero bytes, then padding to a 16-byte boundary
_FOOTER_TAIL = 140     # version, 120 zero bytes, magic


class _Node:
    __slots__ = ("name", "props", "children", "nested")

    def __init__(self, name, props, children, nested):
        self.name = name            # bytes
        self.props = props          # [(type code, raw bytes after the code)]
        self.children = children    # [_Node]
        self.nested = nested        # True when a null record closes the node (children or not)


class _Format:
    def __init__(self, version: int):
        self.meta = struct.Struct("<3Q" if version >= 7500 else "<3I")
        self.null = b"\0" * (self.meta.size + 1)


def _parse_props(buf, pos, count):
    props = []
    for _ in range(count):
        code = buf[pos]
        pos += 1
        if code in _FIXED:
            size = _FIXED[code]
        elif code in _ARRAYS:
            size = 12 + struct.unpack_from("<I", buf, pos + 8)[0]
        elif code in _STRINGS:
            size = 4 + struct.unpack_from("<I", buf, pos)[0]
        else:
            raise ValueError(f"unknown FBX property type {chr(code)!r} at byte {pos - 1}")
        props.append((code, buf[pos:pos + size]))
        pos += size
    return props, pos


def _parse_node(buf, pos, fmt):
    end, count, length = fmt.meta.unpack_from(buf, pos)
    pos += fmt.meta.size
    if end == 0:
        return None, pos + 1                                   # null record (one more zero byte: name length)
    n = buf[pos]
    name = bytes(buf[pos + 1:pos + 1 + n])
    pos += 1 + n
    start = pos
    props, pos = _parse_props(buf, pos, count)
    if pos != start + length:
        raise ValueError(f"FBX node {name!r}: property length mismatch")
    children, nested = [], pos < end
    while pos < end:
        child, pos = _parse_node(buf, pos, fmt)
        if child is None:
            break
        children.append(child)
    if pos != end:
        raise ValueError(f"FBX node {name!r}: end offset mismatch")
    return _Node(name, props, children, nested), pos


def _size(node, fmt):
    s = fmt.meta.size + 1 + len(node.name) + sum(1 + len(raw) for _, raw in node.props)
    s += sum(_size(c, fmt) for c in node.children)
    return s + (len(fmt.null) if node.nested else 0)


def _write(node, out, pos, fmt):
    end = pos + _size(node, fmt)
    plen = sum(1 + len(raw) for _, raw in node.props)
    out += fmt.meta.pack(end, len(node.props), plen)
    out += bytes((len(node.name),)) + node.name
    for code, raw in node.props:
        out.append(code)
        out += raw
    pos += fmt.meta.size + 1 + len(node.name) + plen
    for c in node.children:
        pos = _write(c, out, pos, fmt)
    if node.nested:
        out += fmt.null
        pos += len(fmt.null)
    assert pos == end
    return pos


def _read(path):
    buf = memoryview(Path(path).read_bytes())
    if bytes(buf[:len(_MAGIC)]) != _MAGIC:
        raise ValueError(f"{path} is not a binary FBX file")
    version = struct.unpack_from("<I", buf, len(_MAGIC))[0]
    fmt = _Format(version)
    pos = len(_MAGIC) + 4
    top = []
    while True:
        node, pos = _parse_node(buf, pos, fmt)
        if node is None:
            break
        top.append(node)
    return buf, pos, fmt, top


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def object_names(path: str | Path) -> list[tuple[str, str]]:
    """(name, class) of every named object in the file, e.g. ("jaw_open", "SubDeformer")."""
    _, _, _, top = _read(path)
    out = []
    for node in _walk(top):
        for code, raw in node.props:
            value = bytes(raw[4:]) if code == ord("S") else b""
            if _SEP in value:
                name, _, cls = value.partition(_SEP)
                out.append((name.decode("utf-8", "replace"), cls.decode("utf-8", "replace")))
    return out


def rename_objects(path: str | Path, mapping: dict[str, str], out_path: str | Path | None = None) -> int:
    """Give objects named like a key of `mapping` the mapped name. Returns how many names changed."""
    buf, pos, fmt, top = _read(path)
    enc = {k.encode("utf-8"): v.encode("utf-8") for k, v in mapping.items()}
    changed = 0
    for node in _walk(top):
        for i, (code, raw) in enumerate(node.props):
            value = bytes(raw[4:]) if code == ord("S") else b""
            if _SEP not in value:
                continue
            name, sep, cls = value.partition(_SEP)
            if name in enc:
                value = enc[name] + sep + cls
                node.props[i] = (code, struct.pack("<I", len(value)) + value)
                changed += 1
    footer = bytes(buf[pos:])
    out = bytearray(buf[:len(_MAGIC) + 4])
    at = len(out)
    for node in top:
        at = _write(node, out, at, fmt)
    out += fmt.null
    at += len(fmt.null)
    if len(footer) > _FOOTER_HEAD + _FOOTER_TAIL and len(footer) <= _FOOTER_HEAD + _FOOTER_TAIL + 16:
        out += footer[:_FOOTER_HEAD]                             # padding keeps the tail 16-byte aligned
        pad = (-(len(out))) % 16 or 16
        out += b"\0" * pad + footer[-_FOOTER_TAIL:]
    else:
        out += footer
    Path(out_path or path).write_bytes(bytes(out))
    return changed
