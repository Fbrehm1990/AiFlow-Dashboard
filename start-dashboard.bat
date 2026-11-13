@echo off
title Foreman AI dashboard
cd /d "%~dp0"
python dashboard\server.py
if errorlevel 1 pause
