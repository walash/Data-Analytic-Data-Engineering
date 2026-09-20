"""Exploratory Data Analysis subpackage.

Responsible for descriptive statistics, statistical hypothesis testing,
publication-quality visualisations, and markdown report generation for the
CVD risk-stratification & ED capacity-planning project.
"""
from .descriptive_stats import DescriptiveAnalyzer
from .visualizations import EDAVisualizer
from .statistical_tests import StatisticalTester
from .eda_report import EDAReport

__all__ = [
    "DescriptiveAnalyzer",
    "EDAVisualizer",
    "StatisticalTester",
    "EDAReport",
]
