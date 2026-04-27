@echo off
echo Starting H&M Invoice Extractor...
call venv\Scripts\activate.bat
"%USERPROFILE%\bin\doppler.exe" run -- python run.py
