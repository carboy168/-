from __future__ import annotations
import logging
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str)

class FunctionWorker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result)
        except Exception as e:
            logging.exception("Background task failed")
            from provider import ProviderError
            message=str(e) if isinstance(e,(ProviderError,ValueError)) else "操作失败，请检查配置、网络和脱敏日志。"
            self.signals.error.emit(message)
