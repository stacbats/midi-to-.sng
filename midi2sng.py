"""MIDI -> GoatTracker 2 (.sng, GTS5) converter core.

The SID has three voices, so up to three MIDI sources (track/channel pairs) are
reduced to monophonic lines, quantised to a row grid and written as GoatTracker
patterns/orderlists using a small built-in set of instruments.
"""
from __future__ import annotations

import os
import struct
import subprocess
from dataclasses import dataclass, field

import mido

MAX_PATTERNS = 208
MAX_SONGLEN = 254
MAX_PATTROWS = 127  # leaves room for the end-of-pattern row

FIRSTNOTE, LASTNOTE = 0x60, 0xBC
REST, KEYOFF = 0xBD, 0xBE
ENDPATT = 0xFF
LOOPSONG = 0xFF
CMD_SETTEMPO = 0xF
MIDI_C0 = 12  # MIDI note number of GoatTracker's C-0

# Instrument slots (1-based, as stored in the .sng)
INS_LEAD, INS_BASS, INS_SAW, INS_TRI, INS_KICK, INS_SNARE, INS_HAT, INS_PLUCK = range(1, 9)
INSTRUMENT_NAMES = {
    INS_LEAD: "Pulse lead",
    INS_BASS: "Pulse bass",
    INS_SAW: "Sawtooth",
    INS_TRI: "Triangle",
    INS_KICK: "Kick",
    INS_SNARE: "Snare",
    INS_HAT: "Hi-hat",
    INS_PLUCK: "Pluck",
}


@dataclass
class Note:
    start: int  # ticks
    end: int
    pitch: int
    velocity: int = 100


@dataclass
class Source:
    """One MIDI track/channel pair."""
    track: int
    channel: int
    name: str = ""
    program: int = 0
    notes: list[Note] = field(default_factory=list)

    @property
    def is_drums(self) -> bool:
        return self.channel == 9

    @property
    def avg_pitch(self) -> float:
        return sum(n.pitch for n in self.notes) / len(self.notes) if self.notes else 0

    @property
    def label(self) -> str:
        kind = "Drums" if self.is_drums else f"Prog {self.program}"
        return f"T{self.track} Ch{self.channel + 1} {self.name or kind} ({len(self.notes)} notes)"


@dataclass
class MidiInfo:
    ticks_per_beat: int
    bpm: float
    sources: list[Source]
    length_ticks: int


@dataclass
class Assignment:
    source: Source
    voice: int  # 0..2
    instrument: int
    mode: str = "highest"  # or "lowest"; ignored for drums


@dataclass
class Options:
    rows_per_beat: int = 4
    pattern_rows: int = 64
    tempo: int = 0  # frames per row; 0 = derive from MIDI tempo
    transpose: int = 0
    title: str = ""
    author: str = ""
    copyright: str = ""


def load_midi(path: str) -> MidiInfo:
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat
    bpm = 0.0
    sources: dict[tuple[int, int], Source] = {}
    length = 0
    for ti, track in enumerate(mid.tracks):
        tick = 0
        name = ""
        programs: dict[int, int] = {}
        active: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo" and not bpm:
                bpm = mido.tempo2bpm(msg.tempo)
            elif msg.type == "track_name":
                name = msg.name
            elif msg.type == "program_change":
                programs[msg.channel] = msg.program
                if (ti, msg.channel) in sources:
                    sources[(ti, msg.channel)].program = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                active.setdefault((msg.channel, msg.note), []).append((tick, msg.velocity))
            elif msg.type in ("note_off", "note_on"):
                stack = active.get((msg.channel, msg.note))
                if stack:
                    start, vel = stack.pop(0)
                    src = sources.setdefault(
                        (ti, msg.channel),
                        Source(ti, msg.channel, name, programs.get(msg.channel, 0)),
                    )
                    if not src.name:
                        src.name = name
                    src.notes.append(Note(start, max(tick, start + 1), msg.note, vel))
                    length = max(length, tick)
    result = [s for s in sources.values() if s.notes]
    for s in result:
        s.notes.sort(key=lambda n: (n.start, n.pitch))
    return MidiInfo(tpb, bpm or 120.0, result, length)


def program_to_instrument(program: int) -> int:
    if program < 16 or 24 <= program < 32:
        return INS_PLUCK
    if program < 24:
        return INS_TRI
    if program < 40:
        return INS_BASS
    if program < 72:
        return INS_SAW
    if program < 80:
        return INS_TRI
    return INS_LEAD


def drum_instrument(pitch: int) -> int:
    if pitch in (35, 36, 41, 43, 45, 47, 48, 50):
        return INS_KICK
    if pitch in (37, 38, 39, 40):
        return INS_SNARE
    return INS_HAT


_DRUM_PRIORITY = {INS_KICK: 0, INS_SNARE: 1, INS_HAT: 2}


def auto_assign(info: MidiInfo, max_voices: int = 3) -> list[Assignment]:
    """Pick the most important sources (up to max_voices) and give them sensible voices."""
    max_voices = max(1, min(3, max_voices))
    melodic = [s for s in info.sources if not s.is_drums]
    drums = [s for s in info.sources if s.is_drums]

    def score(s: Source) -> float:
        return len(s.notes) * (1.0 + sum(n.end - n.start for n in s.notes) / max(info.length_ticks, 1))

    chosen: list[Source] = []
    if drums:
        chosen.append(max(drums, key=score))
    ranked = sorted(melodic, key=score, reverse=True)
    chosen += ranked[: max_voices - len(chosen)]

    result = []
    drum = [s for s in chosen if s.is_drums]
    rest = sorted((s for s in chosen if not s.is_drums), key=lambda s: s.avg_pitch)
    # Voice 1 = lead (highest), voice 2 = bass/low, voice 3 = drums or remaining
    ordered = []
    if rest:
        ordered.append(rest[-1])
        rest = rest[:-1]
    ordered += rest + drum
    for voice, s in enumerate(ordered):
        if s.is_drums:
            result.append(Assignment(s, voice, INS_KICK))
        else:
            ins = program_to_instrument(s.program)
            if s.avg_pitch < 50 and ins in (INS_SAW, INS_TRI, INS_PLUCK):
                ins = INS_BASS
            mode = "lowest" if s.avg_pitch < 50 else "highest"
            result.append(Assignment(s, voice, ins, mode))
    return result


def frames_per_row(bpm: float, rows_per_beat: int) -> int:
    """PAL 50 Hz frames per tracker row."""
    return max(3, min(0x7F, round(50 * 60 / bpm / rows_per_beat)))


def _mono_line(a: Assignment, row_ticks: float, transpose: int):
    """Reduce an assignment to non-overlapping (start_row, end_row, note_byte, instr)."""
    cands: dict[int, tuple] = {}
    for n in a.source.notes:
        s = round(n.start / row_ticks)
        e = max(s + 1, round(n.end / row_ticks))
        if a.source.is_drums:
            ins = drum_instrument(n.pitch)
            key = (_DRUM_PRIORITY[ins],)
            note = FIRSTNOTE + 36
            e = s + 1
        else:
            ins = a.instrument
            pitch = n.pitch + transpose
            while pitch < MIDI_C0:
                pitch += 12
            while pitch - MIDI_C0 + FIRSTNOTE > LASTNOTE:
                pitch -= 12
            key = (-pitch,) if a.mode == "highest" else (pitch,)
            note = FIRSTNOTE + pitch - MIDI_C0
        cur = cands.get(s)
        if cur is None or key < cur[0]:
            cands[s] = (key, e, note, ins)
    starts = sorted(cands)
    line = []
    for i, s in enumerate(starts):
        _, e, note, ins = cands[s]
        if i + 1 < len(starts):
            e = min(e, starts[i + 1])
        line.append((s, e, note, ins))
    return line


def _instruments_and_tables():
    wave = [
        (0x41, 0x00), (0xFF, 0x00),                      # 1  pulse
        (0x21, 0x00), (0xFF, 0x00),                      # 3  saw
        (0x11, 0x00), (0xFF, 0x00),                      # 5  triangle
        (0x81, 0xC8), (0x41, 0xA8), (0x41, 0x9C),        # 7  kick
        (0x41, 0x94), (0x41, 0x8C), (0x41, 0x86),
        (0xFF, 0x00),
        (0x81, 0xD0), (0x41, 0xAA), (0x41, 0xA4),        # 14 snare
        (0x80, 0xD4), (0x80, 0xD1), (0xFF, 0x00),
        (0x81, 0xDF), (0x80, 0xDF), (0xFF, 0x00),        # 20 hi-hat
    ]
    pulse = [
        (0x88, 0x00), (0xFF, 0x00),                      # 1  static 50%
        (0x88, 0x00), (0x20, 0x20), (0x20, 0xE0), (0xFF, 0x04),  # 3 modulated
    ]
    speed = [(0x06, 0x03)]                               # 1  vibrato
    # (ad, sr, wave, pulse, filter, speed, vibdelay, name)
    ins = {
        INS_LEAD: (0x0A, 0xA9, 1, 3, 0, 1, 12),
        INS_BASS: (0x09, 0x89, 1, 1, 0, 0, 0),
        INS_SAW: (0x09, 0xA8, 3, 0, 0, 0, 0),
        INS_TRI: (0x08, 0xA8, 5, 0, 0, 0, 0),
        INS_KICK: (0x06, 0x00, 7, 1, 0, 0, 0),
        INS_SNARE: (0x00, 0x00, 14, 1, 0, 0, 0),
        INS_HAT: (0x00, 0x00, 20, 0, 0, 0, 0),
        INS_PLUCK: (0x07, 0x00, 1, 1, 0, 0, 0),
    }
    return ins, [wave, pulse, [], speed]


def _fixed(text: str, size: int) -> bytes:
    return text.encode("latin-1", "replace")[:size].ljust(size, b"\0")


def build_sng(
    assignments: list[Assignment], info: MidiInfo, opt: Options
) -> tuple[bytes, list[str]]:
    warnings: list[str] = []
    bpm = info.bpm
    tempo = opt.tempo or frames_per_row(bpm, opt.rows_per_beat)
    row_ticks = info.ticks_per_beat / opt.rows_per_beat
    plen = max(4, min(MAX_PATTROWS, opt.pattern_rows))

    lines: list[list] = [[], [], []]
    for a in assignments:
        merged = lines[a.voice]
        merged.extend(_mono_line(a, row_ticks, opt.transpose))
    for v in range(3):
        lines[v].sort()
        fixed = []
        for i, (s, e, note, ins) in enumerate(lines[v]):
            if fixed and s == fixed[-1][0]:
                continue
            if fixed and fixed[-1][1] > s:
                fixed[-1] = (fixed[-1][0], s, fixed[-1][2], fixed[-1][3])
            fixed.append((s, e, note, ins))
        lines[v] = fixed

    end_row = max((l[-1][1] for l in lines if l), default=1)
    total = -(-(end_row + 1) // plen) * plen
    channels = []
    for v in range(3):
        rows = [[REST, 0, 0, 0] for _ in range(total)]
        for i, (s, e, note, ins) in enumerate(lines[v]):
            rows[s][0], rows[s][1] = note, ins
            nxt = lines[v][i + 1][0] if i + 1 < len(lines[v]) else None
            if e != nxt and e < total and rows[e][0] == REST:
                rows[e][0] = KEYOFF
        channels.append(rows)
    channels[0][0][2:] = [CMD_SETTEMPO, tempo]

    patterns: list[bytes] = []
    index: dict[bytes, int] = {}
    orders: list[list[int]] = [[], [], []]
    for v in range(3):
        for p in range(0, total, plen):
            body = b"".join(bytes(r) for r in channels[v][p : p + plen]) + bytes([ENDPATT, 0, 0, 0])
            if body not in index:
                index[body] = len(patterns)
                patterns.append(body)
            orders[v].append(index[body])
    if len(patterns) > MAX_PATTERNS:
        warnings.append(f"{len(patterns)} patterns exceed GoatTracker's limit of {MAX_PATTERNS}; truncating.")
        keep = MAX_PATTERNS
        patterns = patterns[:keep]
        orders = [[x for x in o if x < keep] for o in orders]
        n = min(len(o) for o in orders)
        orders = [o[:n] for o in orders]
    if len(orders[0]) > MAX_SONGLEN:
        warnings.append(f"Song needs {len(orders[0])} orderlist steps; truncated to {MAX_SONGLEN}.")
        orders = [o[:MAX_SONGLEN] for o in orders]

    ins, tables = _instruments_and_tables()
    out = bytearray(b"GTS5")
    out += _fixed(opt.title, 32) + _fixed(opt.author, 32) + _fixed(opt.copyright, 32)
    out += bytes([1])
    for v in range(3):
        out += bytes([len(orders[v]) + 1]) + bytes(orders[v]) + bytes([LOOPSONG, 0])
    out += bytes([max(ins)])
    for i in range(1, max(ins) + 1):
        ad, sr, w, p, f, s, vd = ins[i]
        out += bytes([ad, sr, w, p, f, s, vd, 2, 0x09]) + _fixed(INSTRUMENT_NAMES[i], 16)
    for t in tables:
        out += bytes([len(t)]) + bytes(l for l, _ in t) + bytes(r for _, r in t)
    out += bytes([len(patterns)])
    for body in patterns:
        out += bytes([len(body) // 4]) + body
    return bytes(out), warnings


def convert(midi_path: str, sng_path: str, assignments=None, opt: Options | None = None):
    info = load_midi(midi_path)
    opt = opt or Options()
    assignments = assignments if assignments is not None else auto_assign(info)
    if not assignments:
        raise ValueError("No notes found to convert.")
    data, warnings = build_sng(assignments, info, opt)
    with open(sng_path, "wb") as fh:
        fh.write(data)
    return warnings


def export_sid(gt2reloc: str, sng_path: str, sid_path: str) -> str:
    """Run GoatTracker's gt2reloc to turn a .sng into a playable .sid."""
    res = subprocess.run(
        [gt2reloc, sng_path, sid_path],
        capture_output=True, text=True, cwd=os.path.dirname(gt2reloc) or None,
    )
    if res.returncode != 0 or not os.path.exists(sid_path):
        raise RuntimeError((res.stdout + res.stderr).strip() or "gt2reloc failed")
    return res.stdout.strip()


def parse_sng(data: bytes) -> dict:
    """Minimal reader mirroring GoatTracker's loader (used by the tests)."""
    assert data[:4] == b"GTS5"
    pos = 4 + 96
    nsongs = data[pos]; pos += 1
    orders = []
    for _ in range(nsongs):
        for _ in range(3):
            length = data[pos]; pos += 1
            orders.append(list(data[pos : pos + length + 1])); pos += length + 1
    ninstr = data[pos]; pos += 1
    pos += ninstr * 25
    tables = []
    for _ in range(4):
        n = data[pos]; pos += 1
        tables.append((data[pos : pos + n], data[pos + n : pos + 2 * n])); pos += 2 * n
    npat = data[pos]; pos += 1
    pats = []
    for _ in range(npat):
        n = data[pos]; pos += 1
        pats.append([tuple(data[pos + i * 4 : pos + i * 4 + 4]) for i in range(n)]); pos += n * 4
    assert pos == len(data), (pos, len(data))
    return {"orders": orders, "tables": tables, "patterns": pats, "instruments": ninstr}
