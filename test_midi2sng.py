import os
import tempfile

import mido

import gui
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


def test_program_changes():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "programs.mid")
        mid = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        track.extend([
            mido.Message("note_on", note=60, velocity=90),
            mido.Message("program_change", program=40, time=60),
            mido.Message("note_off", note=60, time=60),
            mido.Message("note_on", note=62, velocity=90),
            mido.Message("program_change", program=80, time=60),
            mido.Message("note_off", note=62, time=60),
            mido.Message("note_on", note=64, velocity=90),
            mido.Message("note_off", note=64, time=120),
        ])
        mid.save(path)
        info = m.load_midi(path)
        source = info.sources[0]
        assert [n.program for n in source.notes] == [0, 40, 80]
        data, _ = m.build_sng(m.auto_assign(info), info, m.Options())
        song = m.parse_sng(data)
        pattern = song["patterns"][song["orders"][0][0]]
        assert [pattern[i][1] for i in range(3)] == [m.INS_PLUCK, m.INS_SAW, m.INS_LEAD]
        fixed, _ = m.build_sng([m.Assignment(source, 0, m.INS_TRI)], info, m.Options())
        song = m.parse_sng(fixed)
        pattern = song["patterns"][song["orders"][0][0]]
        assert [pattern[i][1] for i in range(3)] == [m.INS_TRI] * 3


def test_programs_across_tracks_and_channels():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "channels.mid")
        mid = mido.MidiFile(type=1, ticks_per_beat=480)
        control = mido.MidiTrack([
            mido.Message("program_change", channel=0, program=40),
            mido.Message("program_change", channel=0, program=80, time=120),
        ])
        notes = mido.MidiTrack([
            mido.Message("note_on", channel=0, note=60, velocity=90),
            mido.Message("note_on", channel=1, note=65, velocity=90),
            mido.Message("note_off", channel=0, note=60, time=120),
            mido.Message("note_off", channel=1, note=65),
            mido.Message("note_on", channel=0, note=62, velocity=90),
            mido.Message("note_off", channel=0, note=62, time=120),
        ])
        mid.tracks.extend([control, notes])
        mid.save(path)
        sources = {s.channel: s for s in m.load_midi(path).sources}
        assert [n.program for n in sources[0].notes] == [40, 80]
        assert [n.program for n in sources[1].notes] == [0]
        assert sources[0].program == 40

        mid.type = 2
        mid.save(path)
        sources = {s.channel: s for s in m.load_midi(path).sources}
        assert [n.program for n in sources[0].notes] == [0, 0]


def test_gui_instrument_selection():
    class Value:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    source = m.Source(0, 0, program=40, notes=[m.Note(0, 120, 60)])
    app = object.__new__(gui.App)
    app.nvoices = Value(2)
    app.rows = [(source, Value("Voice 1"), Value(gui.MIDI_PROGRAM), Value("highest"))]
    assignment = gui.App.assignments(app)[0]
    assert assignment.instrument is None
    assert m._mono_line(assignment, 120, 0)[0][3] == m.INS_SAW
    app.rows = [(source, Value("Voice 1"), Value("Triangle"), Value("highest"))]
    assert gui.App.assignments(app)[0].instrument == m.INS_TRI


def test_program_mapping_and_drums():
    source = m.Source(0, 0, notes=[
        m.Note(0, 120, 36, program=0),
        m.Note(120, 240, 38, program=40),
    ])
    info = m.MidiInfo(480, 120, [source], 240)
    assignment = m.auto_assign(info)[0]
    assert assignment.mode == "lowest"
    assert [row[3] for row in m._mono_line(assignment, 120, 0)] == [m.INS_PLUCK, m.INS_SAW]

    drums = m.Source(1, 9, notes=[
        m.Note(0, 120, 36, program=80),
        m.Note(120, 240, 38, program=0),
    ])
    assert [row[3] for row in m._mono_line(m.Assignment(drums, 0, m.INS_TRI), 120, 0)] == [
        m.INS_KICK, m.INS_SNARE
    ]


if __name__ == "__main__":
    test_roundtrip()
    test_assignment()
    test_program_changes()
    test_programs_across_tracks_and_channels()
    test_gui_instrument_selection()
    test_program_mapping_and_drums()
    print("ok")
