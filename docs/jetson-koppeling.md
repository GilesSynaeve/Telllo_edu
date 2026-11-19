# Jetson-koppeling

De Jetson stuurt de drone niet rechtstreeks aan. Hij praat met de **laptop**, die aan de Wi-Fi van
de Tello hangt en de drone bestuurt (`tello_gui.py`). De Jetson stuurt opdrachten (een missie, pad
volgen, landen, …) en krijgt de positie van de drone en de **coördinaten van elke gevonden plas**
terug. Alles is JSON over UDP, één bericht per pakket. De volledige lijst van berichten staat in
de README onder [Protocol](../README.md#protocol-json-over-udp-één-bericht-per-pakket).

```
 Jetson  ──(UDP 9000: mission / follow_path / abort / ...)──▶  Laptop  ──(Wi-Fi)──▶  Tello EDU
 Jetson  ◀──(UDP 9001: status / puddle / mission_done ...)──  Laptop  ◀──(video onderste camera)──
```

## 1. Netwerk

De laptop zit op de Wi-Fi van de Tello en kan daar niet tegelijk op een ander Wi-Fi-netwerk. De
Jetson moet de laptop dus via een **tweede verbinding** bereiken:

* een **ethernetkabel** tussen Jetson en laptop (of beide aan dezelfde router), of
* een **tweede Wi-Fi-adapter** (USB) in de laptop.

Een alternatief: de Tello EDU in *station mode* aan een router hangen (eenmalig `ap <ssid>
<wachtwoord>` sturen). Dan zitten Tello, laptop en Jetson op hetzelfde netwerk. De code maakt de
drone nu aan met `Tello()` (standaardadres van de Tello in zijn eigen Wi-Fi); in station mode moet
dat `Tello(host="<ip van de tello>")` worden.

Test eerst of de Jetson de laptop ziet: `ping <ip van de laptop>` op de Jetson.

## 2. Instellingen op de laptop

In `drone/config.py`:

```python
LISTEN_HOST = "0.0.0.0"          # standaard "127.0.0.1": dan hoort de laptop alleen zichzelf
LISTEN_PORT = 9000               # hier stuurt de Jetson naartoe
JETSON_HOST = "192.168.x.y"      # IP van de Jetson: alleen die mag commando's sturen
JETSON_PORT = 9001               # hier luistert de Jetson
STATUS_INTERVAL = 1.0            # elke seconde een status-bericht
```

* Met `JETSON_HOST = None` neemt de laptop alleen commando's aan van zichzelf (`127.0.0.1`,
  bv. `fake_jetson.py`), zodat niemand anders op het netwerk de drone kan laten opstijgen. Een
  echte Jetson wordt dan genegeerd (log: *Bericht van … genegeerd*): zet `JETSON_HOST` dus op het
  IP van de Jetson zodra je `LISTEN_HOST = "0.0.0.0"` gebruikt.
* Laat op Windows de firewall **inkomend UDP 9000** toe voor Python.
* Berichten gaan pas naar de Jetson als de laptop weet waar die zit: na het eerste commando van de
  Jetson, of meteen als `JETSON_HOST` is ingesteld. Stuur dus eerst een `ping`.

In de bovenbalk van de GUI staat **Jetson ● verbonden** zodra er berichten van de Jetson binnenkomen.
Missies van de Jetson worden gewoon uitgevoerd en in de GUI getekend.

## 3. Assenstelsel

Alle coördinaten zijn in **cm** in het *mission frame* van de vlucht:

* **(0, 0)** = waar de drone stond bij het opstijgen,
* **x** = vooruit (de kant van de neus/voorcamera bij de start), **y** = links, **z** = hoogte.

Bij elke start vanaf de grond wordt dit opnieuw gezet. Een plas uit vlucht 2 heeft dus coördinaten
t.o.v. de startplek van vlucht 2.

Werkt de Jetson met eigen wereldcoördinaten (meter), dan rekent hij die om. Staat de drone op
wereldpositie `(X0, Y0)` met zijn neus in richting `a` (graden), dan is voor een wereldpunt
`(X, Y, Z)`:

```
x = 100 * ( (X-X0)*cos(a) + (Y-Y0)*sin(a) )
y = 100 * (-(X-X0)*sin(a) + (Y-Y0)*cos(a) )
z = 100 * Z
```

En terug, voor een plas `(x, y)` in cm:

```
X = X0 + (x*cos(a) - y*sin(a)) / 100
Y = Y0 + (x*sin(a) + y*cos(a)) / 100
```

In plaats daarvan kan de Jetson vóór het opstijgen `{"type": "set_pose", "x": .., "y": .., "yaw": ..}`
sturen (cm, graden, links = positief). Dan werkt de drone in het stelsel van de Jetson, en geldt die
positie voor de eerstvolgende start.

## 4. Testscripts op de Jetson

Alleen de Python-standaardbibliotheek is nodig.

**Luisteren** (`luister.py`): toont alles wat de drone stuurt, behalve de status van elke seconde.

```python
import json, socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 9001))            # drone → Jetson
while True:
    data, addr = sock.recvfrom(65535)
    msg = json.loads(data)
    if msg["type"] in ("puddle", "puddle_photo"):
        print(f"PLAS {msg['id']}: x={msg['x']:.0f} cm, y={msg['y']:.0f} cm, "
              f"zekerheid {msg['confidence']:.2f}", msg.get("photo", ""))
    elif msg["type"] != "status":
        print(msg)
```

**Opdracht sturen** (`stuur.py`): een missie uit een JSON-bestand, of pad volgen.

```python
import json, socket, sys

LAPTOP = ("192.168.x.z", 9000)          # IP van de laptop
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def stuur(msg):
    sock.sendto(json.dumps(msg).encode(), LAPTOP)

stuur({"type": "ping", "t": 0})          # laptop leert waar de Jetson zit
if len(sys.argv) > 1 and sys.argv[1] == "pad":
    stuur({"type": "follow_path", "id": "jetson-pad", "land_at_end": True})
else:
    stuur(json.load(open("mission_example.json")))   # uit json_flights/
```

Start eerst `luister.py`, dan de GUI op de laptop, dan `python stuur.py` (route) of
`python stuur.py pad` (strook op de vloer volgen). Je ziet dan:

1. `pong` en een `ack` voor de opdracht,
2. bij een route: `waypoint_reached` per punt,
3. één `puddle`-bericht per plas, met `x`, `y` in cm,
4. een `puddle_photo`-bericht met de bestandsnaam van de foto (op de laptop, in `photos/`),
5. `mission_done` (route) of `path_done` (pad volgen), met de lijst van alle plassen en waar de H
   gezien is.

Afbreken kan altijd met `{"type": "abort"}` of `{"type": "land"}`.

## 5. Zonder drone testen

Alles hierboven werkt ook met de simulator: start op de laptop
`python tello_gui.py --sim --scene sim_scenes/pad_met_plassen.json` en stuur vanaf de Jetson
`python stuur.py pad`. De gesimuleerde drone volgt de tape, vindt de drie plassen en stuurt ze naar
de Jetson. Op één computer kan het ook: laat `LISTEN_HOST` op `127.0.0.1` en gebruik
`LAPTOP = ("127.0.0.1", 9000)`, of gebruik `fake_jetson.py` als nep-Jetson:

```bash
python fake_jetson.py listen                                # toon elk bericht van de laptop
python fake_jetson.py ping                                  # één ping, wacht op de pong
python fake_jetson.py mission json_flights/mission_example.json
python fake_jetson.py abort                                 # Abort = landen
```

## Problemen

| probleem | oplossing |
|---|---|
| Jetson krijgt niets | `LISTEN_HOST = "0.0.0.0"`, firewall (UDP 9000 in), Jetson luistert op 9001, eerst een `ping` sturen. |
| Laptop negeert de Jetson | `JETSON_HOST` staat op een ander IP (log: *Bericht van … genegeerd*). |
| `error` als antwoord | Het veld `error` zegt waarom: waypoint buiten de geofence, `set_pose` in de lucht, ongeldige snelheid, onbekend type. |
| Coördinaten kloppen niet met de echte wereld | Startpositie en -richting van de drone gecontroleerd? Elke start is een nieuw (0, 0). Kalibratie: zie README. |
