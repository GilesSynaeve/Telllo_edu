# Tutorial: het plasmodel trainen en testen op de drone

Voor wie nieuw is in het project. Je leert hoe je een dataset van plassen opbouwt, er een
YOLO-objectdetectiemodel mee traint en dat model daarna test: eerst los op beelden, dan op de
Tello EDU met de Jetson erbij.

Gebaseerd op de repo [GilesS420/Telllo_edu](https://github.com/GilesS420/Telllo_edu) (README,
`drone/puddles.py`, `drone/config.py`). Waarom we voor een getraind YOLO-model kozen staat in
[plasdetectie-ontwerpkeuzes.md](plasdetectie-ontwerpkeuzes.md).

> **Het model dat nu gebruikt wordt** is de run `train-6`: YOLO11n, `imgsz=320`, op de testset
> **mAP50 0.719** en **recall 0.661**. Het staat als `models/puddles.pt` op de laptop
> (`PUDDLE_IMGSZ = 320` in `drone/config.py`). Zie [Stap B4](#stap-b4-runs-vergelijken-en-een-model-kiezen).

---

## 0. Het grote plaatje

```
 Tello EDU ──beeld onderste camera──▶ Laptop (tello_gui.py)
                                        │  YOLO-model models/puddles.pt
                                        │  kader → coördinaat (cm) via hoogte + positie
                                        ▼
                                      Jetson  ◀── UDP 9001: "puddle"-berichten
```

* Het **model draait op de laptop**, niet op de Jetson. De laptop stuurt per gevonden plas één
  `puddle`-bericht met `x`, `y` in cm naar de Jetson.
* Het model ziet **grijze, bijgesneden beelden van de onderste camera**, van recht boven. Een
  kant-en-klaar plasmodel (straatbeelden, in kleur, schuin) werkt daarop slecht. Daarom trainen we
  zelf, door een voorgetraind YOLO11n-model bij te trainen ("fine-tunen") op onze eigen beelden.
* De Roboflow-dataset heet **`tello-downward-puddle-detection`**, met één klasse: `puddle`.

De volledige cyclus:

1. Beelden opnemen met de drone
2. Labelen in Roboflow en exporteren
3. Trainen op de laptop (GPU)
4. Los testen op beelden
5. Model in `models/puddles.pt` zetten
6. Testen op de drone, met de Jetson
7. Foutbeelden bijlabelen en opnieuw trainen

---

## 1. Eenmalig: de laptop klaarmaken

We trainen op een Windows-laptop met een NVIDIA-kaart (RTX 4070). Op een laptop zonder NVIDIA-kaart
kan je trainen in Google Colab (gratis GPU); de commando's zijn dezelfde.

```bash
git clone https://github.com/GilesS420/Telllo_edu.git
cd Telllo_edu
python -m venv yolo-env
yolo-env\Scripts\activate          # Windows  (Linux/macOS: source yolo-env/bin/activate)
pip install -r requirements.txt
```

`requirements.txt` installeert ook `ultralytics`. Controleer daarna of PyTorch de GPU ziet:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

Staat er `False`, installeer dan PyTorch met CUDA (zie de keuzetool op pytorch.org), bijvoorbeeld:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

> Tip: Python 3.10 tot 3.12 werkt het vlotst met PyTorch en Ultralytics.

---

## 2. Deel A: de dataset maken

### Stap A1. Beelden opnemen met de drone

Neem beelden op met de echte drone, zodat het model leert wat het later ziet:

```bash
python tello_gui.py --record dataset
```

of start de GUI gewoon en klik rechts op **● Opnemen (dataset)**. Er worden 2 beelden per seconde
van de onderste camera bewaard in `dataset/`, al bijgesneden en in grijswaarden, precies zoals het
model ze later krijgt.

Wat je moet opnemen:

* **Dezelfde ruimtes, vloeren en hoogtes** als tijdens de echte missie. Dat is belangrijker dan
  het aantal beelden.
* **Plassen in alle vormen**: groot, klein, langwerpig, met verschillende belichting, op elke
  vloersoort die je gebruikt.
* **Negatieven**, dus beelden zonder plas: schaduwen, tape, kabels, vlekken, het H-platform,
  glimmende vloerdelen. Die leren het model wat géén plas is.
* Richtwaarde om te starten: **300 tot 500 beelden**, waarvan 20 tot 30% zonder plas.

Gooi bijna-identieke beelden weg (bijvoorbeeld als de drone lang stil hing). Het map `dataset/`
staat in `.gitignore`, dus de beelden komen niet in git.

### Stap A2. Uploaden en labelen in Roboflow

1. Log in op [roboflow.com](https://roboflow.com) en open het project
   **tello-downward-puddle-detection** (vraag toegang aan Giles). Projecttype: *Object Detection*.
2. **Upload** de beelden uit `dataset/`. Doe dat **per vlucht**, zodat je weet welke beelden bij
   elkaar horen.
3. **Labelen**: teken een rechthoek (bounding box) strak rond elke plas, klasse `puddle`.
   * Elke zichtbare plas krijgt een box, ook als hij half in beeld is.
   * Beelden zonder plas laat je leeg en markeer je als *null* / "no objects". Niet weggooien.
   * Twijfel je of iets een plas is? Wees consequent; liever overslaan dan willekeurig labelen.
4. **Train/valid/test verdelen per vlucht, niet per beeld.** Zet één of twee volledige vluchten
   apart voor *valid* en *test*. Beelden van dezelfde vlucht lijken heel sterk op elkaar; staan ze
   in train én valid, dan lijkt het model beter dan het is.

### Stap A3. Een versie maken en exporteren

1. Klik op **Generate** (nieuwe versie).
2. **Preprocessing**: *Auto-Orient* aan. Resize mag op 320×320 (dat is de beeldgrootte waarop we
   trainen) of uit laten.
3. **Augmentation** (maakt extra varianten van de trainbeelden). Zinvol voor beelden van recht boven:
   * Flip horizontaal én verticaal
   * Rotatie 90°
   * Helderheid ±20%, belichting
   * Een beetje blur en ruis
4. **Export** → formaat **YOLOv11** → *Download zip*. Pak de zip uit in de repo, in een eigen map,
   bijvoorbeeld `roboflow_export/`:

```
roboflow_export/
    data.yaml
    train/images  train/labels
    valid/images  valid/labels
    test/images   test/labels
```

Elk label is een `.txt` met per plas één regel: `0 x_midden y_midden breedte hoogte` (genormaliseerd
tussen 0 en 1). Klasse `0` = `puddle`.

> Gebruik niet dezelfde map als de opnames (`dataset/`), anders raken opnames en export door elkaar.

---

## 3. Deel B: het model trainen

### Stap B1. Training starten

In de repo-map, met de venv actief:

```bash
yolo detect train data=roboflow_export/data.yaml model=yolo11n.pt imgsz=320 epochs=100 batch=32 device=0 name=puddles
```

Wat de opties betekenen:

| optie | waarom |
|---|---|
| `model=yolo11n.pt` | het kleinste YOLO11-model ("nano"): snel genoeg om live te draaien. Wordt automatisch gedownload. |
| `imgsz=320` | past bij de lage resolutie van de onderste camera. **Moet gelijk zijn aan `PUDDLE_IMGSZ` in `drone/config.py`.** |
| `epochs=100` | aantal keer door de hele dataset. Ultralytics stopt vanzelf vroeger als het niet meer verbetert. |
| `batch=32` | beelden per stap. Krijg je "CUDA out of memory", zet dan 16. |
| `device=0` | de eerste GPU. Zonder GPU: `device=cpu` (veel trager). |
| `name=puddles` | resultaten komen in `runs/detect/puddles/` (bij een volgende run `puddles2`, …). |

Heb je in Roboflow geen rotatie/flip toegevoegd, laat YOLO het dan doen met
`fliplr=0.5 flipud=0.5 degrees=180` (een bovenaanzicht heeft geen "boven").

Op een RTX 4070 duurt dit met een paar honderd beelden typisch enkele minuten tot een kwartier
(inschatting).

### Stap B2. Resultaten lezen

Alles staat in `runs/detect/puddles/`:

* `weights/best.pt` → **het model dat je gebruikt** (beste score op de validatieset).
* `results.png` → grafieken per epoch. De *loss*-lijnen moeten dalen; de `mAP50`-lijn moet stijgen
  en afvlakken.
* `confusion_matrix.png` → hoeveel plassen gemist zijn en hoeveel achtergrond als plas gezien is.
* `val_batch0_pred.jpg` → voorbeeldbeelden met de voorspellingen. Kijk hier echt naar.

Belangrijkste getallen:

* **mAP50**: hoe goed de kaders overeenkomen met de labels (0 tot 1, hoger is beter).
* **Precision**: van de gevonden plassen, hoeveel zijn echt (weinig valse meldingen).
* **Recall**: van de echte plassen, hoeveel zijn gevonden (weinig gemiste plassen).

Een hoge score op de validatieset is een goed teken, maar **de echte test is een nieuwe vlucht** die
het model nooit zag (Deel C en D).

De scores op de **testset** (beelden die tijdens het trainen niet gebruikt zijn) vraag je zo op:

```bash
yolo detect val data=roboflow_export/data.yaml model=runs/detect/puddles/weights/best.pt imgsz=320 split=test
```

### Stap B3. Model op zijn plek zetten

```bash
copy runs\detect\puddles\weights\best.pt models\puddles.pt      # Windows
# cp runs/detect/puddles/weights/best.pt models/puddles.pt      # Linux/macOS
```

`models/` staat in `.gitignore`: het model gaat niet mee in git (alleen `models/.gitkeep`). Wil je
het delen, zet het dan op een gedeelde schijf of in de Roboflow-versie, en noteer welke dataset-versie
en welke run het was.

### Stap B4. Runs vergelijken en een model kiezen

Na een paar trainingen staan er meerdere mappen in `runs/detect/`. Twee kleine hulpscripts in de
root van de repo helpen kiezen:

```bash
python vergelijk.py     # per run de beste mAP50 (validatie), bij welke epoch, en of best.pt er is
python avg_conf.py      # gemiddelde, min. en max. zekerheid op dataset/test/images
```

`avg_conf.py` gebruikt een vast pad (`runs/detect/puddles/weights/best.pt` en
`dataset/test/images`): pas die twee regels bovenaan aan voor de run en testset die je wilt bekijken.

Kies op de **testset**, niet alleen op de validatieset, en let vooral op de **recall**: een gemiste
plas is erger dan een valse melding, want een valse melding moet sowieso `PUDDLE_MIN_HITS` keer
gezien worden voor ze telt. Zo is `train-6` gekozen:

| run | `imgsz` | mAP50 (test) | recall (test) |
|---|---|---|---|
| **`train-6`** (gekozen) | 320 | **0.719** | **0.661** |

Een model op 640 (`puddles-4`) is ook geprobeerd maar niet gekozen. 320 past bij de lage resolutie
van de onderste camera en is sneller. Verander je `imgsz`, zet `PUDDLE_IMGSZ` in `drone/config.py`
dan op dezelfde waarde.

---

## 4. Deel C: het model los testen (zonder vliegen)

Test eerst op beelden die het model nog niet zag, bijvoorbeeld de testset uit Roboflow:

```bash
python -m drone.puddles roboflow_export/test/images
```

Er opent een venster met de kaders. **Spatie** = volgende beeld, **q** = stoppen. In de terminal zie
je per beeld het aantal plassen en de zekerheid (confidence).

Je kan ook een volledige nieuwe opname of een video testen:

```bash
python -m drone.puddles dataset
python -m drone.puddles vlucht.mp4
python -m drone.puddles roboflow_export/test/images --model runs/detect/puddles2/weights/best.pt
```

Waar je op let:

* **Gemiste plassen?** → meer (en gevarieerdere) plasbeelden labelen, of `PUDDLE_CONF` in
  `drone/config.py` iets lager zetten (standaard `0.4`).
* **Valse plassen** (schaduw, tape, de H)? → precies die beelden als negatief toevoegen, of
  `PUDDLE_CONF` iets hoger.

---

## 5. Deel D: het model testen op de drone

Werk altijd in deze volgorde: eerst op de grond, dan een korte vlucht in een lege ruimte, dan pas
met de Jetson en een volledige missie.

### Stap D1. Controleren dat het model geladen is

```bash
python tello_gui.py --sim        # zonder drone, gewoon om te kijken of alles opstart
python tello_gui.py              # met de drone (laptop op de Wi-Fi van de Tello)
```

In het infovak (bij de H-info) staat de status van de plasdetectie:

* `Nog geen plassen gevonden (model actief)` → goed.
* `Plasdetectie uit: geen model (models/puddles.pt)` → het bestand staat niet op de juiste plek.
* `… ultralytics is niet geïnstalleerd` → `pip install ultralytics` in de juiste venv.

In de simulator (`--sim`) liggen wel plassen, maar daar zoekt `SimPuddleModel` ze, niet je
getrainde model: een model dat op echte foto's getraind is, herkent getekende plassen niet. De
simulator test dus alles ná het model (coördinaten, samenvoegen, foto, Jetson-berichten). Met
`python tello_gui.py --sim --sim-model` laat je toch je eigen model op de simulatorbeelden lopen,
bijvoorbeeld om te zien of het laadt.

### Stap D2. Test op de grond

1. Zet het camerabeeld op de **onderste camera**.
2. Houd de drone met de hand (motoren uit) boven een plas, of leg de drone naast een plas en schuif
   hem erover.
3. In het camerabeeld moet een **blauw kader** rond de plas verschijnen, met de zekerheid.

Op de grond komen er nog **geen coördinaten**: die worden pas berekend in de lucht, omdat de hoogte
van de ToF-sensor nodig is.

> Nog nooit gevlogen met deze drone? Doe eerst het hoofdstuk *Kalibreren* uit de README (beeldhoek,
> oriëntatie van de camera, `YAW_SIGN`). Zonder kalibratie kloppen de coördinaten niet.

### Stap D3. Een korte vlucht over een plas (zonder Jetson)

1. Leg een plas (of een nagebootste) ongeveer **1 m vóór de drone**. Onthoud: bij elke start is de
   drone de oorsprong `(0, 0)`, **x = vooruit (neus), y = links**, in cm.
2. Meet met een meetlint de echte positie van het midden van de plas t.o.v. het startpunt.
3. In de GUI: **Laden** → `json_flights/mission_example.json` (vierkant van 1 m op 80 cm hoogte), of
   teken een eigen pad dat over de plas gaat. Start de missie.
4. Wat er moet gebeuren:
   * Het kader verschijnt in het camerabeeld.
   * Na 3 detecties (`PUDDLE_MIN_HITS`) komt de plas op de **grondkaart** als **P1**, met coördinaat.
   * De drone **onderbreekt zijn pad**, vliegt boven de plas, centreert zich en neemt een foto.
   * In `photos/` staan `plas1_<tijd>.png` en `plas1_<tijd>_detectie.png`. In de GUI staat 📷
     achter de plas.
   * Daarna vliegt hij zijn pad verder en landt.
5. **Vergelijk** de coördinaat in de GUI met je meting. Een paar cm tot ongeveer 10 cm verschil is
   normaal. Zit hij er ver naast, controleer dan de kalibratie (`CAM_HFOV_DEG`, `CAM_FORWARD_SIGN`,
   `CAM_LEFT_SIGN`, `CAM_ROTATE_DEG`).

Wil je de foto-onderbreking even niet, zet dan `PUDDLE_PHOTO = False` in `drone/config.py`.

### Stap D4. De Jetson koppelen

De laptop hangt aan de Wi-Fi van de Tello; de Jetson moet de laptop via een **tweede netwerk**
bereiken (ethernetkabel of tweede Wi-Fi-adapter).

In `drone/config.py` op de laptop:

```python
LISTEN_HOST = "0.0.0.0"          # standaard 127.0.0.1: dan hoort de laptop de Jetson niet
JETSON_HOST = "192.168.x.y"      # IP van de Jetson: alleen die mag commando's sturen
```

Meer uitleg over het netwerk en het assenstelsel staat in
[jetson-koppeling.md](jetson-koppeling.md). Op de Jetson een klein testscript dat alles van de drone
toont (`luister.py`):

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

En een missie sturen vanaf de Jetson (`stuur.py`, IP van de laptop invullen):

```python
import json, socket

LAPTOP = "192.168.x.z"
mission = json.load(open("mission_example.json"))
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(
    json.dumps(mission).encode(), (LAPTOP, 9000))
```

Start eerst `luister.py`, dan de GUI op de laptop, dan `stuur.py`. Je moet zien:

1. een `ack` voor de missie,
2. `waypoint_reached`-berichten,
3. één `puddle`-bericht per plas, met `x`, `y` in cm (mission frame van die vlucht),
4. een `puddle_photo`-bericht met de bestandsnaam van de foto,
5. `mission_done` met de lijst van alle plassen.

Elke seconde komt er ook een `status` (in het script hierboven verborgen).

### Stap D5. Checklist veiligheid

* Eerst `--sim`, dan een kleine missie in een **lege ruimte**.
* Batterij voldoende (`MIN_BATTERY`), waypoints binnen de geofence (`GEOFENCE`).
* Een natte, spiegelende vloer kan de stabiliteit van de Tello storen: vlieg niet laag recht boven
  een grote plas en houd de hand bij **Landen** / noodstop.
* Telkens met de hand verplaatst? Druk op **Reset omgeving** vóór de volgende vlucht.

---

## 6. Verbeteren: de leerlus

Een model wordt goed door een paar rondes:

1. Vlieg een nieuwe testvlucht en neem tegelijk op (`--record dataset`).
2. Zoek de beelden waar het model **een plas mist** of **iets fout als plas ziet**.
3. Label precies die beelden in Roboflow en voeg ze toe (nieuwe vlucht = eigen split).
4. Maak een nieuwe versie, exporteer, train opnieuw (eventueel verder vanaf je vorige model met
   `model=runs/detect/puddles/weights/best.pt`).
5. Vergelijk op **dezelfde** testset met `yolo detect val … split=test` en
   `python -m drone.puddles … --model …` (zie [Stap B4](#stap-b4-runs-vergelijken-en-een-model-kiezen)).
   Alleen als het beter is, kopieer je het naar `models/puddles.pt`.

---

## 7. Problemen oplossen

| probleem | oplossing |
|---|---|
| `torch.cuda.is_available()` geeft `False` | PyTorch met CUDA installeren (zie stap 1), NVIDIA-driver bijwerken. |
| Training: "images not found" / dataset-fout | Open `roboflow_export/data.yaml` en zet de paden goed, bv. `path: C:/…/roboflow_export`, `train: train/images`, `val: valid/images`, `test: test/images`. |
| `CUDA out of memory` | `batch=16` of `batch=8`. |
| GUI: "Plasdetectie uit: geen model" | `best.pt` kopiëren naar `models/puddles.pt` (exacte naam). |
| Kaders in het venster, maar geen plas op de kaart | Drone staat op de grond (dan geen coördinaten), of de plas raakt de rand van het beeld, of hij is nog geen 3 keer gezien. |
| Eén plas wordt twee plassen | `PUDDLE_MERGE_CM` iets groter (standaard 40). |
| Coördinaten zitten er ver naast | Kalibratie uit de README; startpositie gecontroleerd? (x = vooruit, y = links vanaf de start). |
| Jetson krijgt niets | `LISTEN_HOST = "0.0.0.0"`, `JETSON_HOST` = IP van de Jetson, firewall op de laptop (UDP 9000 in), Jetson luistert op 9001. |
| Model traag op de laptop | `PUDDLE_HZ` lager, of exporteer naar ONNX/TensorRT (`yolo export model=models/puddles.pt format=engine half=True imgsz=320`, op dezelfde machine waar het draait) en zet `PUDDLE_MODEL` op dat bestand. |

---

## 8. Spiekbriefje

```bash
# opnemen
python tello_gui.py --record dataset
# trainen
yolo detect train data=roboflow_export/data.yaml model=yolo11n.pt imgsz=320 epochs=100 batch=32 device=0 name=puddles
# model plaatsen
copy runs\detect\puddles\weights\best.pt models\puddles.pt
# scores op de testset + runs vergelijken
yolo detect val data=roboflow_export/data.yaml model=runs/detect/puddles/weights/best.pt imgsz=320 split=test
python vergelijk.py
# los testen
python -m drone.puddles roboflow_export/test/images
# op de drone
python tello_gui.py
```

Instellingen (`drone/config.py`): `PUDDLE_MODEL`, `PUDDLE_CONF` (0.4), `PUDDLE_IMGSZ` (320, gelijk aan
training), `PUDDLE_MIN_HITS` (3), `PUDDLE_MERGE_CM` (40), `PUDDLE_PHOTO` (True), `LISTEN_HOST`,
`JETSON_HOST`.
