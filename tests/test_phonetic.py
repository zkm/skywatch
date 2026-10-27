from app.phonetic import Word, parse, NATO

SPELL = {v: k for k, v in NATO.items() if k not in
         ("alfa", "juliett", "whisky", "tree", "fower", "fife", "niner")}
SPELL["9"] = "niner"


def words(text: str, low: set[int] = frozenset()) -> list[Word]:
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append(Word(w, t, t + 0.4, 0.2 if i in low else 0.95))
        t += 0.5
    return out


def spell(s: str) -> str:
    return " ".join(SPELL[c].capitalize() if c != "X" else "X-ray" for c in s)


BODY = "PQ7RTZ2KLMA9BXC4DEF3GH8JNS5UVW"   # 30 chars
assert len(BODY) == 30


def eam_text(body1: str, body2: str, preamble="Message follows.") -> str:
    return (f"All stations, all stations, this is Andrews, Andrews. "
            f"{spell(body1[:6])}, stand by. {spell(body1[:6])}, stand by. "
            f"{preamble} {spell(body1)} I say again {spell(body2)} "
            f"This is Andrews, out.")


def test_full_eam():
    p = parse(words(eam_text(BODY, BODY)))
    assert p.kind == "eam"
    assert p.body == BODY
    assert p.header == BODY[:6]
    assert p.callsign == "Andrews"
    assert p.expected_len == 30
    assert len(p.chars) == 30 and p.chars[0].t0 < p.chars[-1].t0


def test_merge_fills_unknowns_from_second_copy():
    ws = words(eam_text(BODY, BODY))
    idx = [i for i, w in enumerate(ws) if w.text.strip(",.").lower() == "message"][0]
    ws[idx + 3].prob = 0.1     # 2nd body char in copy 1 is unsure
    p = parse(ws)
    assert p.copy1[1] == "?"
    assert p.body == BODY


def test_disagreement_stays_unknown():
    bad = BODY[:10] + "E" + BODY[11:]
    p = parse(words(eam_text(BODY, bad)))
    assert p.body[10] == "?"
    assert set(p.chars[10].alts) == {BODY[10], "E"}


def test_announced_length():
    body = BODY[:28]
    p = parse(words(eam_text(body, body, "Message of twenty eight characters follows.")))
    assert p.expected_len == 28
    assert p.body == body


def test_misheard_follows():
    # production: "Message of 48 characters Followed" lost the start of the body
    body = BODY[:28]
    p = parse(words(eam_text(body, body, "Message of twenty eight characters followed.")))
    assert p.expected_len == 28
    assert p.body == body


def test_numerals_from_asr_are_split():
    ws = words("Message follows. Alpha 25 Bravo Charlie Delta Echo Foxtrot Golf Hotel "
               "India Juliet Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra "
               "Tango Uniform Victor Whiskey X-ray Yankee Zulu Zero One "
               "This is Offutt, out.")
    p = parse(ws)
    assert p.kind == "eam"
    assert p.body.startswith("A25BC")


def test_garbled_word_mid_run_becomes_unknown():
    ws = words("Message follows. Alpha Bravo Banana Charlie Delta Echo "
               "This is Offutt, out.")
    p = parse(ws)
    assert p.body == "AB?CDE"


def test_fuzzy_spellings():
    p = parse(words("Message follows. Charley Whisky Juliett Alfa Niner This is Offutt, out."))
    assert p.body == "CWJA9"


def test_skyking():
    p = parse(words("Skyking, Skyking, do not answer. Iron Maiden, Iron Maiden. "
                    "Time two five. Authentication Papa Sierra. I say again ... "
                    "This is Offutt, out."))
    assert p.kind == "skyking"
    assert p.skyking == {"codeword": "IRON MAIDEN", "time": "25", "authentication": "PS"}


def test_plain_voice():
    p = parse(words("Andrews, Andrews, this is Reach 123, radio check, over."))
    assert p.kind == "voice"


def test_unstructured_long_run_is_eam():
    p = parse(words(spell(BODY)))
    assert p.kind == "eam" and p.body == BODY
