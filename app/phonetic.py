"""Turn ASR words into copy.

EAM on-air structure (per priyom.org's HFGCS notes):
  "All stations, all stations, this is <callsign> ..."
  <first six characters>, "stand by" (x3)
  "Message follows" | "Message of <N> characters follows"
  <body, usually 30 characters>
  "I say again"
  <body again>
  "This is <callsign>, out" | "... more to follow, stand by"

Skyking: "Skyking, Skyking, do not answer", codeword x2, "time" + 2 digits,
"authentication" + 2 characters, "I say again", repeat, "this is <callsign>, out".

The body is sent twice, so the two copies are merged position by position:
where one copy has a '?' the other fills it; where they disagree it stays '?'.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

NATO = {
    "alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D",
    "echo": "E", "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I",
    "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L", "mike": "M",
    "november": "N", "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R",
    "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "whisky": "W", "xray": "X", "yankee": "Y", "zulu": "Z",
    "zero": "0", "one": "1", "two": "2", "three": "3", "tree": "3",
    "four": "4", "fower": "4", "five": "5", "fife": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "niner": "9",
}
_FUZZY_KEYS = [k for k in NATO if len(k) >= 4]

CARDINAL = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

DEFAULT_EAM_LEN = 30
MIN_EAM_RUN = 20  # a char run this long with no markers is still treated as an EAM


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float


@dataclass
class Tok:
    kind: str          # "char" | "word"
    value: str         # character, or normalized word
    start: float
    end: float
    prob: float
    raw: str
    alt: Optional[str] = None  # best-guess char when value is '?'


@dataclass
class Char:
    c: str
    t0: float
    t1: float
    p: float
    alts: list[str] = field(default_factory=list)


@dataclass
class Parsed:
    kind: str                      # "eam" | "skyking" | "voice"
    callsign: Optional[str] = None
    header: str = ""
    body: str = ""
    copy1: str = ""
    copy2: str = ""
    expected_len: Optional[int] = None
    chars: list[Char] = field(default_factory=list)   # merged body, for UI seek
    skyking: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(raw: str) -> str:
    s = raw.lower().strip()
    s = s.replace("x-ray", "xray").replace("x ray", "xray")
    return re.sub(r"[^a-z0-9]", "", s)


def tokenize(words: list[Word], min_prob: float) -> list[Tok]:
    toks: list[Tok] = []
    for w in words:
        n = _norm(w.text)
        if not n:
            continue
        if n.isdigit():                       # Whisper sometimes emits "25" for "two five"
            dur = (w.end - w.start) / len(n)
            for i, d in enumerate(n):
                toks.append(Tok("char", d, w.start + i * dur, w.start + (i + 1) * dur,
                                w.prob, w.text))
            continue
        ch = NATO.get(n)
        if ch is None and len(n) >= 4:
            m = difflib.get_close_matches(n, _FUZZY_KEYS, n=1, cutoff=0.8)
            if m:
                ch = NATO[m[0]]
        if ch is not None:
            if w.prob < min_prob:
                toks.append(Tok("char", "?", w.start, w.end, w.prob, w.text, alt=ch))
            else:
                toks.append(Tok("char", ch, w.start, w.end, w.prob, w.text))
        else:
            toks.append(Tok("word", n, w.start, w.end, w.prob, w.text))
    # an unknown word sandwiched between characters is almost always a garbled character
    for i in range(1, len(toks) - 1):
        t = toks[i]
        if t.kind == "word" and toks[i - 1].kind == "char" and toks[i + 1].kind == "char" \
                and not _is_marker_word(t.value):
            toks[i] = Tok("char", "?", t.start, t.end, t.prob, t.raw)
    return toks


_MARKER_WORDS = {"message", "say", "again", "stand", "standby", "by", "this",
                 "is", "out", "time", "authentication", "characters", "of", "all",
                 "stations", "skyking", "sky", "king", "do", "not", "answer", "more",
                 "to", "follow", "follows", "followed", "following", "for", "i"}


def _is_marker_word(w: str) -> bool:
    return w in _MARKER_WORDS


def _words_only(toks: list[Tok]) -> list[str]:
    """Token stream as plain words; chars are re-spelled so phrase search works."""
    out = []
    for t in toks:
        if t.kind == "word":
            out.append(t.value)
        else:
            out.append(_norm(t.raw) or t.value.lower())
    return out


def _find(seq: list[str], phrase: list[str], start: int = 0) -> int:
    n = len(phrase)
    for i in range(start, len(seq) - n + 1):
        if seq[i:i + n] == phrase:
            return i
    return -1


def _find_any(seq: list[str], phrases: list[list[str]], start: int = 0) -> tuple[int, int]:
    best = (-1, 0)
    for p in phrases:
        i = _find(seq, p, start)
        if i >= 0 and (best[0] < 0 or i < best[0]):
            best = (i, len(p))
    return best


SAY_AGAIN = [["i", "say", "again"], ["say", "again"]]
STAND_BY = [["stand", "by"], ["standby"]]
THIS_IS = [["this", "is"]]
END_MARKERS = [["this", "is"], ["out"], ["more", "to", "follow"]]
# Whisper often hears "follows" as "followed" or "following"
FOLLOWS = [["follows"], ["followed"], ["following"], ["follow"]]


def _parse_cardinal(words: list[str]) -> Optional[int]:
    total, seen = 0, False
    for w in words:
        if w.isdigit():
            total += int(w); seen = True
        elif w in CARDINAL:
            total += CARDINAL[w]; seen = True
    return total if seen else None


def _chars_between(toks: list[Tok], a: int, b: int) -> list[Char]:
    out = []
    for t in toks[a:b]:
        if t.kind == "char":
            out.append(Char(t.value, t.start, t.end, t.prob, [t.alt] if t.alt else []))
    return out


def _longest_run(toks: list[Tok], a: int = 0, b: Optional[int] = None) -> tuple[int, int]:
    b = len(toks) if b is None else b
    best, cur = (a, a), a
    for i in range(a, b + 1):
        if i == b or toks[i].kind != "char":
            if i - cur > best[1] - best[0]:
                best = (cur, i)
            cur = i + 1
    return best


def merge_copies(c1: list[Char], c2: list[Char], expected: Optional[int]) -> list[Char]:
    if not c2:
        return c1
    if not c1:
        return c2
    if len(c1) != len(c2):
        # can't align safely; prefer the copy matching the expected length, then fewer '?'
        def score(c: list[Char]) -> tuple[int, int]:
            return (0 if expected and len(c) == expected else 1, sum(x.c == "?" for x in c))
        return min((c1, c2), key=score)
    out = []
    for a, b in zip(c1, c2):
        if a.c == b.c:
            out.append(Char(a.c, a.t0, a.t1, max(a.p, b.p), sorted(set(a.alts + b.alts))))
        elif a.c == "?":
            out.append(Char(b.c, b.t0, b.t1, b.p, a.alts))
        elif b.c == "?":
            out.append(Char(a.c, a.t0, a.t1, a.p, b.alts))
        else:
            out.append(Char("?", a.t0, a.t1, min(a.p, b.p), sorted({a.c, b.c})))
    return out


def _callsign(seq: list[str], toks: list[Tok]) -> Optional[str]:
    i = _find(seq, ["this", "is"])
    if i < 0:
        return None
    raw: list[str] = []
    for t in toks[i + 2:i + 5]:
        if t.kind != "word" or t.value in _MARKER_WORDS:
            break
        w = t.raw.strip(" ,.").capitalize()
        if w in raw:          # callsigns are said twice: "this is Andrews, Andrews"
            break
        raw.append(w)
        if t.raw.rstrip().endswith((",", ".")):
            break
    return " ".join(raw) or None


def _parse_skyking(toks: list[Tok], seq: list[str]) -> dict:
    out: dict = {"codeword": None, "time": None, "authentication": None}
    i, n = _find_any(seq, [["do", "not", "answer"]])
    j, _ = _find_any(seq, [["time"]], max(i, 0))
    if i >= 0 and j > i:
        words = [t.raw.strip(" ,.") for t in toks[i + n:j] if t.kind == "word"]
        half = words[: max(1, len(words) // 2)] if len(words) >= 2 else words
        out["codeword"] = " ".join(half).upper() or None
    if j >= 0:
        digits = [t.value for t in toks[j + 1:j + 4] if t.kind == "char"][:2]
        out["time"] = "".join(digits) or None
    k, n2 = _find_any(seq, [["authentication"]])
    if k >= 0:
        letters = [t.value for t in toks[k + n2:k + n2 + 3] if t.kind == "char"][:2]
        out["authentication"] = "".join(letters) or None
    return out


def parse(words: list[Word], min_prob: float = 0.45) -> Parsed:
    toks = tokenize(words, min_prob)
    seq = _words_only(toks)
    callsign = _callsign(seq, toks)

    if _find_any(seq, [["skyking"], ["sky", "king"]])[0] >= 0:
        return Parsed(kind="skyking", callsign=callsign, skyking=_parse_skyking(toks, seq))

    # header: char run just before the first "stand by"
    header = ""
    sb, _ = _find_any(seq, STAND_BY)
    if sb > 0:
        a, b = _longest_run(toks, max(0, sb - 12), sb)
        header = "".join(t.value for t in toks[a:b])

    # "message follows" / "message of N characters follows"
    expected = None
    body_start = -1
    m = _find(seq, ["message"])
    while m >= 0:
        f = _find_any(seq, FOLLOWS, m)[0]
        if 0 <= f - m <= 5:
            body_start = f + 1
            if f - m > 1:
                expected = _parse_cardinal(seq[m + 1:f])
            break
        m = _find(seq, ["message"], m + 1)

    if body_start >= 0:
        sa, sal = _find_any(seq, SAY_AGAIN, body_start)
        end1 = sa if sa >= 0 else _find_any(seq, END_MARKERS, body_start)[0]
        end1 = end1 if end1 >= 0 else len(toks)
        c1 = _chars_between(toks, body_start, end1)
        c2: list[Char] = []
        if sa >= 0:
            e2, _ = _find_any(seq, END_MARKERS + [["message"]], sa + sal)
            c2 = _chars_between(toks, sa + sal, e2 if e2 >= 0 else len(toks))
    else:
        a, b = _longest_run(toks)
        if b - a < MIN_EAM_RUN:
            return Parsed(kind="voice", callsign=callsign)
        c1 = _chars_between(toks, a, b)
        c2 = []
        sa, sal = _find_any(seq, SAY_AGAIN, b)
        if sa >= 0:
            a2, b2 = _longest_run(toks, sa + sal)
            c2 = _chars_between(toks, a2, b2)

    exp = expected or DEFAULT_EAM_LEN
    merged = merge_copies(c1, c2, exp)
    return Parsed(
        kind="eam",
        callsign=callsign,
        header=header,
        body="".join(c.c for c in merged),
        copy1="".join(c.c for c in c1),
        copy2="".join(c.c for c in c2),
        expected_len=exp,
        chars=merged,
    )
