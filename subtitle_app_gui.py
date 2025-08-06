#!/usr/bin/env python3
"""
Subtitle Translator GUI Application

Features:
 1. Downloads two videos (MP4, selectable quality)
 2. Auto-detects source subtitle language from filename (bracketed tags or suffix)
 3. Extracts subtitles via VSE CLI in a dedicated venv
 4. Translates subtitles via OpenAI ChatGPT API
 5. Burns subtitles into second video via ffmpeg
 6. Cleans up intermediate files

Usage:
    python subtitle_app_gui.py
"""
import os
import sys
import json
import threading
import subprocess
import re
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

CONFIG_FILE = os.path.join(os.path.expanduser('~'), '.subtitle_app_config.json')
QUALITIES = ['1080', '720', '480', '360', '240']
KNOWN_LANGS = {
    'en': 'en', 'eng': 'en',
    'es': 'es', 'esp': 'es', 'español': 'es',
    'ru': 'ru',
    'ja': 'ja', 'jpn': 'ja',
    'zh': 'zh',
    'ko': 'ko', 'kor': 'ko',
}


class SubtitleApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Subtitle Translator')
        self.resizable(False, False)
        self._build_gui()
        self._load_config()

    def _build_gui(self):
        pad = {'padx': 5, 'pady': 3}
        fields = [
            ('VSE Folder', 'vse_dir', True),
            ('Video with subs URL', 'subs_url', False),
            ('Clean video URL', 'clean_url', False),
            ('Subtitle area (y1 x1 y2 x2)', 'area', False),
            ('OpenAI API Key', 'api_key', False),
            ('Target lang', 'lang', False),
            ('Quality', 'quality', False),
            ('FFmpeg path', 'ffmpeg', False),
            ('Output dir', 'outdir', True),
        ]
        self.vars = {}
        for i, (label, key, browse) in enumerate(fields):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky='w', **pad)
            if key == 'quality':
                w = ttk.Combobox(self, values=QUALITIES, state='readonly', width=8)
                w.current(0)
            else:
                show = '*' if key == 'api_key' else ''
                w = ttk.Entry(self, width=50, show=show)
            w.grid(row=i, column=1, **pad)
            self.vars[key] = w
            if browse:
                ttk.Button(self, text='Browse', command=lambda k=key: self._browse(k)).grid(row=i, column=2, **pad)
        ttk.Button(self, text='Start', command=self._start).grid(row=len(fields), column=0, **pad)
        ttk.Button(self, text='Save', command=self._save).grid(row=len(fields), column=1, **pad)
        self.status = tk.StringVar(self, value='Ready')
        ttk.Label(self, textvariable=self.status).grid(row=len(fields)+1, column=0, columnspan=3, sticky='we', **pad)

    def _browse(self, key):
        if key in ('vse_dir', 'outdir'):
            p = filedialog.askdirectory()
        else:
            p = filedialog.askopenfilename()
        if p:
            e = self.vars[key]
            e.delete(0, tk.END)
            e.insert(0, p)

    def _load_config(self):
        try:
            data = json.load(open(CONFIG_FILE, 'r', encoding='utf-8'))
            for k, v in data.items():
                if k in self.vars:
                    e = self.vars[k]
                    e.delete(0, tk.END)
                    e.insert(0, v)
        except:
            pass

    def _save(self):
        data = {k: self.vars[k].get() for k in self.vars}
        json.dump(data, open(CONFIG_FILE, 'w', encoding='utf-8'), indent=2)
        messagebox.showinfo('Saved', 'Settings saved')

    def _start(self):
        self._update('Running...')
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            v = self.vars
            out = v['outdir'].get().strip()
            vse_dir = v['vse_dir'].get().strip()
            os.makedirs(out, exist_ok=True)

            # 1) Setup venv and dependencies automatically
            self._update('Setting up environment')
            venv_path = os.path.join(vse_dir, 'videoEnv')
            python_venv = os.path.join(venv_path, 'Scripts', 'python.exe')
            if not os.path.isdir(venv_path):
                subprocess.run([sys.executable, '-m', 'venv', venv_path], check=True)
                for req in ('requirements.txt', 'requirements_directml.txt'):
                    f = os.path.join(vse_dir, req)
                    if os.path.isfile(f):
                        subprocess.run([python_venv, '-m', 'pip', 'install', '-r', f], check=True)
                subprocess.run([python_venv, '-m', 'pip', 'install', 'yt-dlp'], check=True)

            # 2) Download videos
            self._update('Downloading videos')
            vid1 = os.path.join(out, 'video_subs.mp4')
            vid2 = os.path.join(out, 'video_clean.mp4')
            for url, path in ((v['subs_url'].get(), vid1), (v['clean_url'].get(), vid2)):
                subprocess.run([
                    python_venv, '-m', 'yt_dlp',
                    '-f', f"bestvideo[height<={v['quality'].get()}]+bestaudio/best",
                    '--merge-output-format', 'mp4',
                    '-o', path, url
                ], check=True)

            # 3) Detect language
            self._update('Detecting subtitle language')
            name = os.path.basename(urllib.parse.urlparse(v['subs_url'].get()).path)
            src = self._detect_lang(name)
            v['lang'].delete(0, tk.END)
            v['lang'].insert(0, src)

            # 4) Extract subtitles
            self._update('Extracting subtitles')
            # Write settings.ini for subtitle language and mode
            cfg_path = os.path.join(vse_dir, 'settings.ini')
            lang_code = v['lang'].get().strip()
            with open(cfg_path, 'w', encoding='utf-8') as cfg:
                cfg.write(
                    "[DEFAULT]\n"
                    "Interface = English\n"
                    f"Language = {lang_code}\n"
                    "Mode = fast\n"
                )
            # Launch VSE CLI and feed inputs automatically
            proc = subprocess.Popen(
                [python_venv, '-m', 'backend.main'],
                cwd=vse_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            area_input = v['area'].get().strip()
            prompt_data = f"{vid1}\n{area_input}\n"
            outp, errp = proc.communicate(prompt_data)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Extractor failed. stdout: {outp}\nstderr: {errp}"
                )
            orig = os.path.join(vse_dir, 'output.srt')

            # 5) Translate
            self._update('Translating subtitles')
            trans = os.path.join(out, 'translated_subs.srt')
            self._translate(orig, trans, v['api_key'].get(), v['lang'].get())

            # 6) Burn subtitles
            self._update('Burning subtitles')
            final = os.path.join(out, 'final.mp4')
            ffmpeg_cmd = v['ffmpeg'].get().strip() or 'ffmpeg'
            subprocess.run([
                ffmpeg_cmd,
                '-i', vid2,
                '-vf', f"subtitles={trans}",
                '-c:a', 'copy',
                final
            ], check=True)

            # 7) Cleanup
            self._update('Cleaning up')
            for fpath in (vid1, vid2, orig, trans):
                try:
                    os.remove(fpath)
                except:
                    pass

            self._update('Done')
        except Exception as e:
            messagebox.showerror('Error', str(e))
            self._update('Error')

    def _update(self, msg):
        self.status.set(msg)
        self.update_idletasks()

    def _detect_lang(self, filename):
        base = filename.rsplit('.', 1)[0]
        m = re.search(r"\[(?:Sub\s*)?([A-Za-zÑñ]+)\]", base)
        if m:
            c = m.group(1).lower()
            return KNOWN_LANGS.get(c, c[:2])
        m = re.search(r"[._\-/]([A-Za-z]{2,5})$", base)
        if m:
            c = m.group(1).lower()
            return KNOWN_LANGS.get(c, c[:2])
        return 'auto'

    def _translate(self, ins, outs, key, lang):
        import openai
        openai.api_key = key
        text = open(ins, encoding='utf-8').read()
        msgs = [
            {'role': 'system', 'content': f"Translate to {lang}, preserve SRT."},
            {'role': 'user', 'content': text}
        ]
        resp = openai.ChatCompletion.create(
            model='gpt-3.5-turbo', messages=msgs, temperature=0
        )
        with open(outs, 'w', encoding='utf-8') as f:
            f.write(resp.choices[0].message.content)


if __name__ == '__main__':
    SubtitleApp().mainloop()
