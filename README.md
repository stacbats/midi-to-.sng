# MIDI to GoatTracker / SID

Converts a MIDI file to a GoatTracker 2 `.sng` (GTS5) song, optionally to `.sid`.

    pip install mido
    python gui.py

1. Open a MIDI file; the three most important tracks are auto-assigned to SID voices (drums = MIDI channel 10).
2. Adjust voice, instrument and mono mode (highest/lowest note of chords) per track.
   The default **MIDI program** instrument follows MIDI program changes per note;
   choose a named preset to override them with one fixed instrument.
3. Save `.sng` (open in GoatTracker, then export `.sid` with its packer), or set the path to `gt2reloc.exe`
   from your GoatTracker folder to write the `.sid` directly.

Notes: tempo is taken from the first MIDI tempo event (frames per row at 50 Hz; override with *Tempo*);
notes are quantised to the *Rows per beat* grid; each voice is monophonic. Built-in instruments are
simple pulse/saw/triangle/drum presets with their wave/pulse tables, ready to be tweaked in GoatTracker.
MIDI programs are approximated by these presets; different programs can map to the same preset.
Program changes affect notes starting after the change, not notes already playing. Drum sounds are
selected by MIDI note number regardless of the instrument selector. Low-pitched melodic sources now
use their MIDI program's preset rather than automatically forcing a bass preset.

Tests: `python test_midi2sng.py`
