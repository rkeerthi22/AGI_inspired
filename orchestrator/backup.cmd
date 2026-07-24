@echo off
rem Nightly DB backup wrapper for Windows Task Scheduler (see missions/_M1_INDEX.md).
rem Fixes F16 (docs/HARDENING.md) — the ledger had no second copy anywhere.
set PYTHONIOENCODING=utf-8
cd /d S:\AGI_like
python orchestrator\backup.py >> runs\backup_last.log 2>&1
