# Connexion AP et Wi-Fi domestique (STA)

Lancer `python tello_gui.py`. Une fenêtre de connexion s'ouvre **avant** le
démarrage de djitellopy. Aucun identifiant Wi-Fi n'est sauvegardé dans le projet.
Le choix de connexion est limité à la session ; AP reste le choix par défaut.

## Connexion directe AP

Connecter le PC au réseau **TELLO-…**, choisir **Connexion directe (AP)** et
cliquer **Connecter**. L'adresse habituelle est `192.168.10.1`.
Le démarrage des caméras et des fonctions de navigation reste celui du mode AP.

## Préparer STA une seule fois

1. Poser le drone, moteurs arrêtés. Connecter le PC au Wi-Fi **TELLO-…**.
   Fermer les autres logiciels Tello, qui pourraient occuper le port UDP 8889.
2. Lancer l'interface, choisir **Wi-Fi domestique (STA)** et saisir le SSID et
   le mot de passe du réseau domestique 2,4 GHz. Éviter le réseau invité et
   l'isolation des clients du routeur.
3. Cliquer **Envoyer la configuration STA**. L'assistant envoie `command`, puis
   une seule commande `ap` contenant les identifiants. Il vide immédiatement
   le champ du mot de passe et attend le redémarrage du drone.
   Un accusé absent est signalé comme **résultat non confirmé**, sans répétition
   automatique de la configuration.
4. Reconnecter **manuellement le PC** au Wi-Fi domestique. L'assistant ne crée
   pas de profil Wi-Fi Windows et ne modifie pas les paramètres réseau du PC.
5. Saisir l'IPv4 attribuée au drone dans la liste DHCP du routeur, ou cliquer
   **Détecter le drone**. Le sous-réseau proposé s'actualise au changement de
   réseau tant que vous ne l'avez pas modifié. La recherche reste limitée à
   un sous-réseau IPv4 privé de 256 adresses maximum ; elle envoie uniquement
   `command` et `sdk?`, sans mouvement. Choisir le bon appareil si plusieurs
   drones sont présents.
6. Cliquer **Connecter**. Le PC conserve alors son accès Internet par le routeur.

Lors des démarrages suivants, le drone conserve les paramètres STA dans son
propre firmware : choisir STA et renseigner/détecter son IP, sans renvoyer le
mot de passe. Une réservation DHCP sur le routeur peut stabiliser cette IP.

Le SDK décrit des paramètres séparés par des espaces sans mécanisme
d'échappement. Cet assistant refuse donc les espaces et caractères de contrôle
dans le SSID/mot de passe, plutôt que d'envoyer une commande ambiguë.
Le mot de passe doit contenir de 8 à 63 octets ; le SSID de 1 à 32 octets UTF-8.

## Vidéo : limitation déterminante

La [documentation publiée de djitellopy](https://djitellopy.readthedocs.io/en/latest/tello/)
signale l'absence de vidéo des Tello EDU en STA, tandis que le
[code actuel de la bibliothèque](https://github.com/damiafuentes/DJITelloPy/blob/master/djitellopy/tello.py)
mentionne sa prise en charge avec SDK 3. **Le résultat dépend donc du firmware ;
le passage en STA ne garantit pas les caméras.**

Par défaut, STA démarre sans vidéo, avec les commandes de base et la télémétrie.
La case **Essayer la vidéo STA** lance un essai limité : une réponse `ok` à
`streamon` ne suffit pas, deux nouvelles images décodées doivent être reçues.
En cas d'échec, l'interface continue sans caméra avec un avertissement permanent.
Si seule la caméra avant est disponible, son image peut être affichée, mais les
fonctions utilisant la caméra inférieure restent bloquées.

Sans vidéo inférieure confirmée :

- missions et déplacements vers des coordonnées désactivés ;
- odométrie visuelle, carte du terrain, détection et atterrissage H indisponibles ;
- saltos avec retour et enregistrement du dataset inférieur désactivés ;
- position horizontale signalée comme inconnue, sans coordonnées inventées ;
- décollage simple, atterrissage sur place, arrêt d'urgence et capteurs conservés.

Ces protections s'appliquent aux boutons, aux raccourcis et aux commandes Jetson.
Pour retrouver les fonctions visuelles habituelles, revenir en AP.

## Revenir en AP

Le bouton **Retour AP / aide** sélectionne AP dans le logiciel et affiche la
procédure physique. Il ne change pas à lui seul le mode radio du drone.
Drone posé et moteurs arrêtés, allumer le drone puis maintenir son bouton
d'alimentation environ **5 secondes** pour réinitialiser le Wi-Fi. Attendre son
redémarrage, reconnecter le PC à **TELLO-…**, puis choisir AP dans l'assistant.
Cette procédure efface les anciens paramètres Wi-Fi du drone.

La procédure est décrite dans le
[guide officiel SDK 3.0, section des questions fréquentes](https://dl.djicdn.com/downloads/RoboMaster%20TT/Tello_SDK_3.0_User_Guide_en.pdf).
La commande STA figure aussi dans le
[guide officiel SDK 2.0](https://dl-cdn.ryzerobotics.com/downloads/Tello/Tello%20SDK%202.0%20User%20Guide.pdf).

## Dépannage et confidentialité

- Pas de réponse SDK : vérifier le mode radio, l'IP DHCP, le même réseau local,
  l'absence d'isolation des clients et le pare-feu UDP **8889**.
- SDK reconnu mais pas de capteurs : vérifier le pare-feu UDP **8890** pour le
  Python de votre environnement virtuel. La vidéo, lorsqu'elle existe, utilise
  UDP **11111**.
- Aucun drone détecté : vérifier le sous-réseau et consulter la liste DHCP du
  routeur. Une IP saisie manuellement reste possible. Relancer l'application
  après un échec de connexion pour rouvrir l'assistant.
- Le mot de passe n'est transmis ni au logger SDK, ni aux fichiers, ni aux
  arguments de lancement. Les réponses d'erreur de la configuration ne sont pas
  reproduites, car un appareil peut y répéter la commande sensible. L'échange
  `ap` utilise le protocole UDP du SDK, qui ne chiffre pas lui-même la commande.
- Les outils autonomes `tello_combined.py` et `calibrate_camera.py` conservent
  leur fonctionnement AP. La sélection AP/STA concerne `tello_gui.py` et les
  modules de `DroneApp`, qui partagent la même instance Tello et la même IP.

Pour ouvrir directement une session sans l'assistant, sans identifiants :

```powershell
python tello_gui.py --connection ap
python tello_gui.py --connection sta --host 192.168.1.77
python tello_gui.py --connection sta --host 192.168.1.77 --sta-video
python tello_gui.py --sim
```

`drone/config.py` contient uniquement le mode et les IP par défaut, ainsi que
les délais. **Ne jamais y ajouter de mot de passe Wi-Fi.**

## Vérification sans drone

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
```

Les tests du réseau et du démarrage AP/STA remplacent les sockets et les appels
SDK par des doubles de test. Ils ne contactent aucun drone et ne lancent aucune
commande de vol réelle. La connexion à votre routeur et la compatibilité vidéo
de votre firmware restent à vérifier sur le matériel, drone posé.
