
#!/usr/bin/env python3
import csv
import json
import re
import unicodedata
import urllib.request
import urllib.error

from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data"
INDEX_URL = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/LEAGUE_INDEX.md"

SEASON = "2627"
OUTPUT = Path("docs/kgvar.json")
TZ = ZoneInfo("Europe/Istanbul")

# Tarama ayarları
LOOKAHEAD_DAYS = 3
MAX_CANDIDATES = 6
FORM_MATCHES = 10

# Güçlü aday eşikleri
STRONG_MIN_HISTORY = 8
STRONG_MIN_BTTS = 0.55
STRONG_MIN_SCORE_RATE = 0.65
STRONG_MIN_CONCEDE_RATE = 0.65
STRONG_MIN_VENUE_HISTORY = 2
STRONG_MIN_VENUE_BTTS = 0.45
STRONG_MIN_SCORE = 70

# İzleme listesi: güçlü aday bulunmazsa daha geniş tarama
WATCH_MIN_HISTORY = 6
WATCH_MIN_BTTS = 0.45
WATCH_MIN_SCORE_RATE = 0.60
WATCH_MIN_CONCEDE_RATE = 0.60
WATCH_MIN_VENUE_HISTORY = 2
WATCH_MIN_SCORE = 60


def get_text(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "StudioErs-KGVar/3.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8-sig", errors="replace")


def get_date(value):
    value = (value or "").strip()
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).date()
    except ValueError:
        pass

    for fmt in (
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%d-%m-%Y"
    ):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue

    return None


def get_score(value):
    try:
        value = str(value).strip()
        if not value:
            return None

        n = int(float(value))
        return n if n >= 0 else None

    except (ValueError, TypeError):
        return None


def value(row, *names):
    data = {
        str(k).strip().lower(): (v or "").strip()
        for k, v in row.items()
        if k
    }

    for name in names:
        result = data.get(name.lower(), "")
        if result != "":
            return result

    return ""


def normalize_team(name):
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(
        c for c in s
        if not unicodedata.combining(c)
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

    home = value(
        row, "home", "home_team", "hometeam", "team_home"
    )

    away = value(
        row, "away", "away_team", "awayteam", "team_away"
    )

    if not match_date or not home or not away:
        return None

    hs = get_score(
        value(
            row,
            "ft_home",
            "home_score",
            "homescore",
            "home_goals",
            "fthg"
        )
    )

    aws = get_score(
        value(
            row,
            "ft_away",
            "away_score",
            "awayscore",
            "away_goals",
            "ftag"
        )
    )

    # Yalnızca iki skor da varsa tamamlanmış kabul edilir.
    if hs is None or aws is None:
        hs = aws = None

    return {
        "date": match_date.isoformat(),
        "time": value(row, "time", "kickoff", "start_time"),
        "home": home,
        "away": away,
        "home_key": normalize_team(home),
        "away_key": normalize_team(away),
        "home_score": hs,
        "away_score": aws,
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
            "Lig klasörleri bulunamadı."
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
            url = f"{BASE}/{folder}/{filename}"

            try:
                content = get_text(url)

                for row in csv.DictReader(content.splitlines()):
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
                    f"{league}/{filename}: {type(exc).__name__}: {exc}"
                )

    unique = {}

    for m in matches:
        key = (
            m["date"],
            m["home_key"],
            m["away_key"],
            m["league"]
        )

        old = unique.get(key)

        if (
            old is None
            or (
                old["home_score"] is None
                and m["home_score"] is not None
            )
        ):
            unique[key] = m

    return list(unique.values()), folders, errors


def is_completed(m):
    return (
        m["home_score"] is not None
        and m["away_score"] is not None
    )


def team_form(matches, team_key, fixture_date, venue=None):
    previous = []

    for m in matches:
        match_date = date.fromisoformat(m["date"])

        if match_date >= fixture_date:
            continue

        if not is_completed(m):
            continue

        if venue == "home":
            relevant = m["home_key"] == team_key
            scored = m["home_score"]
            conceded = m["away_score"]

        elif venue == "away":
            relevant = m["away_key"] == team_key
            scored = m["away_score"]
            conceded = m["home_score"]

        elif m["home_key"] == team_key:
            relevant = True
            scored = m["home_score"]
            conceded = m["away_score"]

        elif m["away_key"] == team_key:
            relevant = True
            scored = m["away_score"]
            conceded = m["home_score"]

        else:
            relevant = False
            scored = conceded = None

        if relevant:
            previous.append((m, scored, conceded))

    previous.sort(
        key=lambda x: x[0]["date"],
        reverse=True
    )

    previous = previous[:FORM_MATCHES]
    n = len(previous)

    if not n:
        return {
            "played": 0,
            "btts": 0,
            "rate": 0,
            "scored_rate": 0,
            "conceded_rate": 0,
            "avg_scored": 0,
            "avg_conceded": 0
        }

    btts = sum(
        1 for _, s, c in previous
        if s > 0 and c > 0
    )

    scored_n = sum(
        1 for _, s, c in previous
        if s > 0
    )

    conceded_n = sum(
        1 for _, s, c in previous
        if c > 0
    )

    return {
        "played": n,
        "btts": btts,
        "rate": round(btts / n, 3),
        "scored_rate": round(scored_n / n, 3),
        "conceded_rate": round(conceded_n / n, 3),
        "avg_scored": round(
            sum(s for _, s, _ in previous) / n, 2
        ),
        "avg_conceded": round(
            sum(c for _, _, c in previous) / n, 2
        )
    }


def team_quality(form):
    return (
        0.50 * form["rate"]
        + 0.25 * form["scored_rate"]
        + 0.25 * form["conceded_rate"]
    )


def candidate_score(h, a, hh, aa):
    overall = (
        team_quality(h) + team_quality(a)
    ) / 2

    venue = (
        hh["rate"] + aa["rate"]
    ) / 2

    return round(
        100 * (0.80 * overall + 0.20 * venue)
    )


def analyze_fixture(m, matches):
    fixture_date = date.fromisoformat(m["date"])

    h = team_form(
        matches, m["home_key"], fixture_date
    )

    a = team_form(
        matches, m["away_key"], fixture_date
    )

    hh = team_form(
        matches, m["home_key"], fixture_date, "home"
    )

    aa = team_form(
        matches, m["away_key"], fixture_date, "away"
    )

    score = candidate_score(h, a, hh, aa)

    strong = (
        h["played"] >= STRONG_MIN_HISTORY
        and a["played"] >= STRONG_MIN_HISTORY
        and h["rate"] >= STRONG_MIN_BTTS
        and a["rate"] >= STRONG_MIN_BTTS
        and h["scored_rate"] >= STRONG_MIN_SCORE_RATE
        and a["scored_rate"] >= STRONG_MIN_SCORE_RATE
        and h["conceded_rate"] >= STRONG_MIN_CONCEDE_RATE
        and a["conceded_rate"] >= STRONG_MIN_CONCEDE_RATE
        and hh["played"] >= STRONG_MIN_VENUE_HISTORY
        and aa["played"] >= STRONG_MIN_VENUE_HISTORY
        and hh["rate"] >= STRONG_MIN_VENUE_BTTS
        and aa["rate"] >= STRONG_MIN_VENUE_BTTS
        and score >= STRONG_MIN_SCORE
    )

    watch = (
        h["played"] >= WATCH_MIN_HISTORY
        and a["played"] >= WATCH_MIN_HISTORY
        and h["rate"] >= WATCH_MIN_BTTS
        and a["rate"] >= WATCH_MIN_BTTS
        and h["scored_rate"] >= WATCH_MIN_SCORE_RATE
        and a["scored_rate"] >= WATCH_MIN_SCORE_RATE
        and h["conceded_rate"] >= WATCH_MIN_CONCEDE_RATE
        and a["conceded_rate"] >= WATCH_MIN_CONCEDE_RATE
        and hh["played"] >= WATCH_MIN_VENUE_HISTORY
        and aa["played"] >= WATCH_MIN_VENUE_HISTORY
        and score >= WATCH_MIN_SCORE
    )

    if strong:
        level = "strong"
        label = "GÜÇLÜ ADAY"
    elif watch:
        level = "watch"
        label = "İZLEME ADAYI"
    else:
        return None

    return {
        "date": m["date"],
        "time": m["time"],
        "home": m["home"],
        "away": m["away"],
        "league": m["league"],
        "score": score,
        "selection_level": level,
        "selection_label": label,
        "home_form": h,
        "away_form": a,
        "home_home_form": hh,
        "away_away_form": aa
    }


def build_candidates(matches, today):
    end_date = today + timedelta(days=LOOKAHEAD_DAYS)

    fixtures = [
        m for m in matches
        if today <= date.fromisoformat(m["date"]) <= end_date
        and not is_completed(m)
    ]

    analyzed = []

    rejected = {
        "insufficient_history": 0,
        "weak_statistics": 0,
        "no_candidate": 0
    }

    for fixture in fixtures:
        result = analyze_fixture(fixture, matches)

        if result:
            analyzed.append(result)
        else:
            rejected["no_candidate"] += 1

    # Önce güçlü adayları göster.
    strong = [
        x for x in analyzed
        if x["selection_level"] == "strong"
    ]

    watch = [
        x for x in analyzed
        if x["selection_level"] == "watch"
    ]

    strong.sort(
        key=lambda x: (-x["score"], x["date"], x["league"])
    )

    watch.sort(
        key=lambda x: (-x["score"], x["date"], x["league"])
    )

    # Güçlü adaylar önce gelir. Kalan yerleri izleme adayları doldurur.
    selected = strong[:MAX_CANDIDATES]

    remaining = MAX_CANDIDATES - len(selected)

    if remaining > 0:
        selected.extend(watch[:remaining])

    return selected, rejected, len(fixtures), len(strong), len(watch)


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
            "Güçlü aday ve izleme adayı ayrı sınıflandırılır. "
            "Puan gerçekleşme olasılığı veya garanti değildir. "
            "İstatistikler geçmiş tamamlanmış maçlardan hesaplanır."
        ),
        "candidate_count": 0,
        "strong_count": 0,
        "watch_count": 0,
        "candidates": [],
        "diagnostics": {}
    }

    try:
        matches, folders, errors = load_matches()

        (
            candidates,
            rejected,
            fixture_count,
            strong_count,
            watch_count
        ) = build_candidates(matches, today)

        result["candidates"] = candidates
        result["candidate_count"] = len(candidates)
        result["strong_count"] = strong_count
        result["watch_count"] = watch_count

        result["diagnostics"] = {
            "competitions_discovered": len(folders),
            "matches_parsed": len(matches),
            "upcoming_fixtures": fixture_count,
            "scan_days": LOOKAHEAD_DAYS,
            "rejected_candidates": rejected,
            "source_errors_count": len(errors),
            "source_errors_sample": errors[:8]
        }

        print("Türkiye tarihi:", today.isoformat())
        print("Tarama bitişi:", result["scan_until"])
        print("Lig sayısı:", len(folders))
        print("Okunan maç:", len(matches))
        print("Yaklaşan fikstür:", fixture_count)
        print("Güçlü aday:", strong_count)
        print("İzleme adayı:", watch_count)
        print("Gösterilecek aday:", len(candidates))

        for m in candidates:
            print(
                m["date"],
                m["home"], "-",
                m["away"],
                m["score"],
                m["selection_label"]
            )

    except Exception as exc:
        result["diagnostics"] = {
            "fatal_error": f"{type(exc).__name__}: {exc}"
        }
        print("HATA:", exc)

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("JSON yazıldı:", OUTPUT)


if __name__ == "__main__":
    main()
