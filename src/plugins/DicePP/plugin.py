"""NoneBot entry point for DicePP's registration side effects."""

# Importing this module deliberately registers DicePP's matchers and lifecycle
# hooks with the already-initialized NoneBot runtime.
from plugins.DicePP.adapter import nonebot_adapter as _nonebot_adapter
