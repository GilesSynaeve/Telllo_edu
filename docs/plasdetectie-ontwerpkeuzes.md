# Plasdetectie: waarom een getraind model, en hoe het in de code zit

Dit document legt uit welke keuzes er gemaakt zijn voor het herkennen van plassen. Hoe je het
model zelf traint en test staat in [plasmodel-trainen-en-testen.md](plasmodel-trainen-en-testen.md).

## Kort

We gebruiken geen model dat vanaf nul getraind is en ook geen kant-en-klaar plasmodel. We nemen
een **voorgetraind YOLO11n-model** (Ultralytics) en trainen het bij (fine-tunen) op **eigen
beelden van de onderste camera van de Tello**. Dat werkt met een paar honderd gelabelde beelden en
is klein genoeg om live mee te draaien.

Het gekozen model is de run **`train-6`**: YOLO11n, `imgsz=320`, op de testset **mAP50 0.719** en
**recall 0.661**. Het staat als `models/puddles.pt` op de laptop.

## Wat we eerst hadden: drempels met OpenCV

De eerste versie (`drone/puddle_detector.py`) was pure OpenCV: de mediaanhelderheid van de vloer
nemen en alles markeren wat daar duidelijk van afwijkt (drempel + contouren). Dat faalt op
schaduwen, reflecties van lampen, tape, kabels en vlekken: alles wat donkerder of lichter is dan de
vloer werd een "plas". Die detector is daarom verwijderd (PR #15). De camerahulpfuncties die hij
gebruikte (`cm_per_pixel`, `pixel_to_world`) staan nu in `drone/downcam.py`.

## Waarom geen bestaand plasmodel

Er bestaan datasets met plassen (Roboflow Universe, zoek op "puddle", en de academische
Puddle-1000-dataset). Die beelden zijn bijna altijd **buiten, schuin vanuit een auto of op
ooghoogte, in kleur**. De beelden van de Tello zijn **loodrecht van boven, klein (320 × 240),
wazig en grijs**, meestal binnen. Dat verschil is zo groot dat zo'n model op de drone weinig vindt.
Zulke datasets zijn hooguit bruikbaar als extra trainingsdata, niet als vervanging.

## De dataset

* Roboflow-project **`tello-downward-puddle-detection`**, één klasse: `puddle`, bounding boxes.
* Opgenomen met de drone zelf (`python tello_gui.py --record dataset` of de knop *Opnemen*), dus
  met dezelfde camera, hoogte, vloer en dezelfde bewerking (bijgesneden, grijs) als tijdens een
  missie.
* Ook **beelden zonder plas** (schaduwen, tape, het H-platform, glimmende vloer): die leren het
  model wat géén plas is.
* Train/valid/test verdeeld **per vlucht**, niet per beeld. Beelden van één vlucht lijken sterk
  op elkaar; staan ze in train én test, dan lijkt het model beter dan het is.

Voor het midden van een plas is een box genoeg. Wil je later ook de vorm of de oppervlakte precies
weten, label dan met polygonen en train `yolo11n-seg`.

## Waar het model draait: op de laptop

Het advies was eerst om het model op de Jetson te draaien. Uiteindelijk draait het **op de
laptop**, in dezelfde software die de drone bestuurt (`drone/puddles.py`):

* de laptop heeft het camerabeeld al (de Tello stuurt zijn video alleen naar het toestel op zijn
  Wi-Fi);
* voor de coördinaat van een plas zijn de **hoogte (ToF)** en de **positie van de drone op het
  moment van het beeld** nodig, en die zitten ook op de laptop;
* de drone moet bij een nieuwe plas zijn pad onderbreken en er een foto van nemen. Dat kan alleen
  als de detectie in dezelfde lus zit als de besturing.

De Jetson krijgt per plas één `puddle`-bericht met de coördinaten in cm (zie
[jetson-koppeling.md](jetson-koppeling.md)). Wil je het model toch op de Jetson of sneller op de
laptop: `PUDDLE_MODEL` mag ook een `.onnx` of TensorRT-`.engine` zijn
(`yolo export model=models/puddles.pt format=engine half=True imgsz=320`, op de machine waar het
draait).

## Van box naar coördinaat

1. Het model draait in een eigen thread op het nieuwste beeld, hoogstens `PUDDLE_HZ` (5) keer per
   seconde, zodat het de H-detectie en het camerabeeld niet ophoudt.
2. Het midden van elke box wordt met de ToF-hoogte en de beeldhoek van de camera omgerekend naar cm
   onder de drone, en daarna met de positie van de drone (min `FRAME_LATENCY_S`) naar het mission
   frame (`pixel_to_world` in `drone/downcam.py`). Boxen die de rand van het beeld raken tellen
   niet mee: de plas is maar half in beeld.
3. Boxen binnen `PUDDLE_MERGE_CM` (40 cm) zijn dezelfde plas. Pas na `PUDDLE_MIN_HITS` (3)
   detecties telt een plas en gaat er een bericht naar de Jetson. Eén foute detectie wordt zo geen
   plas.
4. Tijdens een missie onderbreekt een nieuwe plas het pad: de drone centreert zich erboven en neemt
   een foto van recht boven (`photos/`), daarna vliegt hij verder.

## Testen zonder drone

Een model dat op echte foto's getraind is, herkent de getekende plassen van de simulator niet. In
`--sim` zoekt `SimPuddleModel` (`drone/sim.py`) daarom de plassen in het camerabeeld. Alles na het
model (stap 2 tot 4) is in de simulator wel de echte code. Het model zelf test je los op echte
beelden met `python -m drone.puddles <map of video>`.
