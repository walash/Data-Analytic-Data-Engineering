.PHONY: help install data eda model forecast all test clean lint

PY ?= python

help:
	@echo "Targets:"
	@echo "  install   Install Python dependencies"
	@echo "  data      Run the data-engineering pipeline (generate -> ETL -> features -> catalog)"
	@echo "  eda       Run exploratory data analysis"
	@echo "  model     Run predictive-modelling pipeline"
	@echo "  forecast  Run outpatient load-forecasting pipeline"
	@echo "  all       Run every pipeline stage end to end"
	@echo "  test      Run the pytest suite"
	@echo "  clean     Remove generated data, models and outputs"

install:
	$(PY) -m pip install -r requirements.txt

data:
	$(PY) main.py --pipeline data_engineering

eda:
	$(PY) main.py --pipeline eda

model:
	$(PY) main.py --pipeline modeling

forecast:
	$(PY) main.py --pipeline forecasting

all:
	$(PY) main.py --pipeline full

test:
	$(PY) -m pytest tests/ -q

clean:
	rm -rf data/raw/* data/processed/* data/feature_store/* models/*.pkl \
	       outputs/figures/*.png outputs/reports/*.csv outputs/reports/*.md \
	       outputs/reports/*.html outputs/reports/*.json
	@echo "Cleaned generated artefacts."
