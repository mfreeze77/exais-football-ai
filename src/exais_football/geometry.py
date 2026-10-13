"""Small dependency-free projective geometry with normalized coordinates and failure gates."""

from __future__ import annotations

import math

from .service import CatalogError


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def inverse(m):
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    out = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ]
    determinant = a * out[0][0] + b * out[1][0] + c * out[2][0]
    if not math.isfinite(determinant) or abs(determinant) < 1e-12:
        raise CatalogError(
            "calibration_degenerate", "The selected points do not define a stable projection", 422
        )
    return [[v / determinant for v in row] for row in out]


def project(matrix, x, y):
    den = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if not math.isfinite(den) or abs(den) < 1e-10:
        raise CatalogError("projection_singular", "This point lies at the projection horizon", 422)
    value = [(matrix[r][0] * x + matrix[r][1] * y + matrix[r][2]) / den for r in range(2)]
    if not all(math.isfinite(v) for v in value):
        raise CatalogError("projection_nonfinite", "Projection is not finite", 422)
    return value


def hull_area(points):
    points = sorted(set(tuple(p) for p in points))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    if len(points) < 3:
        return 0.0
    lower = []
    upper = []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return (
        abs(
            sum(
                hull[i][0] * hull[(i + 1) % len(hull)][1]
                - hull[(i + 1) % len(hull)][0] * hull[i][1]
                for i in range(len(hull))
            )
        )
        / 2
    )


def _normalize(points):
    x = sum(p[0] for p in points) / len(points)
    y = sum(p[1] for p in points) / len(points)
    distance = sum(math.hypot(p[0] - x, p[1] - y) for p in points) / len(points)
    if distance < 1e-9:
        raise CatalogError("calibration_degenerate", "Choose distinct, distributed points", 422)
    scale = math.sqrt(2) / distance
    transform = [[scale, 0, -scale * x], [0, scale, -scale * y], [0, 0, 1]]
    return [project(transform, *p) for p in points], transform


def homography(source, target):
    if not 4 <= len(source) == len(target) <= 64:
        raise CatalogError("calibration_points", "Use 4–64 paired points", 422)
    if len(set(map(tuple, source))) != len(source) or len(set(map(tuple, target))) != len(target):
        raise CatalogError(
            "calibration_duplicate", "Calibration point pairs must be unique on both sides", 422
        )
    if hull_area(source) < 1e-6 or hull_area(target) < 1e-6:
        raise CatalogError(
            "calibration_degenerate", "Collinear points cannot define a field plane", 422
        )
    source_n, sn = _normalize(source)
    target_n, tn = _normalize(target)
    rows = []
    rhs = []
    for (x, y), (u, v) in zip(source_n, target_n, strict=True):
        rows.extend(([x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]))
        rhs.extend((u, v))
    # Normal equations on normalized inputs, solved with full row pivoting.
    a = [
        [sum(r[i] * r[j] for r in rows) for j in range(8)]
        + [sum(r[i] * v for r, v in zip(rows, rhs, strict=True))]
        for i in range(8)
    ]
    pivots = []
    for col in range(8):
        pivot = max(range(col, 8), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        divisor = a[col][col]
        pivots.append(abs(divisor))
        if abs(divisor) < 1e-10:
            raise CatalogError(
                "calibration_degenerate", "Points produce an unstable calibration", 422
            )
        a[col] = [v / divisor for v in a[col]]
        for r in range(8):
            if r != col:
                factor = a[r][col]
                a[r] = [v - factor * w for v, w in zip(a[r], a[col], strict=True)]
    if max(pivots) / min(pivots) > 1e9:
        raise CatalogError("calibration_unstable", "Select points spanning more of the field", 422)
    h = [a[i][-1] for i in range(8)] + [1]
    matrix = matmul(matmul(inverse(tn), [h[:3], h[3:6], h[6:]]), sn)
    scale = matrix[2][2]
    if abs(scale) < 1e-12:
        raise CatalogError("calibration_unstable", "The calibration has a singular scale", 422)
    matrix = [[v / scale for v in row] for row in matrix]
    back = inverse(matrix)
    errors = [math.dist(project(back, *dst), src) for src, dst in zip(source, target, strict=True)]
    return matrix, math.sqrt(sum(e * e for e in errors) / len(errors))
