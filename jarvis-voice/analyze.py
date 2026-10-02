import librosa, numpy as np, glob, os, soundfile as sf

def stats(path):
    y, sr = librosa.load(path, sr=None, mono=True)
    # F0 via pyin (voiced)
    f0, vflag, vprob = librosa.pyin(y, fmin=60, fmax=400, sr=sr)
    f0v = f0[~np.isnan(f0)]
    if len(f0v) == 0:
        return None
    # spectral centroid (brightness)
    cen = librosa.feature.spectral_centroid(y=y, sr=sr).mean()
    return dict(
        dur=len(y)/sr, sr=sr,
        f0_median=float(np.median(f0v)),
        f0_mean=float(np.mean(f0v)),
        f0_p10=float(np.percentile(f0v,10)),
        f0_p90=float(np.percentile(f0v,90)),
        centroid=float(cen),
    )

base = r"C:\Users\Дамир\Downloads\jarvis-lab\jarvis-lab\resources\sound\voices"
groups = {
    "ORIG jarvis-og":   glob.glob(os.path.join(base,"jarvis-og","ru","*.wav")),
    "ORIG jarvis-howdy": glob.glob(os.path.join(base,"jarvis-howdy","ru","*.wav")),
    "REF used":          [r"C:\jarvis-voice\ref_jarvis.wav"],
    "GENERATED":         [r"C:\jarvis-voice\out_test1.wav"],
}

for name, files in groups.items():
    meds=[]; cens=[]
    for f in files:
        s = stats(f)
        if s: meds.append(s["f0_median"]); cens.append(s["centroid"])
    if meds:
        print(f"{name:20s}  n={len(meds):2d}  F0_median={np.median(meds):6.1f}Hz  "
              f"F0_range[{np.min(meds):.0f}-{np.max(meds):.0f}]  centroid={np.mean(cens):5.0f}Hz")

print("\n--- по каждому оригинальному файлу (F0 медиана) ---")
for name in ["jarvis-og","jarvis-howdy"]:
    for f in sorted(glob.glob(os.path.join(base,name,"ru","*.wav"))):
        s = stats(f)
        if s: print(f"  {name}/{os.path.basename(f):16s} F0={s['f0_median']:6.1f}Hz  dur={s['dur']:.1f}s")
