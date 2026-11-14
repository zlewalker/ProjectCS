from projectcs.config import NAMES


def sample_row(**changes):
    row = dict(
        match_id='test-match', map_no=0, team_id='test-team',
        player_id='test-player', nickname='TestPlayer', date='2026-10-20T20:00:00+00:00',
        map='ancient', roster_count=1, won=1, rounds=20,
        kills=16, deaths=12, assists=4, adr=80, headshots=8,
        damage=1600, first_kills=2, entry_count=4, entry_wins=2,
        sniper_kills=4, flash_count=6, flash_successes=3, enemies_flashed=4,
        utility_count=5, utility_damage=120, utility_enemies=3,
        clutch_kills=2, clutch_1v1_count=2, clutch_1v1_wins=1,
        clutch_1v2_count=1, clutch_1v2_wins=0, pistol_kills=2, mvps=2,
        double_kills=2, triple_kills=1, quadro_kills=0, penta_kills=0,
        traded_deaths=None, untraded_deaths=None,
        first_half_score=8, second_half_score=5, overtime_score=0,
        score_for=13, score_against=7, source='faceit', coverage='Statistiche match')
    row.update(changes)
    return row


def app_rows():
    rows = [sample_row(player_id='test-' + name, nickname=name, roster_count=5) for name in NAMES]
    rows.append(sample_row(match_id='test-solo', nickname='Senshe', player_id='test-Senshe', date='2026-10-21T20:00:00+00:00'))
    return rows
