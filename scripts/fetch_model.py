#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Hiyori Live2D 模型全部文件（jsdelivr 镜像 Live2D 官方仓库）"""
import json, os, urllib.request

BASE = "https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori"
OUT = os.path.join(os.path.dirname(__file__), "..", "web", "assets", "models", "hiyori")
OUT = os.path.normpath(OUT)
os.makedirs(OUT, exist_ok=True)

meta = json.load(open("/tmp/hiyori_test.json"))
fr = meta["FileReferences"]
files = [fr["Moc"], fr["Physics"], fr["Pose"], fr["DisplayInfo"]]
if "UserData" in fr: files.append(fr["UserData"])
files += fr["Textures"]
for grp in fr.get("Motions", {}).values():
    for m in grp: files.append(m["File"])
files += [e["File"] for e in fr.get("Expressions", [])]
files = sorted(set(files))
print(f"共 {len(files)} 个文件", flush=True)

ok, fail = 0, []
for f in files:
    dest = os.path.join(OUT, f)
    os.makedirs(os.path.dirname(dest) or OUT, exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 100:
        ok += 1; continue
    try:
        urllib.request.urlretrieve(f"{BASE}/{f}", dest); ok += 1
        print("ok", f, flush=True)
    except Exception as e:
        fail.append((f, str(e)[:80]))

with open(os.path.join(OUT, "Hiyori.model3.json"), "w") as fp:
    fp.write(open("/tmp/hiyori_test.json").read())
print(f"成功 {ok}, 失败 {len(fail)}", flush=True)
for f, e in fail: print("FAIL", f, e, flush=True)
print("Expressions:", [e["File"] for e in fr.get("Expressions", [])] or "无", flush=True)
print("Motion组:", list(fr.get("Motions", {}).keys()), flush=True)
print("DL_DONE", flush=True)
