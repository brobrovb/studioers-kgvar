
#!/usr/bin/env python3
import csv
import json
import re
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data"
INDEX_URL = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/LEAGUE_INDEX.md"
SEASON = "2627"

OUTPUT = Path("docs/kgvar.json")
HISTORY_OUTPUT = Path("docs/kgvar-history.json")

TZ = ZoneInfo("Europe/Istanbul")

FORM_MATCHES = 10
MIN_OVERALL_MATCHES = 8
MIN_VENUE_MATCHES = 3

MIN_TEAM_BTTS_RATE = 0.60
MIN_TEAM_SCORE_RATE = 0.70
MIN_TEAM_CONCEDE_RATE = 0.70

MIN_CANDIDATE_SCORE = 75
MAX_CANDIDATES = 3


def get_text(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "StudioErs-KGVar/3.0"}
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

    # İki tarafın skoru da yoksa tamamlanmış sayma.
    if hs is None or aws is None:
        hs = aws = None

    return {
        "date": match_date.isoformat(),
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

                content = get_text(
                    f"{BASE}/{folder}/{filename}"
                )

                for row in csv.DictReader(content.splitlines()):

                    match = parse_row(
                        row,
                        league,
                        filename
                    )

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


def team_form(matches, team_key, today, venue=None):

    previous = []

    for m in matches:

        if (
            date.fromisoformat(m["date"]) >= today
            or not is_completed(m)
        ):
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
            "rate": None,
            "scored_rate": None,
            "conceded_rate": None,
            "avg_scored": None,
            "avg_conceded": None
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


def build_candidates(matches, today):

    fixtures = [
        m for m in matches
        if m["date"] == today.isoformat()
        and not is_completed(m)
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

    for m in fixtures:

        h = team_form(
            matches,
            m["home_key"],
            today
        )

        a = team_form(
            matches,
            m["away_key"],
            today
        )

        hh = team_form(
            matches,
            m["home_key"],
            today,
            "home"
        )

        aa = team_form(
            matches,
            m["away_key"],
            today,
            "away"
        )

        if (
            h["played"] < MIN_OVERALL_MATCHES
            or a["played"] < MIN_OVERALL_MATCHES
        ):
            rejected["insufficient_history"] += 1
            continue

        if (
            h["rate"] < MIN_TEAM_BTTS_RATE
            or a["rate"] < MIN_TEAM_BTTS_RATE
        ):
            rejected["weak_btts"] += 1
            continue

        if (
            h["scored_rate"] < MIN_TEAM_SCORE_RATE
            or a["scored_rate"] < MIN_TEAM_SCORE_RATE
        ):
            rejected["weak_scoring"] += 1
            continue

        if (
            h["conceded_rate"] < MIN_TEAM_CONCEDE_RATE
            or a["conceded_rate"] < MIN_TEAM_CONCEDE_RATE
        ):
            rejected["weak_conceding"] += 1
            continue

        if (
            hh["played"] < MIN_VENUE_MATCHES
            or aa["played"] < MIN_VENUE_MATCHES
        ):
            rejected["weak_venue_form"] += 1
            continue

        if (
            hh["rate"] < 0.50
            or aa["rate"] < 0.50
        ):
            rejected["weak_venue_form"] += 1
            continue

        overall = (
            team_quality(h)
            + team_quality(a)
        ) / 2

        venue = (
            hh["rate"]
            + aa["rate"]
        ) / 2

        score = round(
            100 * (
                0.80 * overall
                + 0.20 * venue
            )
        )

        if score < MIN_CANDIDATE_SCORE:
            rejected["below_score_threshold"] += 1
            continue

        candidates.append({
            "date": m["date"],
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
            "score": score,
            "home_form": h,
            "away_form": a,
            "home_home_form": hh,
            "away_away_form": aa
        })

    candidates.sort(
        key=lambda x: (
            -x["score"],
            x["league"],
            x["home"]
        )
    )

    return candidates[:MAX_CANDIDATES], rejected, len(fixtures)


# -------------------------------
# TAHMİN GEÇMİŞİ
# -------------------------------

def load_history():

    if not HISTORY_OUTPUT.exists():
        return {"days": {}}

    try:

        data = json.loads(
            HISTORY_OUTPUT.read_text(encoding="utf-8")
        )

        if (
            isinstance(data, dict)
            and isinstance(data.get("days"), dict)
        ):
            return data

    except (OSError, json.JSONDecodeError):
        pass

    return {"days": {}}


def match_key(item):

    return (
        item.get("date", ""),
        normalize_team(item.get("home", "")),
        normalize_team(item.get("away", ""))
    )


def update_history(history, today, candidates, matches):

    day_key = today.isoformat()

    days = history.setdefault("days", {})

    # Günün ilk tahminlerini sabitle.
    # Sonraki çalıştırmalarda yeni maçlarla değiştirme.
    if day_key not in days:

        days[day_key] = {
            "date": day_key,
            "created_at": datetime.now(TZ).isoformat(
                timespec="seconds"
            ),
            "candidates": [
                {
                    **c,
                    "status": "pending",
                    "actual_home_score": None,
                    "actual_away_score": None,
                    "btts_result": None
                }
                for c in candidates
            ]
        }

    # Tamamlanmış maçları gerçek sonuçlarla eşleştir.
    completed = {}

    for m in matches:

        if is_completed(m):
            completed[match_key(m)] = m

    # Geçmiş günlerin de sonuçlarını kontrol et.
    for day in days.values():

        for item in day.get("candidates", []):

            if item.get("status") == "finished":
                continue

            actual = completed.get(match_key(item))

            if actual:

                hs = actual["home_score"]
                aws = actual["away_score"]

                item["actual_home_score"] = hs
                item["actual_away_score"] = aws

                item["status"] = "finished"

                # İki takım da gol attıysa bildik.
                item["btts_result"] = (
                    hs > 0 and aws > 0
                )

    return history


def history_summary(history):

    total = 0
    correct = 0
    wrong = 0
    pending = 0

    for day in history.get("days", {}).values():

        for item in day.get("candidates", []):

            total += 1

            if (
                item.get("status") != "finished"
                or item.get("btts_result") is None
            ):
                pending += 1

            elif item["btts_result"]:
                correct += 1

            else:
                wrong += 1

    decided = correct + wrong

    success_rate = (
        round(100 * correct / decided, 1)
        if decided else None
    )

    return {
        "total_picks": total,
        "correct": correct,
        "wrong": wrong,
        "pending": pending,
        "success_rate": success_rate
    }


# -------------------------------
# ANA PROGRAM
# -------------------------------

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
            "KG Var tahmini, iki takımın da gol atması durumunda "
            "başarılı sayılır. Günlük tahminler sabitlenir. "
            "Sonuçlar kaynak CSV'ye işlendiğinde otomatik "
            "olarak değerlendirilir. Aday puanı garanti değildir."
        ),

        "candidate_count": 0,
        "candidates": [],
        "diagnostics": {},
        "history_summary": {}
    }

    try:

        matches, folders, errors = load_matches()

        candidates, rejected, fixture_count = build_candidates(
            matches,
            today
        )

        history = load_history()

        history = update_history(
            history,
            today,
            candidates,
            matches
        )

        today_record = history["days"].get(
            today.isoformat(),
            {}
        )

        result["candidates"] = today_record.get(
            "candidates",
            []
        )

        result["candidate_count"] = len(
            result["candidates"]
        )

        result["history_summary"] = history_summary(
            history
        )

        result["history"] = history

        result["diagnostics"] = {
            "competitions_discovered": len(folders),
            "matches_parsed": len(matches),
            "today_fixtures": fixture_count,
            "rejected_candidates": rejected,
            "source_errors_count": len(errors),
            "source_errors_sample": errors[:8]
        }

        print("Türkiye tarihi:", today.isoformat())

        print(
            "Lig/organizasyon:",
            len(folders),
            "| Okunan maç:",
            len(matches)
        )

        print(
            "Bugünkü fikstür:",
            fixture_count,
            "| İlk seçim:",
            len(candidates)
        )

        print(
            "Tahmin geçmişi:",
            result["history_summary"]
        )

        for m in result["candidates"]:

            if m.get("btts_result") is True:
                verdict = "✓ BİLDİK"

            elif m.get("btts_result") is False:
                verdict = "✕ BİLEMEDİK"

            else:
                verdict = "BEKLİYOR"

            print(
                f"{m['home']} - {m['away']}: "
                f"{m['score']}/100 | {verdict}"
            )

        HISTORY_OUTPUT.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        HISTORY_OUTPUT.write_text(
            json.dumps(
                history,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    except Exception as exc:

        result["diagnostics"] = {
            "fatal_error": (
                f"{type(exc).__name__}: {exc}"
            )
        }

        print("HATA:", exc)

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUTPUT.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("Sonuç yazıldı:", OUTPUT)
    print("Tahmin geçmişi:", HISTORY_OUTPUT)


if __name__ == "__main__":
    main()
