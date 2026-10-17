# -*- coding: utf-8 -*-
"""P15 equivalence test: old O(k^2) chain vs new O(k) indexed chain."""
import math
import random


def build_edges(cells, x0, y0, grid_step):
    cellset = set(cells)
    bbox2d = []
    for (a, b) in cells:
        xa = round(x0 + a * grid_step, 9)
        ya = round(y0 + b * grid_step, 9)
        xb = round(x0 + (a + 1) * grid_step, 9)
        yb = round(y0 + (b + 1) * grid_step, 9)
        if (a, b - 1) not in cellset:
            bbox2d.append(((xa, ya), (xb, ya)))
        if (a, b + 1) not in cellset:
            bbox2d.append(((xa, yb), (xb, yb)))
        if (a - 1, b) not in cellset:
            bbox2d.append(((xa, ya), (xa, yb)))
        if (a + 1, b) not in cellset:
            bbox2d.append(((xb, ya), (xb, yb)))
    return bbox2d


def ring_old(bbox2d):
    ring = [bbox2d[0][0], bbox2d[0][1]]
    used = {0}
    cur = bbox2d[0][1]
    guard = 0
    while len(used) < len(bbox2d) and guard < len(bbox2d) * 4:
        guard += 1
        done = False
        for ei, (pa, pb) in enumerate(bbox2d):
            if ei in used:
                continue
            if math.dist(pb, cur) < 1e-6:
                cur = pa; ring.append(pa); used.add(ei); done = True; break
            elif math.dist(pa, cur) < 1e-6:
                cur = pb; ring.append(pb); used.add(ei); done = True; break
        if not done:
            break
    return ring, used


def ring_new(bbox2d):
    pt2edges = {}
    for ei, (pa, pb) in enumerate(bbox2d):
        pt2edges.setdefault(pa, []).append((ei, 0))
        pt2edges.setdefault(pb, []).append((ei, 1))
    ring = [bbox2d[0][0], bbox2d[0][1]]
    used = {0}
    cur = bbox2d[0][1]
    guard = 0
    while len(used) < len(bbox2d) and guard < len(bbox2d) * 4:
        guard += 1
        nxt = None
        for ei, end in pt2edges.get(cur, ()):
            if ei in used:
                continue
            pa, pb = bbox2d[ei]
            nxt = (pb if end == 0 else pa, ei)
            break
        if nxt is None:
            break
        cur, ei = nxt
        ring.append(cur)
        used.add(ei)
    return ring, used


def random_island(rng, w, h):
    """random connected blob of cells (4-neighbourhood)"""
    cells = set()
    a, b = rng.randrange(w), rng.randrange(h)
    cells.add((a, b))
    target = rng.randrange(4, w * h)
    while len(cells) < target:
        a, b = rng.choice(sorted(cells))
        da, db = rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
        na, nb = a + da, b + db
        if 0 <= na < w and 0 <= nb < h:
            cells.add((na, nb))
    return sorted(cells)


def main():
    rng = random.Random(20261017)
    n_case = 0
    n_diff = 0
    for trial in range(300):
        w = rng.randrange(3, 14)
        h = rng.randrange(3, 14)
        cells = random_island(rng, w, h)
        if len(cells) < 3:
            continue
        x0 = rng.uniform(-800, 800)
        y0 = rng.uniform(-800, 800)
        gs = rng.choice([0.3, 0.5, 1.0, 2.0, 1.7, 0.37])
        edges = build_edges(cells, x0, y0, gs)
        if len(edges) < 3:
            continue
        n_case += 1
        r_old, u_old = ring_old(edges)
        r_new, u_new = ring_new(edges)
        if r_old != r_new or u_old != u_new:
            n_diff += 1
            if n_diff <= 3:
                print('DIFF trial=%d cells=%d edges=%d gs=%s x0=%.6f y0=%.6f'
                      % (trial, len(cells), len(edges), gs, x0, y0))
                print('  old ring len=%d used=%d' % (len(r_old), len(u_old)))
                print('  new ring len=%d used=%d' % (len(r_new), len(u_new)))
    print('cases=%d diffs=%d' % (n_case, n_diff))
    assert n_diff == 0, 'ring chain equivalence FAILED'
    print('P15 ring-chain equivalence: OK')


if __name__ == '__main__':
    main()
