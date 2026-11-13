# Runbook: from zero to a live run (macOS)

Run one command at a time; each step says what you should see.

```bash
# 1. Get the code
cd ~/Desktop && unzip aegis.zip && cd aegis          # or: git clone https://github.com/umeshlinga/aegis
python3 --version                                    # need 3.11+  (brew install python@3.12 if not)

# 2. Environment
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,rag,mcp]"
python -m playwright install chromium                # downloads the browser (~150 MB)

# 3. Keys: edit .env in VS Code, never paste keys into chat or commit them
cp .env.example .env
code .env                                            # set ANTHROPIC_API_KEY, NCBI_EMAIL (and NCBI_API_KEY if you have one)

# 4. Check everything
aegis doctor
#   expect: ensembl OK, ncbi OK, gnomad OK, and "llm fast/balanced/strong OK" with a tiny cost each

# 5. Tests and offline eval (no money spent)
pytest -q                                            # expect: 52 passed
aegis eval --mode offline --n 60                     # opens nothing; report at evals/results/latest/report.html
open evals/results/latest/report.html

# 6. First live variant (example coordinate; swap in variants you know well from your own work; costs cents)
aegis variant 17-43045712-T-C --phenotype "hereditary breast and ovarian cancer"
#   if anything fails: aegis show <run_id> --json, then aegis serve and open the run in the explorer

# 7. Explorer
aegis serve                                          # http://127.0.0.1:8080

# 8. Company research with approval
aegis company https://<a public company site> --outreach
aegis approvals
aegis approve <id>                                   # or --reject

# 9. Crash and resume, for real
aegis variant 13-32340300-GT-G &  sleep 6; kill -9 %1
aegis runs                                           # the run shows "running"
aegis resume <run_id>                                # completed steps are not redone

# 10. Live benchmark (downloads ClinVar variant_summary, a few hundred MB; LLM cost: a few dollars for 40 cases)
make bench
aegis eval --mode record --cases evals/data/clinvar_benchmark.jsonl --n 40 --profiles none,harsh --calibrate --out evals/results/live
```

**Claude Desktop (MCP).** Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{"mcpServers": {"aegis": {"command": "/ABS/PATH/aegis/.venv/bin/aegis", "args": ["mcp"],
  "env": {"ANTHROPIC_API_KEY": "sk-...", "AEGIS_DB": "/ABS/PATH/aegis/data/aegis.db"}}}}
```
Restart Claude Desktop; ask it to "curate variant 17-43045712-T-C with Aegis".

**Docker.** `docker compose up --build` → explorer :8080, Jaeger :16686, Prometheus :9090.

**Push to GitHub.**
```bash
git remote add origin https://github.com/umeshlinga/aegis.git
git push -u origin main
```
