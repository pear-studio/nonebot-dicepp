"""DicePP registration side effects, executed in NoneBot's plugin context."""

# Importing the business module is deliberate: its command decorators populate
# DEFAULT_REGISTRY.  Keeping this in the canonical plugin loading chain makes
# command registration explicit instead of relying on an adapter import.
from plugins.DicePP import module as _business_command_modules
from plugins.DicePP.core.command.user_cmd import DEFAULT_REGISTRY as _DEFAULT_REGISTRY

if len(_DEFAULT_REGISTRY) == 0:
    raise RuntimeError(
        "DicePP command registration failed: "
        "plugins.DicePP.module left DEFAULT_REGISTRY empty"
    )

# Importing the adapter deliberately registers DicePP's matchers and lifecycle
# hooks with the already-initialized NoneBot runtime.
from plugins.DicePP.adapter import nonebot_adapter as _nonebot_adapter
