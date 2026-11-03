"""运行时密钥读取：环境变量优先，其次 Windows 凭据管理器。

约束（与 ANALYSIS §13.6 一致）：
- 绝不硬编码、绝不写日志、绝不落盘、绝不下发到前端；
- 凭据值只在进程内存里存在，`repr()` 也被屏蔽，避免误打印；
- 非 Windows 或读取失败时静默返回空串，由调用方降级到 mock。

凭据目标名可通过 `AI_KEY_CREDENTIAL_TARGET` 指定；默认指向 Freebuff BYOK
写入的通用凭据（可在 `cmdkey /list` 中看到同名 Target）。
"""
import json
import logging
import os
import subprocess

logger = logging.getLogger("xiaoman-cred")

DEFAULT_TARGET = "com.freebuff.byok.v1/connection:360e4a4c-f084-4d80-adf1-dac59a44b450:1"

_PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
$src = @'
using System;
using System.Runtime.InteropServices;
public class CredReader {
    [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    private static extern bool CredRead(string target, int type, int flags, out IntPtr credential);
    [DllImport("advapi32.dll", SetLastError=true)]
    private static extern void CredFree(IntPtr cred);
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    private struct CREDENTIAL {
        public int Flags;
        public int Type;
        public string TargetName;
        public string Comment;
        public long LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }
    public static string Read(string target) {
        IntPtr p;
        if (!CredRead(target, 1, 0, out p)) { return ""; }
        try {
            CREDENTIAL c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
            byte[] b = new byte[c.CredentialBlobSize];
            if (c.CredentialBlobSize > 0) { Marshal.Copy(c.CredentialBlob, b, 0, c.CredentialBlobSize); }
            return Convert.ToBase64String(b);
        } finally { CredFree(p); }
    }
}
'@
Add-Type -TypeDefinition $src -Language CSharp
$b64 = [CredReader]::Read($env:CRED_TARGET)
if ($b64) { Write-Output ("B64:" + $b64) }
"""


class _Secret(str):
    """字符串子类：repr() 自动脱敏，避免异常回溯/lprint 泄露密钥。

    注意：str() 仍返回真实值，因为要拼进 `Authorization: Bearer …`。
    """

    def __repr__(self):  # pragma: no cover - 仅用于调试
        return "<redacted>"


def _decode_blob(b64):
    import base64

    raw = base64.b64decode(b64)
    for enc in ("utf-16-le", "utf-8"):
        try:
            text = raw.decode(enc).strip("\x00").strip()
        except UnicodeDecodeError:
            continue
        if text:
            return text
    return ""


def _extract_key(payload):
    """从凭据内容里取出 api key：支持纯字符串或 JSON。"""
    text = payload.strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            return ""
        for key in ("apiKey", "api_key", "key", "token", "accessToken", "secret"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""
    return text


def read_credential(target=None):
    """从 Windows 凭据管理器读取通用凭据原文；失败返回空串。"""
    if os.name != "nt":
        return ""
    name = (target or os.environ.get("AI_KEY_CREDENTIAL_TARGET") or DEFAULT_TARGET).strip()
    if not name:
        return ""
    env = dict(os.environ)
    env["CRED_TARGET"] = name
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_TEMPLATE],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("凭据读取失败：%s", type(exc).__name__)
        return ""
    out = (proc.stdout or "").strip()
    if not out.startswith("B64:"):
        return ""
    return _decode_blob(out[4:])


def api_key():
    """返回可用的上游 API key；环境变量优先，其次凭据管理器。"""
    env_key = (os.environ.get("AI_API_KEY") or "").strip()
    if env_key:
        return _Secret(env_key)
    raw = read_credential()
    if not raw:
        return ""
    key = _extract_key(raw)
    if not key:
        return ""
    logger.info("已从 Windows 凭据管理器读取 AI key（脱敏，长度 %d）", len(key))
    return _Secret(key)


if __name__ == "__main__":  # 自检：只输出状态，绝不输出密钥内容
    key = api_key()
    print(json.dumps({"has_key": bool(key), "length": len(key)}, ensure_ascii=False))
