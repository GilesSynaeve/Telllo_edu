import csv, glob, os
for f in sorted(glob.glob("runs/detect/*/results.csv")):
    rows = list(csv.DictReader(open(f)))
    if not rows: continue
    k = [c for c in rows[0] if "mAP50(B)" in c and "95" not in c][0]
    best = max(rows, key=lambda r: float(r[k]))
    heeft_pt = os.path.exists(os.path.join(os.path.dirname(f), "weights/best.pt"))
    print(f"{os.path.basename(os.path.dirname(f)):12} mAP50={float(best[k]):.3f} epoch={best['epoch'].strip()} best.pt={'ja' if heeft_pt else 'NEE'}")