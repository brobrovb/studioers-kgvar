
#!/usr/bin/env python3
import csv
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday/main"
INDEX_URL = BASE + "/LEAGUE_INDEX.md"
SEASON = "2627"
OUTPUT = Path("docs/kgvar.json")
TZ = ZoneInfo("Europe/Istanbul")


def get_text(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "StudioErs-KGVar/1.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as response:
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

    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


def get_score(value):
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def value(row, *names):
    data = {
        str(k).strip().lower(): (v or "").strip()
        for k, v in row.items() if k
    }
    for name in names:
        if name.lower() in data:
            return data[name.lower()]
    return ""


def parse_row(row, league, filename):
    match_date = get_date(value(
        row, "date", "match_date", "fixture_date", "datetime"
    ))
    home = value(row, "home", "home_team", "hometeam", "team_home")
    away = value(row, "away", "away_team", "awayteam", "team_away")

    if not match_date or not home or not away:
        return None

    return {
        "date": match_date.isoformat(),
        "home": home,
        "away": away,
        "home_score": get_score(value(
            row, "home_score", "homescore", "home_goals", "fthg"
        )),
        "away_score": get_score(value(
            row, "away_score", "awayscore", "away_goals", "ftag"
        )),
        "league": league,
        "source_file": filename,
    }


def discover_competitions():
    text = get_text(INDEX_URL)
    folders = set()

    for link in re.findall(r"\]\(([^)]+)\)", text):
        link = link.split("#", 1)[0].strip()

        marker = "/blob/main/"
        if marker in link:
            link = link.split(marker, 1)[1]

        link = link.lstrip("./")

        for filename in (
            f"results_{SEASON}.csv",
            f"fixtures_{SEASON}.csv",
        ):
            suffix = "/" + filename
            if link.endswith(suffix):
                folders.add(link[:-len(suffix)])

    if not folders:
        raise RuntimeError(
            "Lig listesinde beklenen CSV bağlantıları bulunamadı. "
            "Veri kaynağının dosya yapısı değişmiş olabilir."
        )

    return sorted(folders)


def load_matches():
    matches = []
    errors = []
    folders = discover_competitions()

    for folder in folders:
        league = folder.rsplit("/", 1)[-1]
        for filename in (
            f"results_{SEASON}.csv",
            f"fixtures_{SEASON}.csv",
        ):
            url = f"{BASE}/{folder}/{filename}"

            try:
                content = get_text(url)
                rows = csv.DictReader(content.splitlines())

                for row in rows:
                    match = parse_row(row, league, filename)
                    if match:
                        matches.append(match)

            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                errors.append(
                    f"{league}/{filename}: {type(exc).__name__}"
                )

    unique = {}
    for match in matches:
        key = (
            match["date"],
            match["home"].casefold(),
            match["away"].casefold(),
        )
        old = unique.get(key)
        if old is None or (
            old["home_score"] is None
            and match["home_score"] is not None
        ):
            unique[key] = match

    return list(unique.values()), folders, errors


def is_btts(match):
    home = match["home_score"]
    away = match["away_score"]

    if home is None or away is None:
        return None

    return home > 0 and away > 0


def team_form(matches, team, today, venue=None):
    team_key = team.casefold()
    previous = []

    for match in matches:
        match_day = date.fromisoformat(match["date"])

        if match_day >= today or is_btts(match) is None:
            continue

        if venue == "home":
            relevant = match["home"].casefold() == team_key
        elif venue == "away":
            relevant = match["away"].casefold() == team_key
        else:
            relevant = team_key in (
                match["home"].casefold(),
                match["away"].casefold(),
            )

        if relevant:
            previous.append(match)

    previous.sort(key=lambda m: m["date"], reverse=True)
    previous = previous[:8]

    if not previous:
        return {"played": 0, "btts": 0, "rate": None}

    count = sum(1 for match in previous if is_btts(match))

    return {
        "played": len(previous),
        "btts": count,
        "rate": round(count / len(previous), 3),
    }


def build_candidates(matches, today):
    fixtures = [
        m for m in matches
        if m["date"] == today.isoformat()
        and m["home_score"] is None
        and m["away_score"] is None
    ]

    candidates = []

    for match in fixtures:
        home = team_form(matches, match["home"], today)
        away = team_form(matches, match["away"], today)
        home_home = team_form(
            matches, match["home"], today, "home"
        )
        away_away = team_form(
            matches, match["away"], today, "away"
        )

        forms = [
            (home, 0.35),
            (away, 0.35),
            (home_home, 0.15),
            (away_away, 0.15),
        ]

        available = [
            (form["rate"], weight)
            for form, weight in forms
            if form["rate"] is not None
        ]

        if not available:
            continue

        score = round(
            100 * sum(rate * weight for rate, weight in available)
            / sum(weight for _, weight in available)
        )

        if score < 55:
            continue

        candidates.append({
            "date": match["date"],
            "home": match["home"],
            "away": match["away"],
            "league": match["league"],
            "score": score,
            "home_form": home,
            "away_form": away,
            "home_home_form": home_home,
            "away_away_form": away_away,
        })

    candidates.sort(
        key=lambda m: (-m["score"], m["league"], m["home"])
    )
    return candidates[:3]


def main():
    now = datetime.now(TZ)
    today = now.date()

    result = {
        "updated_at": now.isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "timezone": "Europe/Istanbul",
        "source": "open-matchday public CSV data",
        "notice": (
            "Puan olasılık veya garanti değildir; geçmiş maçlardan "
            "üretilmiş sıralama puanıdır. Fikstürler eksik olabilir."
        ),
        "candidate_count": 0,
        "candidates": [],
        "diagnostics": {},
    }

    try:
        matches, folders, errors = load_matches()
        result["candidates"] = build_candidates(matches, today)
        result["candidate_count"] = len(result["candidates"])
        result["diagnostics"] = {
            "competitions_discovered": len(folders),
            "matches_parsed": len(matches),
            "source_errors_count": len(errors),
            "source_errors_sample": errors[:8],
        }

        print("Türkiye tarihi:", today.isoformat())
        print("Bulunan lig/organizasyon:", len(folders))
        print("Okunan maç:", len(matches))
        print("Aday sayısı:", result["candidate_count"])

        for match in result["candidates"]:
            print(
                f"{match['home']} - {match['away']}: "
                f"{match['score']}/100"
            )

    except Exception as exc:
        result["diagnostics"] = {
            "fatal_error": f"{type(exc).__name__}: {exc}"
        }
        print("HATA:", exc)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("Sonuç yazıldı:", OUTPUT)


if __name__ == "__main__":
    main()
