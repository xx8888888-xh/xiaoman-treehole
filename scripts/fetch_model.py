#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Hiyori Live2D 模型全部文件（jsdelivr 镜像 Live2D 官方仓库）
用法: python3 scripts/fetch_model.py [--force]
  --force: 强制重写 model3.json（默认不覆盖已存在的手术版本）
"""
import argparse
import json
import os
import sys
import urllib.request
import shutil

BASE = "https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori"
# 内置模型清单（从官方 Hiyori.model3.json 抽取），脚本自包含、无外部依赖
MANIFEST = {
    "FileReferences": {
        "Moc": "Hiyori.moc3",
        "Physics": "Hiyori.physics3.json",
        "Pose": "Hiyori.pose3.json",
        "DisplayInfo": "Hiyori.cdi3.json",
        "Textures": [
            "Hiyori.2048/texture_01.png",
            "Hiyori.2048/texture_02.png"
        ],
        "Motions": {
            "Idle": [
                {"File": "motions/Hiyori_m01.motion3.json", "FadeInTime": 0.5, "FadeOutTime": 0.5},
                {"File": "motions/Hiyori_m02.motion3.json", "FadeInTime": 0.5, "FadeOutTime": 0.5},
                {"File": "motions/Hiyori_m03.motion3.json", "FadeInTime": 0.5, "FadeOutTime": 0.5}
            ],
            "TapBody": [
                {"File": "motions/Hiyori_m04.motion3.json", "FadeInTime": 0.5, "FadeOutTime": 0.5},
                {"File": "motions/Hiyori_m05.motion3.json", "FadeInTime": 0.5, "FadeOutTime": 0.5}
            ]
        },
        "Expressions": []
    }
}
OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "web", "assets", "models", "hiyori"))
M3_PATH = os.path.join(OUT, "Hiyori.model3.json")
M3_ORIG = M3_PATH + ".orig"

def download_with_timeout(url, dest, timeout=30):
    """下载文件，带超时；成功返回 True，失败返回 False"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'xiaoman-fetch/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, 'wb') as f:
            shutil.copyfileobj(resp, f)
        return True
    except Exception as e:
        print(f"  下载失败: {e}", flush=True)
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="强制重写 model3.json（默认不覆盖）")
    args = parser.parse_args()

    os.makedirs(OUT, exist_ok=True)

    fr = MANIFEST["FileReferences"]
    # 收集所有文件：用 .get() 防御性取值，过滤空值
    files = []
    for key in ("Moc", "Physics", "Pose", "DisplayInfo"):
        val = fr.get(key)
        if val:
            files.append(val)
        else:
            print(f"⚠ 清单缺失字段: {key}", flush=True)
    if fr.get("UserData"):
        files.append(fr["UserData"])
    files += fr.get("Textures", [])
    for grp in fr.get("Motions", {}).values():
        for m in grp:
            f = m.get("File")
            if f:
                files.append(f)
    for e in fr.get("Expressions", []):
        f = e.get("File")
        if f:
            files.append(f)
    files = sorted(set(files))
    print(f"共 {len(files)} 个文件", flush=True)

    ok, fail = 0, []
    for f in files:
        dest = os.path.join(OUT, f)
        os.makedirs(os.path.dirname(dest) or OUT, exist_ok=True)
        if os.path.exists(dest) and os.path.getsize(dest) > 100:
            ok += 1
            continue
        url = f"{BASE}/{f}"
        if download_with_timeout(url, dest, timeout=30):
            ok += 1
            print("ok", f, flush=True)
        else:
            fail.append((f, "download failed"))

    # model3.json 处理：仅当不存在或 --force 时写入；写入前 diff 提示
    if args.force or not os.path.exists(M3_PATH):
        if os.path.exists(M3_PATH):
            # 备份现有版本为 .orig（仅当 .orig 不存在时）
            if not os.path.exists(M3_ORIG):
                shutil.copy2(M3_PATH, M3_ORIG)
                print(f"已备份现有 model3.json → {M3_ORIG}", flush=True)
            # diff 提示
            try:
                with open(M3_PATH, 'r') as f_old, open(M3_ORIG, 'r') as f_orig:
                    old_content = f_old.read()
                    orig_content = f_orig.read()
                if old_content != orig_content:
                    print("⚠ 现有 model3.json 与 .orig 不同，将被覆盖", flush=True)
            except Exception:
                pass
        new_content = json.dumps(MANIFEST, ensure_ascii=False, indent=1)
        with open(M3_PATH, "w") as fp:
            fp.write(new_content)
        print("model3.json 已写入（内置清单）", flush=True)
    else:
        print("model3.json 已存在，跳过写入（使用 --force 强制覆盖）", flush=True)

    print(f"成功 {ok}, 失败 {len(fail)}", flush=True)
    for f, e in fail:
        print("FAIL", f, e, flush=True)
    print("Expressions:", [e["File"] for e in fr.get("Expressions", [])] or "无", flush=True)
    print("Motion组:", list(fr.get("Motions", {}).keys()), flush=True)
    print("DL_DONE", flush=True)

    if fail:
        sys.exit(1)
    return 0

if __name__ == "__main__":
    sys.exit(main())
