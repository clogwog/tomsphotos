import bisect

from gallerypanel import (
    DEFAULT_RATIO,
    compute_justified_layout,
    hit_test_position,
)


def test_single_item_keeps_target_height():
    positions, total = compute_justified_layout(["a"], {}, 800)
    assert len(positions) == 1
    path, x, y, w, h = positions[0]
    assert h == 200  # target
    assert w == 200  # default ratio 1.0
    assert total == 200 + 4


def test_full_row_justifies_to_width():
    items = ["a", "b", "c", "d"]
    positions, _ = compute_justified_layout(items, {}, 800)
    # all square, 4 per row at target 200 -> natural 816 > 800, so justify
    first_row = [p for p in positions if p[2] == 0]
    assert len(first_row) == 4
    row_right_edge = max(p[1] + p[3] for p in first_row)
    assert abs(row_right_edge - 800) < 1.0


def test_last_partial_row_not_stretched():
    items = ["a", "b", "c", "d", "e"]  # 5 items, 4 per row -> last row 1 item
    positions, total = compute_justified_layout(items, {}, 800)
    last = positions[-1]
    assert last[3] == 200  # not stretched
    assert last[4] == 200


def test_ratios_change_widths():
    ratios = {"a": 2.0, "b": 0.5}
    positions, _ = compute_justified_layout(["a", "b"], ratios, 800)
    pa = positions[0]
    pb = positions[1]
    # same height, width proportional to ratio
    assert pa[4] == pb[4]
    assert abs((pa[3] / pb[3]) - (2.0 / 0.5)) < 0.01


def test_known_ratio_overrides_default():
    positions, _ = compute_justified_layout(["a"], {"a": 1.5}, 800)
    assert positions[0][3] / positions[0][4] == 1.5


def test_row_height_clamped():
    # extreme wide ratios force tiny height -> clamped to min
    ratios = {f"w{i}": 0.05 for i in range(200)}
    positions, _ = compute_justified_layout(list(ratios), ratios, 800)
    for p in positions:
        assert p[4] >= 100  # MIN_ROW_HEIGHT


def test_15k_items_layout_performance():
    import time
    items = [f"p{i}.jpg" for i in range(15000)]
    ratios = {p: (1.0 + (i % 7) * 0.1) for i, p in enumerate(items)}
    t0 = time.perf_counter()
    positions, total = compute_justified_layout(items, ratios, 1200)
    dt = time.perf_counter() - t0
    assert len(positions) == 15000
    assert total > 0
    # 15K items layout must be fast (pure math, no per-item work)
    assert dt < 1.0, f"layout took {dt:.3f}s"


def test_y_starts_are_sorted_for_bisect():
    positions, _ = compute_justified_layout([f"p{i}" for i in range(500)], {}, 800)
    y_starts = [p[2] for p in positions]
    assert y_starts == sorted(y_starts)
    # hit-testing works for every item's center
    for path, x, y, w, h in positions[::50]:
        hit = hit_test_position(positions, y_starts, x + w / 2, y + h / 2)
        assert hit == path


def test_hit_test_returns_none_in_gap():
    positions, _ = compute_justified_layout([f"p{i}" for i in range(10)], {}, 800)
    y_starts = [p[2] for p in positions]
    # between rows (y in the 4px gap)
    assert hit_test_position(positions, y_starts, 100, 198.5) is None
    # outside right edge of last row
    last = positions[-1]
    assert hit_test_position(positions, y_starts, last[1] + last[3] + 2, last[2] + 1) is None


def test_default_ratio_is_square():
    assert DEFAULT_RATIO == 1.0