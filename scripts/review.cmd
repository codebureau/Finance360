@echo off
rem Windows entry point: review <PR#> | --stop | --reset  (needs Python 3.10+, git, gh, docker)
python "%~dp0review.py" %*
