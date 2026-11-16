@echo off
rem Windows entry point: deploy <tag> [--first]  (needs Python 3.10+ and ssh)
python "%~dp0deploy.py" %*
