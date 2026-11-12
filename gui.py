"""Tkinter front end for midi2sng."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import midi2sng as m

VOICES = ["Off", "Voice 1", "Voice 2", "Voice 3"]
MIDI_PROGRAM = "MIDI program"
INSTRS = [MIDI_PROGRAM] + [m.INSTRUMENT_NAMES[i] for i in sorted(m.INSTRUMENT_NAMES)]
CONFIG = os.path.join(os.path.expanduser("~"), ".midi2sng_gt2reloc")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MIDI to GoatTracker / SID")
        self.geometry("820x560")
        self.info = None
        self.rows = []  # (source, voice_var, instr_var, mode_var)

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.midi_var = tk.StringVar()
        ttk.Button(top, text="Open MIDI...", command=self.open_midi).pack(side="left")
        ttk.Label(top, textvariable=self.midi_var).pack(side="left", padx=8)

        self.grid_frame = ttk.LabelFrame(self, text="Sources (max 3 voices)", padding=8)
        self.grid_frame.pack(fill="both", expand=True, padx=8)
        self.nvoices = tk.IntVar(value=3)
        self.voice_combos = []
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=4)
        ttk.Button(bar, text="Auto-pick voices", command=self.auto).pack(side="left")
        ttk.Label(bar, text="Voices:").pack(side="left", padx=(16, 4))
        for n in (2, 3):
            ttk.Radiobutton(bar, text=str(n), value=n, variable=self.nvoices,
                            command=self.auto).pack(side="left")

        opts = ttk.LabelFrame(self, text="Options", padding=8)
        opts.pack(fill="x", padx=8)
        self.rpb = tk.IntVar(value=4)
        self.prows = tk.IntVar(value=64)
        self.tempo = tk.IntVar(value=0)
        self.transpose = tk.IntVar(value=0)
        self.title_var = tk.StringVar()
        self.author_var = tk.StringVar()
        for i, (label, var, lo, hi) in enumerate([
            ("Rows per beat", self.rpb, 1, 8),
            ("Pattern rows", self.prows, 8, 127),
            ("Tempo (0=auto)", self.tempo, 0, 127),
            ("Transpose", self.transpose, -36, 36),
        ]):
            ttk.Label(opts, text=label).grid(row=0, column=i * 2, sticky="e", padx=(8, 2))
            ttk.Spinbox(opts, from_=lo, to=hi, textvariable=var, width=5).grid(row=0, column=i * 2 + 1)
        ttk.Label(opts, text="Title").grid(row=1, column=0, sticky="e", pady=4)
        ttk.Entry(opts, textvariable=self.title_var, width=30).grid(row=1, column=1, columnspan=3, sticky="w")
        ttk.Label(opts, text="Author").grid(row=1, column=4, sticky="e")
        ttk.Entry(opts, textvariable=self.author_var, width=30).grid(row=1, column=5, columnspan=3, sticky="w")

        self.reloc = tk.StringVar()
        if os.path.exists(CONFIG):
            self.reloc.set(open(CONFIG).read().strip())
        sid = ttk.Frame(self, padding=8)
        sid.pack(fill="x")
        ttk.Label(sid, text="gt2reloc.exe (optional, for .sid):").pack(side="left")
        ttk.Entry(sid, textvariable=self.reloc, width=50).pack(side="left", padx=4)
        ttk.Button(sid, text="Browse", command=self.browse_reloc).pack(side="left")

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Save .sng", command=lambda: self.save(False)).pack(side="left")
        ttk.Button(bottom, text="Save .sng + .sid", command=lambda: self.save(True)).pack(side="left", padx=8)

    def open_midi(self):
        path = filedialog.askopenfilename(filetypes=[("MIDI", "*.mid *.midi"), ("All", "*.*")])
        if not path:
            return
        try:
            self.info = m.load_midi(path)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.path = path
        self.midi_var.set(f"{os.path.basename(path)}  -  {self.info.bpm:.1f} BPM")
        self.title_var.set(os.path.splitext(os.path.basename(path))[0][:32])
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.rows = []
        self.voice_combos = []
        for i, s in enumerate(self.info.sources):
            v, ins, mode = tk.StringVar(value="Off"), tk.StringVar(), tk.StringVar(value="highest")
            ttk.Label(self.grid_frame, text=s.label, width=42).grid(row=i, column=0, sticky="w")
            cb = ttk.Combobox(self.grid_frame, textvariable=v, values=VOICES, width=8, state="readonly")
            cb.grid(row=i, column=1)
            self.voice_combos.append(cb)
            ttk.Combobox(self.grid_frame, textvariable=ins, values=INSTRS, width=12, state="readonly").grid(row=i, column=2)
            ttk.Combobox(self.grid_frame, textvariable=mode, values=["highest", "lowest"], width=8, state="readonly").grid(row=i, column=3)
            self.rows.append((s, v, ins, mode))
        self.auto()

    def auto(self):
        if not self.info:
            return
        n = self.nvoices.get()
        for cb in self.voice_combos:
            cb.configure(values=VOICES[: n + 1])
        chosen = {id(a.source): a for a in m.auto_assign(self.info, n)}
        for s, v, ins, mode in self.rows:
            a = chosen.get(id(s))
            v.set(VOICES[a.voice + 1] if a else "Off")
            ins.set(m.INSTRUMENT_NAMES[m.INS_KICK] if s.is_drums else MIDI_PROGRAM)
            mode.set(a.mode if a else "highest")

    def browse_reloc(self):
        p = filedialog.askopenfilename(filetypes=[("gt2reloc", "gt2reloc*.exe gt2reloc*"), ("All", "*.*")])
        if p:
            self.reloc.set(p)
            with open(CONFIG, "w") as fh:
                fh.write(p)

    def assignments(self):
        by_name = {v: k for k, v in m.INSTRUMENT_NAMES.items()}
        out = []
        for s, v, ins, mode in self.rows:
            if v.get() in VOICES[1 : self.nvoices.get() + 1]:
                instrument = None if ins.get() == MIDI_PROGRAM else by_name[ins.get()]
                out.append(m.Assignment(s, VOICES.index(v.get()) - 1, instrument, mode.get()))
        return out

    def save(self, sid):
        if not self.info:
            return messagebox.showinfo("MIDI", "Open a MIDI file first.")
        a = self.assignments()
        if not a:
            return messagebox.showinfo("Voices", "Assign at least one source to a voice.")
        out = filedialog.asksaveasfilename(defaultextension=".sng", initialfile=os.path.splitext(os.path.basename(self.path))[0] + ".sng", filetypes=[("GoatTracker song", "*.sng")])
        if not out:
            return
        opt = m.Options(self.rpb.get(), self.prows.get(), self.tempo.get(), self.transpose.get(),
                        self.title_var.get(), self.author_var.get())
        try:
            data, warnings = m.build_sng(a, self.info, opt)
            with open(out, "wb") as fh:
                fh.write(data)
            msg = f"Saved {out}"
            if sid:
                if not self.reloc.get():
                    raise RuntimeError("Set the path to gt2reloc.exe first.")
                sid_path = os.path.splitext(out)[0] + ".sid"
                m.export_sid(self.reloc.get(), out, sid_path)
                msg += f"\nSaved {sid_path}"
            messagebox.showinfo("Done", msg + ("\n\n" + "\n".join(warnings) if warnings else ""))
        except Exception as exc:
            messagebox.showerror("Error", str(exc))


if __name__ == "__main__":
    App().mainloop()
