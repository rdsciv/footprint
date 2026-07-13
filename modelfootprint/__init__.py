"""modelfootprint — estimate the energy, water, and carbon footprint of LLM
inference from real session transcripts or hypothetical usage.

See METHODOLOGY.md for derivations and LIMITATIONS_AND_FAQ.md for what these
estimates can and cannot claim.
"""
__version__ = "0.2.0"

from .engine import (  # noqa: F401
    BOUNDS,
    PLACEHOLDER_LINE,
    PLACEHOLDER_TOKENS,
    compute,
    fmt_sig,
    fmt_tok,
    load_coefficients,
    parse_transcript,
    render,
    tier_for,
)
