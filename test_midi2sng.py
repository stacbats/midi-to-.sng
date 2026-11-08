import os
import tempfile

import mido

import midi2sng as m


def make_midi(path):
    mid = mido.MidiFile(ticks_per_beat=480)
    lead, bass, drums = (mido.MidiTrack() for _ in range(3))
    lead.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(125)))
    for ch, tr, notes in [(0, lead, [72, 74, 76, 77] * 8), (1, bass, [36, 36, 43, 43] * 8)]:
        tr.append(mido.Message("program_change", channel=ch, program=80 if ch == 0 else 33))
        for n in notes:
            tr.append(mido.Message("note_on", channel=ch, note=n, velocity=90, time=0))
            tr.append(mido.Message("note_off", channel=ch, note=n, velocity=0, time=420))
            tr.append(mido.Message("note_on", channel=ch, note=n + 4, velocity=90, time=60))  # overlap chord
            tr.append(mido.Message("note_off", channel=ch, note=n + 4, velocity=0, time=0))
    for _ in range(16):
        for n in (36, 42):
            drums.append(mido.Message("note_on", channel=9, note=n, velocity=100, time=0))
            drums.append(mido.Message("note_off", channel=9, note=n, velocity=0, time=240))
    for t in (lead, bass, drums):
        mid.tracks.append(t)
    mid.save(path)


def test_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        mp, sp = os.path.join(d, "t.mid"), os.path.join(d, "t.sng")
        make_midi(mp)
        warnings = m.convert(mp, sp, opt=m.Options(title="Test"))
        assert not warnings
        parsed = m.parse_sng(open(sp, "rb").read())
        assert parsed["instruments"] == 8
        assert len(parsed["orders"]) == 3
        assert len({len(o) for o in parsed["orders"]}) == 1
        for o in parsed["orders"]:
            assert o[-2:] == [0xFF, 0]
            assert all(x < len(parsed["patterns"]) for x in o[:-2])
        first = parsed["patterns"][parsed["orders"][0][0]]
        assert first[0][2:] == (0xF, 6)  # 125 BPM, 4 rows/beat -> 6 frames
        assert first[-1][0] == 0xFF
        assert first[0][0] == m.FIRSTNOTE + 72 - 12 or first[0][0] == m.FIRSTNOTE + 76 - 12


def test_assignment():
    with tempfile.TemporaryDirectory() as d:
        mp = os.path.join(d, "t.mid")
        make_midi(mp)
        info = m.load_midi(mp)
        a = m.auto_assign(info)
        assert len(a) == 3 and sorted(x.voice for x in a) == [0, 1, 2]
        assert next(x for x in a if x.source.is_drums).instrument == m.INS_KICK


if __name__ == "__main__":
    test_roundtrip()
    test_assignment()
    print("ok")
