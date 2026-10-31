"""Report local AI runtime and hardware hints without downloading any model."""
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.ai_engine import _configured_local_model


def total_memory_gb():
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.ullTotalPhys / (1024 ** 3), 1)
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / (1024 ** 3), 1)
    except (AttributeError, OSError, ValueError):
        return None


def main():
    print(f"OS: {platform.platform()}")
    print(f"CPU: {platform.processor() or 'not reported by the OS'}")
    memory = total_memory_gb()
    print(f"System RAM: {memory} GB" if memory else "System RAM: unavailable")
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        result = subprocess.run([nvidia, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=5, check=False)
        print(f"NVIDIA GPU: {result.stdout.strip() or 'detected, details unavailable'}")
    else:
        print("NVIDIA GPU: not detected with nvidia-smi (other GPU vendors are not checked)")

    ollama = shutil.which("ollama")
    portable_candidates = (
        ROOT / "data" / "runtime" / "ollama" / "windows" / "ollama.exe",
        ROOT / "data" / "runtime" / "ollama" / "windows" / "ollama" / "ollama.exe",
        ROOT / "data" / "runtime" / "ollama" / "windows" / "bin" / "ollama.exe",
    )
    if not ollama:
        ollama = next((str(path) for path in portable_candidates if path.is_file()), None)
    print(f"Ollama executable: {ollama or 'not found on PATH or workspace-local runtime'}")
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=2) as response:
            models = json.loads(response.read()).get("models", [])
        names = [item.get("name") for item in models if item.get("name")]
        print(f"Ollama API: online; installed models: {', '.join(names) if names else 'none'}")
    except (urllib.error.URLError, TimeoutError, ValueError):
        names = []
        print("Ollama API: unavailable")

    configured = _configured_local_model([{"name": name} for name in names])
    if configured in names:
        print(f"Configured model: {configured} (installed)")
    else:
        print(f"Configured model: {configured} (not installed; this check never pulls models)")
    if memory is not None and memory < 8:
        print("Hint: start with a 0.6B–1.7B quantized model; generation quality and speed will be limited.")
    elif memory is not None and memory < 12:
        print("Hint: start with a 1.7B–4B quantized model and a short context window.")
    elif memory is not None:
        print("Hint: a 4B quantized model is a reasonable first CPU/local candidate; measure speed before using larger models.")
    print("No model download or paid service was started by this check.")


if __name__ == "__main__":
    main()
