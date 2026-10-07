"""ARGUS ML service package (services/ml).

FastAPI prediction service. The quant library (packages/quant, built by a
parallel track) is imported defensively via app.quant_compat — never
duplicated here.
"""

__version__ = "0.1.0"
