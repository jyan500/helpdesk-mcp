# Makes `evals` a package so you can run:  python -m evals.harness   (from server/)
# Running as a MODULE (not `python evals/harness.py`) is what puts server/ on sys.path,
# so `from utils.constants import ...` and `from evals.dataset import ...` resolve.
