import unittest
import pandas as pd
from fixtures import sample_row
from projectcs.data import frame
from projectcs.ui import (
    RADAR_CHART_CONFIG,
    build_map_chart,
    build_radar_heatmap_chart,
    build_trend_chart,
    trend_data,
)

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

    def test_radar_heatmap_has_map_image_and_pan_zoom(self):
        self.assertTrue(RADAR_CHART_CONFIG["scrollZoom"])
        df = pd.DataFrame([
            {
                "round_no": 1,
                "victim_name": "LEWALKER",
                "attacker_name": "Enemy",
                "weapon": "ak47",
                "is_traded": 0,
                "is_opening": 1,
                "victim_rx": 480.0,
                "victim_ry": 320.0,
                "attacker_rx": 540.0,
                "attacker_ry": 260.0,
            },
            {
                "round_no": 2,
                "victim_name": "Grvpe",
                "attacker_name": "Enemy",
                "weapon": "awp",
                "is_traded": 1,
                "is_opening": 0,
                "victim_rx": 510.0,
                "victim_ry": 350.0,
                "attacker_rx": 600.0,
                "attacker_ry": 210.0,
            },
        ])
        fig = build_radar_heatmap_chart(df, "dust2", mode="victim", show_vectors=True)
        self.assertEqual(fig.layout.dragmode, "pan")
        self.assertEqual(len(fig.layout.images), 1)
        self.assertEqual(list(fig.layout.xaxis.range), [0, 1024])
        self.assertEqual(list(fig.layout.yaxis.range), [1024, 0])

if __name__ == "__main__":
    unittest.main()
