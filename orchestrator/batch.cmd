@echo off
rem M1 batch engine wrapper for Windows Task Scheduler (see missions/_M1_INDEX.md).
rem utf-8 required: cp1252 crashes on emoji/unicode in model output (machine rule).
set PYTHONIOENCODING=utf-8
cd /d S:\AGI_like
python orchestrator\batch_runner.py %* >> runs\schtask_last.log 2>&1
