import unittest
import pandas as pd
from fixtures import sample_row
from projectcs.data import frame
from projectcs.ui import build_map_chart, build_trend_chart, trend_data

class ChartTests(unittest.TestCase):
    def test_same_day_matches_have_distinct_even_positions(self):
        rows = [sample_row(match_id=f"match-{i}", won=i % 2) for i in range(25)]
        games = trend_data(frame(rows))
        self.assertEqual(games["Numero"].tolist(), list(range(1, 26)))
        fig = build_trend_chart(games, start=6, end=25)
        self.assertEqual(list(fig.data[0].x), list(range(1, 26)))
        self.assertEqual(list(fig.layout.xaxis.range), [5.35, 25.65])
        self.assertTrue(fig.layout.xaxis.rangeslider.visible)
        self.assertEqual(len(set(fig.data[0].x)), 25)

    def test_window_does_not_reset_rolling_average(self):
        rows = [sample_row(match_id=f"match-{i:02}", won=1 if i < 10 else 0) for i in range(12)]
        games = trend_data(frame(rows))
        fig = build_trend_chart(games, start=11, end=12)
        self.assertAlmostEqual(fig.data[0].y[10], 90)
        self.assertAlmostEqual(fig.data[0].y[11], 80)

    def test_single_match_and_missing_result(self):
        games = trend_data(frame([sample_row(won=None)]))
        fig = build_trend_chart(games)
        self.assertEqual(len(fig.data[0].x), 1)
        self.assertTrue(pd.isna(fig.data[0].y[0]))
        self.assertFalse(fig.layout.annotations)

    def test_small_map_percentages_use_readable_outside_labels(self):
        maps = pd.DataFrame({"Mappa": ["Ancient", "Anubis"], "Mappe": [10, 5],
                             "Vittorie %": [0, 100], "Round": [200, 100],
                             "Differenza round": [-20, 10]})
        fig = build_map_chart(maps)
        self.assertEqual(list(fig.data[0].textposition), ["inside", "outside"])
        self.assertEqual(fig.data[0].outsidetextfont.color, "#f4f4f4")
        self.assertFalse(fig.data[0].cliponaxis)

if __name__ == "__main__":
    unittest.main()
