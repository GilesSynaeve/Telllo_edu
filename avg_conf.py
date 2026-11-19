from ultralytics import YOLO
m = YOLO("runs/detect/puddles/weights/best.pt")
confs, zonder = [], 0
for r in m.predict("dataset/test/images", conf=0.25, imgsz=320, stream=True, verbose=False):
    c = r.boxes.conf.tolist()
    confs += c
    zonder += not c
print(f"detecties: {len(confs)}, gemiddelde conf: {sum(confs)/len(confs):.3f}")
print(f"min {min(confs):.3f} / max {max(confs):.3f}, beelden zonder detectie: {zonder}")