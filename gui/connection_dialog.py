"""Pre-flight network selector. Passwords live only in the masked field/worker."""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from drone import config as cfg
from drone.network import (ConnectionOptions, NetworkSetupError, discover_drones,
                           provision_sta, suggested_subnet, validate_credentials)


AP_RESET_HELP = ("Retour au mode AP : drone posé, moteurs arrêtés, allumer le drone puis maintenir "
                 "son bouton d'alimentation environ 5 secondes pour réinitialiser le Wi-Fi. "
                 "Attendre le redémarrage, reconnecter le PC au réseau TELLO-… puis choisir AP. "
                 "Les anciens identifiants Wi-Fi du drone sont réinitialisés.")


class ConnectionDialog:
    def __init__(self, parent):
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title("Connexion Tello EDU — AP / STA")
        self.top.protocol("WM_DELETE_WINDOW", self.cancel)
        self.mode = tk.StringVar(self.top, cfg.TELLO_CONNECTION_MODE)
        self.host = tk.StringVar(self.top)
        self.ssid = tk.StringVar(self.top)
        self.password = tk.StringVar(self.top)
        self._auto_subnet = suggested_subnet()
        self.subnet = tk.StringVar(self.top, self._auto_subnet)
        self.probe_video = tk.BooleanVar(self.top, False)
        self.status = tk.StringVar(self.top)
        self._events = queue.Queue()
        self._busy = False
        self._reboot_until = 0.0
        body = ttk.Frame(self.top, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Choisir le réseau du drone", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        for title, value in (("Connexion directe (AP) — Wi-Fi TELLO", "ap"),
                             ("Wi-Fi domestique (STA) — PC et drone sur le même réseau", "sta")):
            ttk.Radiobutton(body, text=title, value=value, variable=self.mode,
                            command=self.mode_changed).pack(anchor="w", pady=3)
        row = ttk.Frame(body)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="IPv4 du drone :").pack(side="left")
        ttk.Entry(row, textvariable=self.host, width=20).pack(side="left", padx=8)
        ttk.Button(row, text="Retour AP / aide", command=self.return_ap).pack(side="right")

        self.sta = ttk.LabelFrame(body, text="Préparer STA depuis le Wi-Fi AP du drone", padding=10)
        self.sta.pack(fill="x", pady=8)
        ttk.Label(self.sta, text="Drone posé, moteurs arrêtés. Fermer les autres applications Tello.\n"
                  "Pour configurer : PC sur TELLO. Pour reconnecter ensuite : PC sur le Wi-Fi domestique.",
                  wraplength=600).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(self.sta, text="SSID domestique :").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(self.sta, textvariable=self.ssid, width=34).grid(row=1, column=1, sticky="ew")
        ttk.Label(self.sta, text="Mot de passe :").grid(row=2, column=0, sticky="w")
        ttk.Entry(self.sta, textvariable=self.password, show="●", width=34).grid(row=2, column=1, sticky="ew")
        self.configure_btn = ttk.Button(self.sta, text="Envoyer la configuration STA (redémarre le drone)",
                                        command=self.configure_sta)
        self.configure_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(self.sta, text="Mot de passe non enregistré. SDK : SSID et mot de passe sans espaces.\n"
                  "Utiliser le réseau 2,4 GHz du routeur ; éviter l'isolation des clients / réseau invité.",
                  wraplength=600).grid(row=4, column=0, columnspan=2, sticky="w")
        self.sta.columnconfigure(1, weight=1)

        row = ttk.Frame(body)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="Réseau local à rechercher :").pack(side="left")
        ttk.Entry(row, textvariable=self.subnet, width=22).pack(side="left", padx=8)
        self.discover_btn = ttk.Button(row, text="Détecter le drone", command=self.discover)
        self.discover_btn.pack(side="right")
        self.devices = ttk.Combobox(body, state="readonly", values=[])
        self.devices.pack(fill="x")
        self.devices.bind("<<ComboboxSelected>>", self.select_device)
        ttk.Checkbutton(body, text="Essayer la vidéo STA (selon firmware ; images réelles exigées)",
                        variable=self.probe_video).pack(anchor="w", pady=(10, 3))
        ttk.Label(body, text="En STA, la vidéo n'est pas garantie. Sans caméra inférieure confirmée :\n"
                  "missions, odométrie visuelle, saltos avec retour et atterrissage H désactivés.",
                  wraplength=620).pack(anchor="w")
        ttk.Label(body, textvariable=self.status, wraplength=620, foreground="#9b5200").pack(fill="x", pady=12)
        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Button(row, text="Annuler", command=self.cancel).pack(side="right")
        self.connect_btn = ttk.Button(row, text="Connecter", command=self.connect)
        self.connect_btn.pack(side="right", padx=8)
        self.mode_changed()
        self.top.grab_set()
        self._poll_id = self.top.after(100, self.poll)

    def mode_changed(self):
        direct = self.mode.get() == "ap"
        self.host.set(cfg.TELLO_AP_HOST if direct else cfg.TELLO_STA_HOST)
        self.status.set("AP : connecter le PC au Wi-Fi TELLO du drone." if direct else
                        "STA : saisir l'IP attribuée par le routeur ou utiliser la détection après reconnexion du PC.")

    def return_ap(self):
        self.mode.set("ap")
        self.mode_changed()
        self.status.set(AP_RESET_HELP)

    def _start(self, kind, work):
        if self._busy:
            return
        self._busy = True
        self.refresh_buttons()

        def run():
            try:
                self._events.put((kind, work(), None))
            except NetworkSetupError as exc:
                self._events.put((kind, None, str(exc)))
            except Exception:
                # Do not format an unexpected exception that might echo credentials.
                self._events.put((kind, None, "Opération réseau interrompue. Vérifier le Wi-Fi et fermer les autres applications Tello."))

        threading.Thread(target=run, daemon=True).start()

    def configure_sta(self):
        if self._busy:
            return
        ssid, password = self.ssid.get(), self.password.get()
        self.password.set("")
        try:
            validate_credentials(ssid, password)
        except NetworkSetupError as exc:
            self.status.set(str(exc))
            return
        self.status.set("Envoi depuis le réseau AP… Le drone redémarrera si la configuration est acceptée.")
        self._start("configure", lambda: provision_sta(ssid, password))

    def discover(self):
        if self.mode.get() != "sta":
            self.status.set("La détection locale sert au mode STA. Sélectionner STA et rejoindre le réseau domestique.")
            return
        cidr = self.subnet.get()
        # The PC may have switched from TELLO to the home router since opening
        # this dialog. Refresh the route unless the user edited the subnet.
        if cidr == self._auto_subnet:
            cidr = self._auto_subnet = suggested_subnet()
            self.subnet.set(cidr)
        self.status.set("Recherche locale limitée : commandes SDK sans mouvement. Patienter…")
        self._start("discover", lambda: discover_drones(cidr))

    def select_device(self, _event=None):
        if self.devices.get():
            self.host.set(self.devices.get().split()[0])

    def refresh_buttons(self):
        waiting = time.monotonic() < self._reboot_until
        state = "disabled" if self._busy or waiting else "normal"
        for button in (self.configure_btn, self.discover_btn, self.connect_btn):
            button.configure(state=state)

    def poll(self):
        try:
            kind, value, error = self._events.get_nowait()
            self._busy = False
            if error:
                self.status.set(error)
            elif kind == "configure":
                self.mode.set("sta")
                self.host.set(cfg.TELLO_STA_HOST)
                self._reboot_until = time.monotonic() + cfg.WIFI_REBOOT_WAIT_S
                self.status.set("Configuration acceptée. " if value else
                                "Commande envoyée, mais accusé absent : résultat non confirmé. ")
                self._setup_result = self.status.get()
            elif kind == "discover":
                self.devices.configure(values=[f"{host}  (SDK {sdk})" for host, sdk in value])
                self.devices.set("")
                if len(value) == 1:
                    self.devices.current(0)
                    self.select_device()
                self.status.set(f"{len(value)} drone(s) détecté(s). Choisir l'adresse avant de connecter." if value else
                                "Aucun drone détecté. Vérifier l'IP DHCP du routeur, le sous-réseau, "
                                "le pare-feu UDP 8889/8890 et l'absence d'isolation des clients.")
        except queue.Empty:
            pass
        remaining = max(0, int(self._reboot_until - time.monotonic() + 0.99))
        if self._reboot_until:
            self.status.set(self._setup_result + (f" Attente du redémarrage : {remaining} s." if remaining else
                            " Rejoindre le Wi-Fi domestique sur le PC, puis saisir ou détecter l'IP du drone. "
                            "Si le drone ne rejoint pas le réseau, utiliser Retour AP / aide."))
            if not remaining:
                self._reboot_until = 0.0
        self.refresh_buttons()
        self._poll_id = self.top.after(100, self.poll)

    def connect(self):
        try:
            self.result = ConnectionOptions(self.mode.get(), self.host.get(), self.probe_video.get())
        except NetworkSetupError as exc:
            self.status.set(str(exc))
            return
        self.password.set("")
        self.close()

    def cancel(self):
        self.password.set("")
        self.result = None
        self.close()

    def close(self):
        if getattr(self, "_poll_id", None):
            self.top.after_cancel(self._poll_id)
        self.top.destroy()


def choose_connection(parent):
    dialog = ConnectionDialog(parent)
    parent.wait_window(dialog.top)
    return dialog.result
