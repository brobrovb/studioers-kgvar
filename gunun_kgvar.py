
#!/usr/bin/env python3
import csv
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

# Doğru kaynak: master dalı ve data klasörü
BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data"
INDEX_URL = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/LEAGUE_INDEX.md"

SEASON = "2627"
OUTPUT = Path("docs/kgvar.json")
TZ = ZoneInfo("Europe/Istanbul")


def get_text(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "StudioErs-KGVar/1.0"}
    )
    with urllib.request.urlopen(req, timeout=25) as response:
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
        value = str(value).strip()
        if not value:
            return None
        return int(float(value))
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
        # Kaynaktaki gerçek skor sütunları ft_home / ft_away
        "home_score": get_score(value(
            row, "ft_home", "home_score", "homescore",
            "home_goals", "fthg"
        )),
        "away_score": get_score(value(
            row, "ft_away", "away_score", "awayscore",
            "away_goals", "ftag"
        )),
        "league": league,
        "source_file": filename,
    }


def discover_competitions():
    text = get_text(INDEX_URL)

    # Örnek: ENG_1_premier-league, TUR_1_super-lig
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

            except (
                urllib.error.URLError,
                TimeoutError,
                ValueError,
                csv.Error,
            ) as exc:
                errors.append(
                    f"{league}/{filename}: "
                    f"{type(exc).__name__}: {exc}"
                )

    # Aynı maç birden fazla kez geldiyse tekilleştir.
    unique = {}

    for match in matches:
        key = (
            match["date"],
            match["home"].casefold(),
            match["away"].casefold(),
        )

        old = unique.get(key)

        if old is None:
            unique[key] = match
        elif (
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

        # Yalnızca bugünden önce tamamlanmış ve skoru bilinen maçlar.
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
        match for match in matches
        if match["date"] == today.isoformat()
        and match["home_score"] is None
        and match["away_score"] is None
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
        "source_url": "https://github.com/oladeeayo/open-matchday",
        "notice": (
            "100 üzerinden puan bir olasılık veya garanti değildir. "
            "Geçmiş maçlardaki KG Var oranlarından oluşturulan "
            "sıralama puanıdır. Kaynak veriler eksik olabilir."
        ),
        "candidate_count": 0,
        "candidates": [],
        "diagnostics": {},
    }

    try:
        matches, folders, errors = load_matches()

        candidates = build_candidates(matches, today)
        result["candidates"] = candidates
        result["candidate_count"] = len(candidates)

        result["diagnostics"] = {
            "competitions_discovered": len(folders),
            "matches_parsed": len(matches),
            "source_errors_count": len(errors),
            "source_errors_sample": errors[:8],
        }

        print("Türkiye tarihi:", today.isoformat())
        print("Bulunan lig/organizasyon:", len(folders))
        print("Okunan maç:", len(matches))
        print("Kaynak hatası:", len(errors))
        print("Aday sayısı:", len(candidates))

        for match in candidates:
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
