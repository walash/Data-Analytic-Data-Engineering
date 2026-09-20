# =====================================================================
# CVD Risk Stratification & ED Capacity Planning — convenience targets
# =====================================================================
PYTHON ?= python

.DEFAULT_GOAL := help
.PHONY: help install data eda train forecast all test clean

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install Python dependencies
	$(PYTHON) -m pip install -r requirements.txt

data:  ## Run the data-engineering pipeline (generate + ETL + features)
	$(PYTHON) main.py --pipeline data_engineering

eda:  ## Run exploratory data analysis (stats + figures)
	$(PYTHON) main.py --pipeline eda

train:  ## Train and evaluate the ML models
	$(PYTHON) main.py --pipeline modeling

forecast:  ## Run the ED capacity-planning pipeline
	$(PYTHON) main.py --pipeline ed_capacity

all:  ## Run the full end-to-end pipeline
	$(PYTHON) main.py --pipeline full

test:  ## Run the unit-test suite
	$(PYTHON) -m pytest -q

clean:  ## Remove caches and generated logs
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -f logs/pipeline.log
