"""Shared Bot↔Manager control channel protocol and credential helpers.

This package is intentionally free of import-time side effects so that
Manager, Dashboard and Bot processes can all import the wire protocol and
the control token without pulling in any Bot business modules.  It used to
live under ``plugins.DicePP.module.dashboard_reporter``; importing it from
there forced the whole DicePP module package (and its logging/bootstrap
side effects) into Manager and Dashboard GUI processes.

子模块:
- ``protocol``: 消息信封协议 (dicepp-control-v1)
- ``control_token``: Bot↔Manager 专用控制凭据 (manager/control/control-token)
"""
