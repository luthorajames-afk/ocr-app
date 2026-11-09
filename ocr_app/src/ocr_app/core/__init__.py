"""Couche cœur : modèle de données, protocole worker, pipeline d'OCR."""

from ocr_app.core.data_model import (
    Page, Block, Cell, Table, BlockType, Document,
)
from ocr_app.core.protocol import (
    MessageType, ProgressMessage, PageDoneMessage, ErrorMessage, CommandMessage, parse_line,
)
from ocr_app.core.pipeline import Pipeline, PipelineStage, PipelineConfig
from ocr_app.core.task_queue import Task, TaskQueue, TaskStatus

__all__ = [
    "Page",
    "Block",
    "Cell",
    "Table",
    "BlockType",
    "Document",
    "MessageType",
    "ProgressMessage",
    "PageDoneMessage",
    "ErrorMessage",
    "CommandMessage",
    "parse_line",
    "Pipeline",
    "PipelineStage",
    "PipelineConfig",
    "Task",
    "TaskQueue",
    "TaskStatus",
]