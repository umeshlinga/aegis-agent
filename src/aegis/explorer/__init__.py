"""Trace explorer: a read-only local web UI over the JSONL trace store.

The explorer never writes to a trace file. It reads events, groups them
by run, and renders each run as an ordered timeline so a person can see
exactly what the agent did: the plan, every model call, every tool call
with its permission scope, every verification, and every recovery or
permission-gate event, highlighted.
"""
