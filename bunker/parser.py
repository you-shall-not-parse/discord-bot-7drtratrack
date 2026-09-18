"""Conservative text parser: unfamiliar pages never imply a clean profile."""
import html
import re
from html.parser import HTMLParser

from .models import Check, Status


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalize(body: str) -> str:
    """Decode nested JSON escapes without corrupting non-ASCII player names."""
    for _ in range(4):
        decoded = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), body)
        decoded = re.sub(r'\\([\\"/nrt])', lambda m: {"n": " ", "r": " ", "t": " "}.get(m[1], m[1]), decoded)
        decoded = html.unescape(decoded)
        if decoded == body:
            break
        body = decoded
    return body


def parse_profile(body: str, player_id: str, aliases: set[str]) -> Check:
    """Recognize bans tied to a known alias and conservatively identify clean profiles.

    No undocumented boolean fields are assumed to represent Bunker status.
    Clean pages require a matching profile URL, player heading, statistics and a
    complete HTML document. Changed templates become UNKNOWN for review.
    """
    decoded = normalize(body)
    lower = decoded.casefold()
    if any(marker in lower for marker in ("bunny-shield", "cf-chl-", "verify you are human", "captcha", "establishing a secure connection")):
        return Check(Status.UNKNOWN, evidence="Access challenge; no bypass attempted")
    if not decoded.strip():
        return Check(Status.PARSE_ERROR, evidence="Empty response")
    parser = Text()
    try:
        parser.feed(decoded)
    except Exception:
        return Check(Status.PARSE_ERROR, evidence="HTML parsing failed")
    text = re.sub(r"\s+", " ", " ".join(parser.parts))
    # Next.js can split text into adjacent serialized children.
    text = re.sub(r'"\s*,\s*"', "", text)
    for alias in sorted(aliases, key=len, reverse=True):
        if not alias:
            continue
        pattern = r"(?<!\w)" + re.escape(alias) + r"\s+was\s+Bunker\s+banned\b(?:\s+for\s+([^\r\n.<>\"\[\]{}]{1,300}))?"
        match = re.search(pattern, text, flags=re.I)
        if match:
            return Check(Status.BANNED, reason=(match[1] or "").strip(), evidence=match[0][:500])
    without_explanation = re.sub(r"Bunker Ban flags are synced from Bunker\.?", "", text, flags=re.I)
    if re.search(r"bunker.{0,40}ban|ban.{0,40}bunker", without_explanation, re.I):
        # Explanations alone are not flags, but an unrecognized ban component
        # also cannot establish a negative result.
        return Check(Status.UNKNOWN, evidence="Bunker-related text lacks a recognized player-specific status")
    headings = re.findall(r"<h1\b[^>]*>(.*?)</h1>", decoded, re.I | re.S)
    known_heading = any(re.sub(r"<[^>]*>", "", h).strip().casefold() == a.casefold()
                        for h in headings for a in aliases)
    identity = re.search(r'(?:href|content)=[\"\'](?:https://hllrecords\.com)?/profiles/' + re.escape(player_id) + r'/?[\"\']', decoded, re.I)
    stats = sum(bool(re.search(r"\b" + label + r"\b", text, re.I)) for label in ("kills", "deaths", "matches", "playtime"))
    if identity and known_heading and stats >= 2 and "</html>" in lower:
        return Check(Status.NOT_BANNED, evidence="Matched profile URL, player heading and statistics; no Bunker indication")
    if "<" not in decoded and not decoded.lstrip().startswith(("{", "[")):
        return Check(Status.PARSE_ERROR, evidence="Unrecognized response format")
    return Check(Status.UNKNOWN, evidence="Profile identity or complete profile content not established")
