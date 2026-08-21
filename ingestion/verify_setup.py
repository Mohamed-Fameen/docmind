"""
Phase 0 sanity check.

Run this after installing dependencies to confirm your machine is ready for
local embeddings/reranking (GPU) and local LLM inference (Ollama).

Usage:
    uv run python ingestion/verify_setup.py
"""

import shutil
import subprocess
import sys


def check_python():
    print(f"Python version:      {sys.version.split()[0]}")


def check_torch_cuda():
    try:
        import torch
    except ImportError:
        print("torch:                NOT INSTALLED (run `uv sync` first)")
        return

    available = torch.cuda.is_available()
    print(f"CUDA available:       {available}")
    if available:
        print(f"GPU name:             {torch.cuda.get_device_name(0)}")
        print(f"CUDA version (torch): {torch.version.cuda}")
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        print(f"GPU VRAM:             {vram_gb:.1f} GB")
    else:
        print("  -> Embeddings/reranking will fall back to CPU (slower but functional).")
        print("     If you have an NVIDIA GPU and this shows False, check your CUDA")
        print("     toolkit + torch install (see https://pytorch.org/get-started/locally/).")


def check_docker():
    if shutil.which("docker") is None:
        print("Docker:               NOT FOUND on PATH")
        return
    try:
        out = subprocess.run(["docker", "--version"], capture_output=True, text=True, check=True)
        print(f"Docker:               {out.stdout.strip()}")
    except Exception as e:
        print(f"Docker:               found but errored ({e})")


def check_ollama():
    if shutil.which("ollama") is None:
        print("Ollama:               NOT FOUND on PATH (install from https://ollama.com/download)")
        return
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, check=True)
        print("Ollama models installed:")
        print("  " + out.stdout.strip().replace("\n", "\n  "))
    except Exception as e:
        print(f"Ollama:               found but errored ({e})")


if __name__ == "__main__":
    print("=" * 60)
    print("DocMind — Phase 0 environment check")
    print("=" * 60)
    check_python()
    check_torch_cuda()
    check_docker()
    check_ollama()
    print("=" * 60)
    print("Copy the relevant lines into docs/00-setup.md under 'Environment record'.")
