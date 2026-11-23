import unittest

from textual.geometry import Region

from omaspot.pics import parts_outside


class PartsOutsideTests(unittest.TestCase):
    def area(self, parts):
        return sum(p.width * p.height for p in parts)

    def test_no_menu_keeps_the_whole_image(self):
        v = Region(2, 3, 10, 5)
        self.assertEqual(parts_outside(v, None), [v])
        self.assertEqual(parts_outside(v, Region(40, 40, 5, 5)), [v])

    def test_menu_in_the_middle_leaves_four_pieces_that_tile_the_rest(self):
        v, m = Region(0, 0, 20, 10), Region(5, 3, 6, 4)
        parts = parts_outside(v, m)
        self.assertEqual(len(parts), 4)
        self.assertEqual(self.area(parts), 20 * 10 - 6 * 4)
        for p in parts:
            self.assertFalse(p.overlaps(m))
            self.assertEqual(p.intersection(v), p)

    def test_menu_over_a_corner_and_over_everything(self):
        v = Region(10, 10, 10, 10)
        parts = parts_outside(v, Region(15, 5, 20, 8))     # covers the top-right corner
        self.assertEqual(self.area(parts), 100 - 5 * 3)
        self.assertEqual(parts_outside(v, Region(0, 0, 50, 50)), [])


if __name__ == "__main__":
    unittest.main()
