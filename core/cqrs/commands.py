from typing import Any, Optional
from core.domain.entities import Block
from core.ports.repositories import IBlockRepository
from infrastructure.repositories.lmdb_unit_of_work import LMDBUnitOfWork
from core.services.record_service import RecordService
from core.services.auth_service import AuthService

class AddRecordCommand:
    def __init__(
        self,
        patient_id: str,
        data: Any,
        is_protected: bool = False,
        protection_password: Optional[str] = None,
        username: str = "system",
    ):
        self.patient_id = patient_id
        self.data = data
        self.is_protected = is_protected
        self.protection_password = protection_password
        self.username = username

class AddCorrectionCommand:
    def __init__(
        self,
        patient_id: str,
        block_index: int,
        corrected_data: Any,
        encryption_password: Optional[str] = None,
        username: str = "system",
        reason: str = "",
    ):
        self.patient_id = patient_id
        self.block_index = block_index
        self.corrected_data = corrected_data
        self.encryption_password = encryption_password
        self.username = username
        self.reason = reason


class CommandHandler:
    def __init__(
        self,
        record_service: RecordService,
        auth_service: AuthService,
        block_repo: IBlockRepository,
    ):
        self.record_service = record_service
        self.auth_service = auth_service
        self.block_repo = block_repo

    def handle_add_record(self, cmd: AddRecordCommand) -> Block:
        project_name = self.record_service._get_project_name(cmd.patient_id)
        with LMDBUnitOfWork(project_name):
            return self.record_service.add_record(
                patient_id=cmd.patient_id,
                data=cmd.data,
                is_protected=cmd.is_protected,
                protection_password=cmd.protection_password,
                username=cmd.username,
            )

    def handle_add_correction(self, cmd: AddCorrectionCommand) -> Block:
        project_name = self.record_service._get_project_name(cmd.patient_id)
        with LMDBUnitOfWork(project_name):
            return self.record_service.add_correction_block(
                patient_id=cmd.patient_id,
                block_index=cmd.block_index,
                corrected_data=cmd.corrected_data,
                encryption_password=cmd.encryption_password,
                username=cmd.username,
                reason=cmd.reason,
            )
