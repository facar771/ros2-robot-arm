"""Parça testleri için küçük mesh üreticileri (S2). Küp: 8 köşe, 12 üçgen, kenar ``size`` (ham birim)."""
import struct

CUBE_CORNERS = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
# dışa bakan
CUBE_QUADS = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def cube_triangles(size=1.0, origin=(0.0, 0.0, 0.0)):
    pts = [tuple(origin[k] + size * c[k] for k in range(3)) for c in CUBE_CORNERS]
    tris = []
    for a, b, c, d in CUBE_QUADS:
        tris.append((pts[a], pts[b], pts[c]))
        tris.append((pts[a], pts[c], pts[d]))
    return tris


def binary_stl(tris, header=b'test'):
    out = header.ljust(80, b'\0') + struct.pack('<I', len(tris))
    for t in tris:
        # normal 0: sunucu yeniden hesaplar
        out += struct.pack('<12fH', 0, 0, 0, *[v for p in t for v in p], 0)
    return out


def ascii_stl(tris):
    lines = ['solid test']
    for t in tris:
        lines.append('facet normal 0 0 0\n outer loop')
        lines += [f'  vertex {p[0]} {p[1]} {p[2]}' for p in t]
        lines.append(' endloop\nendfacet')
    lines.append('endsolid test')
    return '\n'.join(lines).encode()


def obj_cube(size=1.0, negative=False, slashes=False):
    lines = ['# küp', 'o cube']
    lines += [f'v {size * c[0]} {size * c[1]} {size * c[2]}' for c in CUBE_CORNERS]
    lines.append('vn 0 0 1')
    for quad in CUBE_QUADS:
        idx = [(i - 8) if negative else i + 1 for i in quad]
        toks = [f'{i}//1' if slashes else str(i) for i in idx]
        lines.append('f ' + ' '.join(toks))
    return '\n'.join(lines).encode()
