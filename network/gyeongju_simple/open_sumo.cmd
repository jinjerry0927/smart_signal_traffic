@echo off
sumo-gui -c "%~dp0demo.sumocfg"
if errorlevel 1 pause
