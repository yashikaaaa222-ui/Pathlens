"""Importing this package registers every table on Base.metadata."""
from models.infra import InfraFeature
from models.report import Report, ReportConfirmation, ReportDispute, ReportEvent

__all__ = ["InfraFeature", "Report", "ReportConfirmation", "ReportDispute", "ReportEvent"]
