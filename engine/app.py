"""Point d'entrée de l'exécutable `LineageExplorer.exe`.

Double-clic → le serveur local démarre (sur un port libre), le navigateur s'ouvre sur
l'interface, et une petite fenêtre permet de fermer l'outil. L'espace de travail est le
dossier de l'exe (par exemple le dossier Teams synchronisé de l'équipe), sauf si un
`config.json` placé à côté de l'exe indique un autre chemin (`paths.workspace`).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import urllib.request
import webbrowser


def _log_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "LineageExplorer")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "lineage_explorer.log")


def main() -> None:
    if getattr(sys, "frozen", False) and sys.stdout is None:          # exe sans console
        sys.stdout = sys.stderr = open(_log_path(), "a", encoding="utf-8", buffering=1)
    from engine.config import load_config
    from engine.server import create_server

    try:
        config = load_config()
        running = _running_instance(config["paths"]["workspace"])
        if running:                                   # déjà ouvert : on rouvre simplement le navigateur
            webbrowser.open(running)
            return
        httpd, api = create_server(config)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _message("Lineage Explorer — erreur au démarrage", f"{exc}\n\nJournal : {_log_path()}")
        return
    url = f"http://localhost:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _write_instance(url, api.workspace.root)
    print(f"Lineage Explorer : {url} — espace : {api.workspace.root}")
    webbrowser.open(url)
    try:
        _window(url, api.workspace.root)
    finally:
        _write_instance(None, None)
        httpd.shutdown()
        httpd.server_close()


def _instance_file() -> str:
    return os.path.join(os.path.dirname(_log_path()), "instance.json")


def _write_instance(url, workspace) -> None:
    try:
        if url is None:
            os.remove(_instance_file())
        else:
            with open(_instance_file(), "w", encoding="utf-8") as handle:
                json.dump({"url": url, "workspace": workspace, "pid": os.getpid()}, handle)
    except OSError:
        pass


def _running_instance(workspace: str):
    """URL d'une instance déjà lancée sur ce poste pour le même espace, sinon None."""
    try:
        with open(_instance_file(), encoding="utf-8") as handle:
            info = json.load(handle)
        if os.path.normcase(os.path.abspath(info["workspace"])) != os.path.normcase(os.path.abspath(workspace)):
            return None
        with urllib.request.urlopen(info["url"] + "/api/revision", timeout=2) as res:
            return info["url"] if res.status == 200 else None
    except (OSError, ValueError, KeyError):
        return None


def _message(title: str, text: str) -> None:
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, text)
        root.destroy()
    except Exception:  # noqa: BLE001
        print(title, text)


def _window(url: str, workspace: str) -> None:
    """Petite fenêtre de contrôle ; la fermer arrête l'outil. Sans tkinter : attente console."""
    try:
        import tkinter
        from tkinter import ttk
    except ImportError:
        input(f"Lineage Explorer est ouvert sur {url}. Entrée pour arrêter…")
        return
    root = tkinter.Tk()
    root.title("Lineage Explorer")
    root.geometry("420x170")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Lineage Explorer est ouvert", font=("Segoe UI", 12, "bold")).pack(anchor="w")
    ttk.Label(frame, text=url, foreground="#2563eb").pack(anchor="w", pady=(2, 0))
    ttk.Label(frame, text=f"Espace partagé : {workspace}", wraplength=390, foreground="#71717a").pack(anchor="w", pady=(6, 10))
    buttons = ttk.Frame(frame)
    buttons.pack(anchor="w")
    ttk.Button(buttons, text="Ouvrir dans le navigateur", command=lambda: webbrowser.open(url)).pack(side="left")
    ttk.Button(buttons, text="Fermer l'outil", command=root.destroy).pack(side="left", padx=8)
    root.mainloop()


if __name__ == "__main__":
    main()
