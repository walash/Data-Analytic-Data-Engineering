"""ED capacity planning: demand forecasting and resource allocation.

Exposes the primary classes for the Emergency Department capacity-planning
module so they can be imported cleanly, e.g. ``from src.ed_capacity import
EDPipeline``.
"""
from .demand_forecaster import DemandForecaster
from .resource_allocator import ResourceAllocator, CapacityConfig
from .capacity_dashboard import CapacityDashboard
from .capacity_optimizer import CapacityOptimizer, StaffCost
from .ed_pipeline import EDPipeline

__all__ = [
    "DemandForecaster",
    "ResourceAllocator",
    "CapacityConfig",
    "CapacityDashboard",
    "CapacityOptimizer",
    "StaffCost",
    "EDPipeline",
]
