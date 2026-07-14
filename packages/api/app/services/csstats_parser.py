"""Parse csstats.gg HTML (match pages and profile /stats fragments)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bs4 import BeautifulSoup

MATCH_ID_RE = re.compile(r"/match/(\d+)")
STEAM64_RE = re.compile(r"^\d{17}$")
SCORE_RE = re.compile(r"^(\d+):(\d+)$")
ORDINAL_DATE_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})"
)


@dataclass
class CsstatsPlayerRow:
    steam64_id: str
    name: str | None
    team: str
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    headshot_pct: float | None = None
    adr: float | None = None
    rating: float | None = None


@dataclass
class CsstatsMatchSummary:
    match_id: str
    map: str | None = None
    mode: str | None = None
    played_at: datetime | None = None
    score_team_a: int | None = None
    score_team_b: int | None = None
    players: list[CsstatsPlayerRow] = field(default_factory=list)


@dataclass
class CsstatsProfileMatchStub:
    match_id: str
    played_at: datetime | None = None
    map: str | None = None
    score: str | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    rating: float | None = None


def extract_csstats_match_id(value: str) -> str | None:
    text = value.strip()
    if text.isdigit():
        return text
    match = MATCH_ID_RE.search(text)
    return match.group(1) if match else None


def _parse_csstats_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    cleaned = text.strip()
    match = ORDINAL_DATE_RE.search(cleaned)
    if match:
        day, month, year, time_part = match.groups()
        try:
            dt = datetime.strptime(f"{day} {month} {year} {time_part}", "%d %b %Y %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    for fmt in ("%d %b %Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_percent(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip().rstrip("%")
    parsed = _parse_float(text)
    return parsed


def _team_label_to_key(label: str) -> str:
    if re.search(r"\bTeam 2\b", label):
        return "team_b"
    return "team_a"


def _parse_player_row(tr, team: str) -> CsstatsPlayerRow | None:
    link = tr.select_one('a.player-link[href*="/player/"]')
    if not link:
        return None

    href = link.get("href") or ""
    steam_match = re.search(r"/player/(\d+)", href)
    if not steam_match or not STEAM64_RE.match(steam_match.group(1)):
        return None

    name_span = link.select_one("span")
    name = (name_span.get_text(strip=True) if name_span else link.get_text(strip=True)) or None

    cells = tr.find_all("td", recursive=False)
    kills = deaths = assists = None
    headshot_pct = adr = rating = None

    stat_idx = None
    for idx, cell in enumerate(cells):
        if cell.select_one('input[id$="-xhair"]') or cell.select_one(".glyphicon-screenshot"):
            stat_idx = idx + 1
            break

    if stat_idx is not None and stat_idx + 2 < len(cells):
        kills = _parse_int(cells[stat_idx].get_text())
        deaths = _parse_int(cells[stat_idx + 1].get_text())
        assists = _parse_int(cells[stat_idx + 2].get_text())

    for cell in cells:
        text = cell.get_text(strip=True)
        if text.endswith("%") and headshot_pct is None:
            headshot_pct = _parse_percent(text)
        adr_title = cell.get("title") or ""
        if " over " in adr_title and " rounds" in adr_title:
            adr = _parse_float(text)
        rating_span = cell.select_one('span[style*="border-radius"]')
        if rating_span:
            rating = _parse_float(rating_span.get_text())

    return CsstatsPlayerRow(
        steam64_id=steam_match.group(1),
        name=name,
        team=team,
        kills=kills,
        deaths=deaths,
        assists=assists,
        headshot_pct=headshot_pct,
        adr=adr,
        rating=rating,
    )


def parse_csstats_match_html(html: str, match_id: str | None = None) -> CsstatsMatchSummary:
    soup = BeautifulSoup(html, "html.parser")
    resolved_id = match_id or extract_csstats_match_id(html) or ""
    if not resolved_id:
        watch = soup.select_one('a[href*="/match/"][href*="/watch/"]')
        if watch:
            resolved_id = extract_csstats_match_id(watch.get("href", "")) or ""

    map_name = None
    for img in soup.select("#match-info img[alt^='de_']"):
        alt = img.get("alt")
        if alt:
            map_name = alt.lower()
            break
    if not map_name:
        for candidate in soup.select("#match-info .info"):
            text = candidate.get_text(" ", strip=True)
            if text.startswith("de_"):
                map_name = text.split()[0].lower()

    mode = None
    for info in soup.select("#match-info .info"):
        text = info.get_text(" ", strip=True)
        if text and not text.startswith("de_") and "Server" not in text and "Avg Rank" not in text:
            if "Matchmaking" in text or "Premier" in text or "Competitive" in text or "FACEIT" in text:
                mode = text
                break

    played_at = None
    for info in soup.select("#match-info .info, #last-info .info"):
        if info.select_one(".glyphicon-time"):
            played_at = _parse_csstats_datetime(info.get_text(" ", strip=True))
            if played_at:
                break

    score_a = score_b = None
    team0 = soup.select_one(".team-0-score .team-score-number")
    team1 = soup.select_one(".team-1-score .team-score-number")
    if team0:
        score_a = _parse_int(team0.get_text())
    if team1:
        score_b = _parse_int(team1.get_text())

    players: list[CsstatsPlayerRow] = []
    scoreboard = soup.find("table", id="match-scoreboard")
    if scoreboard:
        current_team = "team_a"
        for tbody in scoreboard.find_all("tbody"):
            for td in tbody.find_all("td", recursive=True):
                text = td.get_text(" ", strip=True)
                if re.search(r"\bTeam 1\b", text):
                    current_team = "team_a"
                    break
                if re.search(r"\bTeam 2\b", text):
                    current_team = "team_b"
                    break
            for tr in tbody.find_all("tr", recursive=False):
                row_text = tr.get_text(" ", strip=True)
                if re.search(r"\bTeam [12]\b", row_text) and not tr.select_one("a.player-link"):
                    current_team = _team_label_to_key(row_text)
                    continue
                player = _parse_player_row(tr, current_team)
                if player:
                    players.append(player)

    return CsstatsMatchSummary(
        match_id=resolved_id,
        map=map_name,
        mode=mode,
        played_at=played_at,
        score_team_a=score_a,
        score_team_b=score_b,
        players=players,
    )


def parse_csstats_profile_stats_html(html: str) -> list[CsstatsProfileMatchStub]:
    soup = BeautifulSoup(html, "html.parser")
    matches: list[CsstatsProfileMatchStub] = []
    seen: set[str] = set()

    for tr in soup.select("#player-matches tr.p-row"):
        onclick = tr.get("onclick") or ""
        match = re.search(r"window\.location='/match/(\d+)'", onclick)
        if not match:
            link = tr.select_one('a.match-list-link[href*="/match/"]')
            if link:
                match = MATCH_ID_RE.search(link.get("href", ""))
        if not match:
            continue

        match_id = match.group(1)
        if match_id in seen:
            continue
        seen.add(match_id)

        ts_el = tr.select_one("[data-timestamp]")
        played_at = None
        if ts_el:
            ts = _parse_int(ts_el.get("data-timestamp"))
            if ts:
                played_at = datetime.fromtimestamp(ts, tz=timezone.utc)

        map_name = None
        map_img = tr.select_one("img[alt^='de_']")
        if map_img and map_img.get("alt"):
            map_name = map_img["alt"].lower()

        score = None
        score_span = tr.select_one('span[style*="font-weight:bold"]')
        if score_span:
            text = score_span.get_text(strip=True)
            if SCORE_RE.match(text):
                score = text

        cells = tr.find_all("td")
        kills = deaths = assists = rating = None
        numeric: list[int] = []
        for cell in cells:
            text = cell.get_text(strip=True)
            if re.fullmatch(r"\d+", text):
                numeric.append(int(text))
            rating_match = re.search(r"([\d.]+)", text)
            if cell.get("class") and "split" in cell.get("class", []) and rating_match:
                try:
                    rating = float(rating_match.group(1))
                except ValueError:
                    pass

        if len(numeric) >= 3:
            kills, deaths, assists = numeric[0], numeric[1], numeric[2]

        matches.append(
            CsstatsProfileMatchStub(
                match_id=match_id,
                played_at=played_at,
                map=map_name,
                score=score,
                kills=kills,
                deaths=deaths,
                assists=assists,
                rating=rating,
            )
        )

    return matches
