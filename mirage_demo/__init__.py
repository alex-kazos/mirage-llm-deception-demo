"""MIRAGE live demo — a runnable, model-pluggable version of the thesis PoC.

The deception mechanism (ingress detector, one-way state machine, shadow state
store, consistency ledger, honeytoken registry) is a faithful port of
`mirage/system.py` from the proof-of-concept. On top of it sits a live
generation layer: every reply is written by a real model, through two separate
model instances — a privileged one that sees the real world and a quarantined
one that sees only the shadow world (the inverted dual-LLM pattern, §4.3).

Same mechanism drives the interactive web demo and the batch results runner.
"""
__all__ = ["mechanism", "models", "live", "autopilot"]
__version__ = "1.0.0"
