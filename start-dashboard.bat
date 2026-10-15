@echo off
title AiFLOW dashboard
cd /d "%~dp0"
python dashboard\server.py
if errorlevel 1 pause
