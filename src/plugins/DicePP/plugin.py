"""Canonical NoneBot launcher adapter for DicePP's PYZ registration module."""

# NoneBot 2.5 loads managed plugins with SourceFileLoader in frozen builds.
# PyInstaller therefore ships this tiny adapter as the one intentional Python
# data file; all registration implementation remains in the PYZ archive.
from plugins.DicePP import _plugin_registration as _registration
