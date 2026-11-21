.PHONY: install data ingest serve test eval clean

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

data:        ## generate synthetic corpus, users and SQLite demo data
	eka generate-data

ingest:      ## chunk, embed and index the corpus
	eka ingest

serve:       ## run the API and web UI on http://localhost:8080
	eka serve --port 8080

test:
	pytest

eval:        ## run the evaluation suite and write eval/reports/latest.md
	eka eval

clean:
	rm -rf data .pytest_cache
