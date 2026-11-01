
#!/usr/bin/env python3
import csv
import json
import re
import unicodedata
import urllib.request
import urllib.error

from datetime import datetime, date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data"
INDEX_URL = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/LEAGUE_INDEX.md"

SEASON = "2627"
OUTPUT = Path("docs/kgvar.json")
TZ = ZoneInfo("Europe/Istanbul")

# Aday kriterleri
FORM_MATCHES = 10
MIN_OVERALL_MATCHES = 8
MIN_VENUE_MATCHES = 3
MIN_TEAM_BTTS_RATE = 0.60
MIN_TEAM_SCORE_RATE = 0.70
MIN_TEAM_CONCEDE_RATE = 0.70
MIN_CANDIDATE_SCORE = 75
MAX_CANDIDATES = 3
LOOKAHEAD_DAYS = 2


def get_text(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "StudioErs-KGVar/3.0"}
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read().decode("utf-8-sig", errors="replace")


def value(row, *names):
    data = {
        str(k).strip().lower(): (v or "").strip()
        for k, v in row.items() if k
    }
    for name in names:
        result = data.get(name.lower(), "")
        if result:
            return result
    return ""


def get_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None

    try:
        return datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        ).date()
    except ValueError:
        pass

    for fmt in (
        "%d/%m/%Y", "%m/%d/%Y",
        "%Y/%m/%d", "%d-%m-%Y"
    ):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            pass

    return None


def get_time(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""

    # HH:MM veya HH:MM:SS biçimleri
    match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)", raw)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"

    return ""


def get_score(raw):
    try:
        raw = str(raw).strip()
        if not raw:
            return None
        number = int(float(raw))
        return number if number >= 0 else None
    except (ValueError, TypeError):
        return None


def normalize_team(name):
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(
        c for c in s if not unicodedata.combining(c)
    ).casefold()

    s = s.replace("&", " and ")
    s = re.sub(
        r"\b(fc|cf|afc|sc|ac|club|football|soccer)\b",
        " ",
        s
    )
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def parse_row(row, league, filename):
    match_date = get_date(
        value(row, "date", "match_date", "fixture_date", "datetime")
    )

    home = value(row, "home", "home_team", "hometeam", "team_home")
    away = value(row, "away", "away_team", "awayteam", "team_away")

    if not match_date or not home or not away:
        return None

    home_score = get_score(value(
        row, "ft_home", "home_score", "homescore",
        "home_goals", "fthg"
    ))

    away_score = get_score(value(
        row, "ft_away", "away_score", "awayscore",
        "away_goals", "ftag"
    ))

    if home_score is None or away_score is None:
        home_score = away_score = None

    match_time = get_time(value(
        row, "time", "kickoff", "start_time",
        "match_time", "kick_off"
    ))

    return {
        "date": match_date.isoformat(),
        "time": match_time,
        "home": home,
        "away": away,
        "home_key": normalize_team(home),
        "away_key": normalize_team(away),
        "home_score": home_score,
        "away_score": away_score,
        "league": league,
        "source_file": filename
    }


def discover_competitions():
    text = get_text(INDEX_URL)

    folders = re.findall(
        r"`([A-Z0-9]+_[A-Z0-9]+_[a-z0-9-]+)`",
        text
    )

    if not folders:
        raise RuntimeError(
            "LEAGUE_INDEX.md içinden lig klasörleri bulunamadı."
        )

    return sorted(set(folders))


def load_matches():
    matches = []
    errors = []
    folders = discover_competitions()

    for folder in folders:
        league = folder.rsplit("/", 1)[-1]

        for filename in (
            f"results_{SEASON}.csv",
            f"fixtures_{SEASON}.csv"
        ):
            try:
                url = f"{BASE}/{folder}/{filename}"
                content = get_text(url)

                reader = csv.DictReader(content.splitlines())

                for row in reader:
                    match = parse_row(row, league, filename)
                    if match:
                        matches.append(match)

            except (
                urllib.error.URLError,
                TimeoutError,
                ValueError,
                csv.Error
            ) as exc:
                errors.append(
                    f"{league}/{filename}: "
                    f"{type(exc).__name__}: {exc}"
                )

    # Aynı maçın sonuç ve fikstür dosyalarındaki tekrarlarını birleştir.
    unique = {}

    for match in matches:
        key = (
            match["date"],
            match["home_key"],
            match["away_key"],
            match["league"]
        )

        old = unique.get(key)

        if (
            old is None
            or (
                old["home_score"] is None
                and match["home_score"] is not None
            )
        ):
            unique[key] = match

    return list(unique.values()), folders, errors


def is_completed(match):
    return (
        match["home_score"] is not None
        and match["away_score"] is not None
    )


def kickoff_datetime(match):
    match_date = date.fromisoformat(match["date"])

    if not match["time"]:
        return None

    hour, minute = map(int, match["time"].split(":"))

    return datetime.combine(
        match_date,
        time(hour, minute),
        tzinfo=TZ
    )


def is_upcoming(match, now):
    match_date = date.fromisoformat(match["date"])
    last_date = now.date() + timedelta(days=LOOKAHEAD_DAYS)

    if match_date < now.date() or match_date > last_date:
        return False

    if is_completed(match):
        return False

    kickoff = kickoff_datetime(match)

    # Bugünkü maçta saat bilgisi yoksa geçmiş/başlamış
    # olma ihtimaline karşı adayı listeye alma.
    if match_date == now.date() and kickoff is None:
        return False

    # Saat bilgisi olan maç mutlaka şu andan ileri olmalı.
    if kickoff is not None and kickoff <= now:
        return False

    return True


def team_form(matches, team_key, fixture_date, venue=None):
    previous = []

    for match in matches:
        match_date = date.fromisoformat(match["date"])

        # Yalnızca aday maçın tarihinden önceki tamamlanmış maçlar.
        if match_date >= fixture_date or not is_completed(match):
            continue

        if venue == "home":
            relevant = match["home_key"] == team_key
            scored = match["home_score"]
            conceded = match["away_score"]

        elif venue == "away":
            relevant = match["away_key"] == team_key
            scored = match["away_score"]
            conceded = match["home_score"]

        elif match["home_key"] == team_key:
            relevant = True
            scored = match["home_score"]
            conceded = match["away_score"]

        elif match["away_key"] == team_key:
            relevant = True
            scored = match["away_score"]
            conceded = match["home_score"]

        else:
            relevant = False
            scored = conceded = None

        if relevant:
            previous.append((match, scored, conceded))

    previous.sort(
        key=lambda item: item[0]["date"],
        reverse=True
    )

    previous = previous[:FORM_MATCHES]
    count = len(previous)

    if not count:
        return {
            "played": 0,
            "btts": 0,
            "rate": None,
            "scored_rate": None,
            "conceded_rate": None,
            "avg_scored": None,
            "avg_conceded": None
        }

    btts = sum(
        1 for _, scored, conceded in previous
        if scored > 0 and conceded > 0
    )

    scored_count = sum(
        1 for _, scored, _ in previous if scored > 0
    )

    conceded_count = sum(
        1 for _, _, conceded in previous if conceded > 0
    )

    return {
        "played": count,
        "btts": btts,
        "rate": round(btts / count, 3),
        "scored_rate": round(scored_count / count, 3),
        "conceded_rate": round(conceded_count / count, 3),
        "avg_scored": round(
            sum(scored for _, scored, _ in previous) / count, 2
        ),
        "avg_conceded": round(
            sum(conceded for _, _, conceded in previous) / count, 2
        )
    }


def team_quality(form):
    return (
        0.50 * form["rate"]
        + 0.25 * form["scored_rate"]
        + 0.25 * form["conceded_rate"]
    )


def build_candidates(matches, now):
    fixtures = [
        match for match in matches
        if is_upcoming(match, now)
    ]

    candidates = []

    rejected = {
        "insufficient_history": 0,
        "weak_btts": 0,
        "weak_scoring": 0,
        "weak_conceding": 0,
        "weak_venue_form": 0,
        "below_score_threshold": 0
    }

    for match in fixtures:
        fixture_date = date.fromisoformat(match["date"])

        home_form = team_form(
            matches, match["home_key"], fixture_date
        )

        away_form = team_form(
            matches, match["away_key"], fixture_date
        )

        home_home_form = team_form(
            matches, match["home_key"], fixture_date, "home"
        )

        away_away_form = team_form(
            matches, match["away_key"], fixture_date, "away"
        )

        if (
            home_form["played"] < MIN_OVERALL_MATCHES
            or away_form["played"] < MIN_OVERALL_MATCHES
        ):
            rejected["insufficient_history"] += 1
            continue

        if (
            home_form["rate"] < MIN_TEAM_BTTS_RATE
            or away_form["rate"] < MIN_TEAM_BTTS_RATE
        ):
            rejected["weak_btts"] += 1
            continue

        if (
            home_form["scored_rate"] < MIN_TEAM_SCORE_RATE
            or away_form["scored_rate"] < MIN_TEAM_SCORE_RATE
        ):
            rejected["weak_scoring"] += 1
            continue

        if (
            home_form["conceded_rate"] < MIN_TEAM_CONCEDE_RATE
            or away_form["conceded_rate"] < MIN_TEAM_CONCEDE_RATE
        ):
            rejected["weak_conceding"] += 1
            continue

        if (
            home_home_form["played"] < MIN_VENUE_MATCHES
            or away_away_form["played"] < MIN_VENUE_MATCHES
            or home_home_form["rate"] < 0.50
            or away_away_form["rate"] < 0.50
        ):
            rejected["weak_venue_form"] += 1
            continue

        overall = (
            team_quality(home_form)
            + team_quality(away_form)
        ) / 2

        venue = (
            home_home_form["rate"]
            + away_away_form["rate"]
        ) / 2

        score = round(
            100 * (0.80 * overall + 0.20 * venue)
        )

        if score < MIN_CANDIDATE_SCORE:
            rejected["below_score_threshold"] += 1
            continue

        candidates.append({
            "date": match["date"],
            "time": match["time"],
            "home": match["home"],
            "away": match["away"],
            "league": match["league"],
            "score": score,
            "selection_level": "strong",
            "selection_label": "GÜÇLÜ ADAY",
            "home_form": home_form,
            "away_form": away_form,
            "home_home_form": home_home_form,
            "away_away_form": away_away_form
        })

    candidates.sort(
        key=lambda item: (
            item["date"],
            item["time"] or "99:99",
            -item["score"]
        )
    )

    # En yüksek puanlı 3 adayı seç.
    candidates.sort(
        key=lambda item: -item["score"]
    )

    return candidates[:MAX_CANDIDATES], rejected, len(fixtures)


def main():
    now = datetime.now(TZ)
    today = now.date()

    result = {
        "updated_at": now.isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "timezone": "Europe/Istanbul",
        "scan_until": (
            today + timedelta(days=LOOKAHEAD_DAYS)
        ).isoformat(),
        "source": "open-matchday public CSV data",
        "source_url": "https://github.com/oladeeayo/open-matchday",
        "notice": (
            "Aday puanı olasılık veya garanti değildir. "
            "İstatistikler aday maç tarihinden önceki "
            "tamamlanmış karşılaşmalardan hesaplanır. "
            "Başlamış ve tamamlanmış maçlar listelenmez."
        ),
        "candidate_count": 0,
        "strong_count": 0,
        "watch_count": 0,
        "candidates": [],
        "diagnostics": {}
    }

    try:
        matches, folders, errors = load_matches()

        candidates, rejected, fixture_count = build_candidates(
            matches, now
        )

        result["candidates"] = candidates
        result["candidate_count"] = len(candidates)
        result["strong_count"] = len(candidates)

        result["diagnostics"] = {
            "competitions_discovered": len(folders),
            "matches_parsed": len(matches),
            "upcoming_fixtures": fixture_count,
            "scan_days": LOOKAHEAD_DAYS + 1,
            "rejected_candidates": rejected,
            "source_errors_count": len(errors),
            "source_errors_sample": errors[:8]
        }

        print("Türkiye saati:", now.isoformat())
        print("Taranan lig:", len(folders))
        print("Okunan maç:", len(matches))
        print("Başlamamış fikstür:", fixture_count)
        print("Seçilen aday:", len(candidates))

        for candidate in candidates:
            print(
                candidate["date"],
                candidate["time"],
                candidate["home"],
                "-",
                candidate["away"],
                candidate["score"]
            )

    except Exception as exc:
        result["diagnostics"] = {
            "fatal_error": f"{type(exc).__name__}: {exc}"
        }
        print("HATA:", exc)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("JSON yazıldı:", OUTPUT)


if __name__ == "__main__":
    main()
