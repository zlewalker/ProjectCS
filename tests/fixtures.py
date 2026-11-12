from projectcs.config import NAMES

def sample_row(**changes):
    row = dict(
        match_id='test-match', map_no=0, team_id='test-team',
        player_id='test-player', nickname='TestPlayer', date='2026-10-20T20:00:00+00:00',
        map='ancient', roster_count=1, won=1, rounds=20,
        kills=16, deaths=12, assists=4, adr=80, headshots=8,
        score_for=13, score_against=7, source='faceit', coverage='Statistiche match')
    row.update(changes)
    return row

def app_rows():
    rows = [sample_row(player_id='test-' + name, nickname=name, roster_count=5) for name in NAMES]
    rows.append(sample_row(match_id='test-solo', nickname='Senshe', player_id='test-Senshe', date='2026-10-21T20:00:00+00:00'))
    return rows
