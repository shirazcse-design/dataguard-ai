"""Interview demo dashboard: a local, read-mostly presentation layer over the existing service.

It adds no classification, routing, agent or evaluation logic of its own. Every number it shows is
read from a committed artifact (`app/demo/metrics.py`) and every classification or agent run goes
through the existing interfaces (`ClassificationService`, `run_batch`). See
`docs/uc4/interview-demo.md`.
"""
