"""CI 产物落盘：截图目录 + 结果 JSON 的统一入口。

约定（云端 e2e 矩阵 job 与本地排查共用）：
- 产物根目录：环境变量 `XIAOMAN_ARTIFACTS_DIR`，缺省 `<repo>/artifacts`
- 截图目录：环境变量 `XIAOMAN_SHOTS_DIR`，缺省 `<产物根>/shots`
- 结果 JSON：`<产物根>/<脚本名>.json`，含每个断言的名称/是否通过/明细

每个 Playwright 脚本结束时调用 `write_result(脚本名, results)`，
workflow 侧把 `<产物根>` 整体上传为 artifact（截图 + 关键 JSON 一起带走）。
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def artifacts_dir():
    """产物根目录（自动创建）"""
    raw = os.environ.get("XIAOMAN_ARTIFACTS_DIR")
    path = Path(raw) if raw else ROOT / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def shots_dir(sub=""):
    """截图目录；sub 用于同一 artifact 内按套件分文件夹"""
    raw = os.environ.get("XIAOMAN_SHOTS_DIR")
    path = Path(raw) if raw else artifacts_dir() / "shots"
    if sub:
        path = path / sub
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_result(script, checks, extra=None):
    """写结果 JSON；checks 支持 (name, ok) 或 (name, ok, detail)。返回是否全过。"""
    norm = []
    for item in checks:
        name, ok = item[0], bool(item[1])
        detail = item[2] if len(item) > 2 else ""
        norm.append({"name": name, "ok": ok, "detail": str(detail)})
    ok = all(c["ok"] for c in norm)
    payload = {
        "script": script,
        "ok": ok,
        "passed": sum(1 for c in norm if c["ok"]),
        "total": len(norm),
        "checks": norm,
    }
    if extra:
        payload["extra"] = extra
    path = artifacts_dir() / f"{script}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok


def print_failures(checks):
    """逐条打印失败断言（带明细），供 CI 日志里直接定位"""
    failed = False
    for item in checks:
        name, ok = item[0], bool(item[1])
        detail = item[2] if len(item) > 2 else ""
        if not ok:
            failed = True
            print(f"FAIL {name}" + (f" · {detail}" if detail else ""), flush=True)
    return failed
