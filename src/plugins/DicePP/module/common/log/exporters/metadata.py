from __future__ import annotations

from ..types import LogExportView


_EXPORT_VIEW_LABELS = {
    LogExportView.CURATED: "跑团正文",
    LogExportView.COMPLETE: "全部记录",
}


def export_view_label(view: LogExportView) -> str:
    return _EXPORT_VIEW_LABELS[view]
